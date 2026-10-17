"""
Run the GenoSQL benchmark against one pipeline configuration.

    python -m eval.run_eval                                  # full system, default model, DEV split
    python -m eval.run_eval --split test                     # held-out test split: report, never tune
    python -m eval.run_eval --model qwen2.5-coder:3b
    python -m eval.run_eval --no-rag --no-semantic --repairs 0 --tag baseline
    python -m eval.run_eval --only population,sv --limit 5   # quick subset
    python -m eval.run_eval --check-gold                     # validate the benchmark itself

Each run writes eval/results/<tag>.jsonl (one line per item) and prints a summary.
Evaluation never writes to RAG memory; items are excluded from retrieving themselves.
"""
import argparse
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

from app.database import run_readonly
from app.nl_query.text_to_sql import PipelineConfig, answer_question
from eval.compare import results_match

HERE = Path(__file__).resolve().parent
BENCHMARK = HERE / "benchmark.jsonl"
RESULTS = HERE / "results"
EVAL_MAX_ROWS = 5000


def load_items(path=BENCHMARK, only=None, limit=None, split="all"):
    items = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if split != "all":
        items = [i for i in items if i.get("split") == split]
    if only:
        items = [i for i in items if i["category"] in only]
    return items[:limit] if limit else items


def check_gold(items):
    """Every gold query must run, be non-truncated, and (for listings) be small."""
    bad = 0
    for item in items:
        if item.get("gold_sql") is None:
            print(f"  {item['id']:<6} unanswerable")
            continue
        try:
            res = run_readonly(item["gold_sql"], max_rows=EVAL_MAX_ROWS)
            flag = "TRUNCATED" if res["truncated"] else ""
            preview = res["rows"][0] if res["rows"] else "(no rows)"
            print(f"  {item['id']:<6} {len(res['rows']):>5} rows  {flag} {preview}")
            bad += bool(flag) or not res["rows"]
        except Exception as e:
            bad += 1
            print(f"  {item['id']:<6} ERROR {str(e).splitlines()[0]}")
    print(f"\n{bad} problem(s)")


def score(item, out):
    answerable = item.get("gold_sql") is not None
    rec = {"id": item["id"], "split": item.get("split"), "category": item["category"], "difficulty": item["difficulty"],
           "question": item["question"], "answerable": answerable,
           "status": out["status"], "sql": out["sql"], "error": out["error"],
           "attempts": out["attempts"], "examples_used": out["examples_used"],
           "latency_ms": out["latency_ms"], "llm_ms": out["llm_ms"],
           "prompt_tokens": out["prompt_tokens"], "output_tokens": out["output_tokens"]}

    if not answerable:
        rec["strict"] = rec["relaxed"] = out["status"] == "abstained"
        return rec

    if out["status"] != "ok":
        rec["strict"] = rec["relaxed"] = False
        return rec

    gold = run_readonly(item["gold_sql"], max_rows=EVAL_MAX_ROWS)
    rec.update(results_match(gold["rows"], out["rows"], ordered=item.get("ordered", False)))
    rec["truncated"] = out["truncated"]
    return rec


def summarise(records, cfg, tag):
    n = len(records)
    answerable = [r for r in records if r["answerable"]]
    unanswerable = [r for r in records if not r["answerable"]]
    pct = lambda xs, key: 100 * sum(r[key] for r in xs) / len(xs) if xs else float("nan")

    print(f"\n=== {tag} === model={cfg.model} rag={cfg.use_rag} semantic={cfg.semantic_schema} "
          f"repairs={cfg.max_repairs} abstain={cfg.allow_abstain} routing={cfg.routing_rules} "
          f"value_check={cfg.value_check} verify_abstain={cfg.verify_abstain}")
    print(f"Execution accuracy (answerable, n={len(answerable)}): "
          f"strict {pct(answerable, 'strict'):.1f}%  relaxed {pct(answerable, 'relaxed'):.1f}%")
    if unanswerable:
        print(f"Correct abstention (unanswerable, n={len(unanswerable)}): {pct(unanswerable, 'strict'):.1f}%")
    false_abstain = sum(r["status"] == "abstained" for r in answerable)
    print(f"False abstentions: {false_abstain}   Invalid/failed SQL: "
          f"{sum(r['status'] in ('invalid', 'db_error') for r in answerable)}   "
          f"Repaired (>1 attempt & correct): {sum(r['attempts'] > 1 and r['relaxed'] for r in answerable)}")

    by_cat = defaultdict(list)
    for r in records:
        by_cat[r["category"]].append(r)
    print("\nBy category (relaxed):")
    for cat, rs in sorted(by_cat.items()):
        print(f"  {cat:<14} {sum(r['relaxed'] for r in rs):>3}/{len(rs):<3}")

    lat = [r["latency_ms"] for r in records]
    toks = [r["prompt_tokens"] + r["output_tokens"] for r in records]
    print(f"\nLatency ms: median {statistics.median(lat):,.0f}  p90 {sorted(lat)[int(0.9 * (n - 1))]:,}"
          f"   Tokens/question: mean {statistics.mean(toks):,.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model")
    p.add_argument("--backend")
    p.add_argument("--no-rag", action="store_true")
    p.add_argument("--no-semantic", action="store_true")
    p.add_argument("--self-abstain", action="store_true", help="offer the model a CANNOT_ANSWER reply")
    p.add_argument("--no-routing", action="store_true", help="drop answer-shape/routing rules")
    p.add_argument("--no-value-check", action="store_true", help="guard skips category-value check")
    p.add_argument("--no-verify-abstain", action="store_true", help="accept the first CANNOT_ANSWER")
    p.add_argument("--no-grounded-abstain", action="store_true",
                   help="don't decline when every attempt needs non-existent tables/columns")
    p.add_argument("--repairs", type=int)
    p.add_argument("--split", choices=["dev", "test", "all"], default="dev",
                   help="dev for iterating (default); test only for reporting")
    p.add_argument("--phrasing", choices=["standard", "lay"], default="standard",
                   help="lay: ask the non-expert paraphrase (items that have one)")
    p.add_argument("--source", choices=["all", "hand", "generated"], default="all")
    p.add_argument("--only", help="comma-separated categories")
    p.add_argument("--limit", type=int)
    p.add_argument("--tag")
    p.add_argument("--check-gold", action="store_true")
    p.add_argument("--resume", action="store_true", help="continue an interrupted run with the same tag")
    args = p.parse_args()

    if args.check_gold:
        return check_gold(load_items(split="all"))
    items = load_items(only=set(args.only.split(",")) if args.only else None, limit=args.limit,
                       split=args.split)
    if args.source != "all":
        items = [i for i in items if i.get("source", "hand") == args.source]
    if args.phrasing == "lay":
        items = [{**i, "question": i["question_lay"]} for i in items if i.get("question_lay")]
    if args.split == "test":
        print("*** HELD-OUT TEST SPLIT: report these numbers; do not change prompts, seeds or "
              "schema notes in response to individual test items. ***")

    cfg = PipelineConfig(max_rows=EVAL_MAX_ROWS)
    if args.model: cfg.model = args.model
    if args.backend: cfg.backend = args.backend
    if args.repairs is not None: cfg.max_repairs = args.repairs
    cfg.use_rag = not args.no_rag
    cfg.semantic_schema = not args.no_semantic
    cfg.allow_abstain = args.self_abstain
    cfg.routing_rules = not args.no_routing
    cfg.value_check = not args.no_value_check
    cfg.verify_abstain = not args.no_verify_abstain
    cfg.grounded_abstain = not args.no_grounded_abstain

    # Suffixes mark departures from the default (full) configuration.
    tag = args.tag or (f"{cfg.model.replace(':', '_').replace('/', '_')}"
                       f"{'' if cfg.use_rag else '_norag'}{'' if cfg.semantic_schema else '_nosem'}"
                       f"_r{cfg.max_repairs}"
                       f"{'' if cfg.routing_rules else '_norout'}{'' if cfg.value_check else '_novc'}"
                       f"{'_selfabs' if cfg.allow_abstain else ''}{'' if cfg.verify_abstain else '_novabs'}"
                       f"{'' if cfg.grounded_abstain else '_nogabs'}"
                       f"{'' if args.phrasing == 'standard' else '_' + args.phrasing}"
                       f"{'' if args.source == 'all' else '_' + args.source}"
                       f"{'' if args.split == 'all' else '_' + args.split}")
    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{tag}.jsonl"

    records = []
    if args.resume and out_path.exists():
        # Keep records already written by an interrupted run; os.fsync per line means
        # at most the item in flight is lost.
        valid_ids = {i["id"] for i in items}
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break  # partial last line from a crash
            if rec["id"] in valid_ids:
                records.append(rec)
        print(f"Resuming: {len(records)}/{len(items)} items already done")
    done = {r["id"] for r in records}
    out_path.write_text("".join(json.dumps(r, default=str) + "\n" for r in records), encoding="utf-8")

    started = time.time()
    with out_path.open("a", encoding="utf-8") as fh:
        for i, item in enumerate(items, 1):
            if item["id"] in done:
                continue
            out = answer_question(item["question"], cfg, exclude_question=item["question"])
            rec = score(item, out)
            records.append(rec)
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            mark = "ok" if rec["relaxed"] else "--"
            print(f"[{i}/{len(items)}] {mark} {item['id']:<6} {rec['status']:<9} "
                  f"{rec['latency_ms'] / 1000:5.1f}s  {item['question'][:70]}")

    summarise(records, cfg, tag)
    print(f"\nWall time {time.time() - started:,.0f}s -> {out_path}")


if __name__ == "__main__":
    main()
