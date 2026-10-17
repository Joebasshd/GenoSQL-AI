"""
Memory-poisoning experiment: does storing unverified answers as RAG examples
propagate errors?

    python -m eval.memory_sim --n 60 --model qwen2.5-coder:3b

Phase 1: N benchmark questions (expert phrasing) are answered with seed-only memory.
Phase 2: the same N questions are asked again in their lay paraphrase, under
four memory policies built from phase 1:
  none       seeds only
  auto       + every phase-1 answer that executed (unverified "self-learning")
  verified   + phase-1 answers that were correct (user clicked ✓)
  corrected  + gold SQL for every phase-1 question (user supplied the fix)
Memory is held in-process; the real sql_examples table is not touched.
"""
import argparse
import json
import os
import random
from decimal import Decimal
from pathlib import Path

import numpy as np

from app.nl_query.text_to_sql import PipelineConfig, answer_question
from app.rag.embeddings import embed_text
from eval.compare import results_match
from eval.run_eval import EVAL_MAX_ROWS, load_items, score
from scripts.seed_examples import SEEDS

RESULTS = Path(__file__).resolve().parent / "results"
POLICIES = ["none", "auto", "verified", "corrected"]


class Memory:
    def __init__(self, pairs: list[tuple[str, str]]):
        self.questions = [q for q, _ in pairs]
        self.sqls = [s for _, s in pairs]
        self.vectors = np.array([embed_text(q) for q in self.questions])

    def __call__(self, question: str, k: int = 3, exclude_question: str | None = None):
        sims = self.vectors @ np.array(embed_text(question))
        order = [i for i in np.argsort(-sims) if self.questions[i] != exclude_question][:k]
        return [(self.questions[i], self.sqls[i], float(1 - sims[i])) for i in order]


def _plain(rows: list) -> list:
    return [[float(v) if isinstance(v, Decimal) else v if v is None or isinstance(v, (int, float, str, bool))
             else str(v) for v in row] for row in rows]


def run(items, cfg, memory, checkpoint: Path) -> list[dict]:
    """Answer items in order; each record is fsynced to `checkpoint`, so a crash loses at most one item."""
    cfg.retriever = memory
    done = {}
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break
            done[rec["id"]] = rec
    checkpoint.write_text("".join(json.dumps(r) + "\n" for r in done.values()), encoding="utf-8")
    if done:
        print(f"  resuming {checkpoint.name}: {len(done)}/{len(items)} done")
    with checkpoint.open("a", encoding="utf-8") as fh:
        for i, item in enumerate(items, 1):
            if item["id"] in done:
                continue
            out = answer_question(item["question"], cfg, exclude_question=item["question"])
            rec = {**score(item, out), "rows": _plain(out["rows"])}
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            done[item["id"]] = rec
            print(f"  [{i}/{len(items)}] {'ok' if rec['relaxed'] else '--'} {item['id']:<6} {item['question'][:70]}")
    return [done[i["id"]] for i in items]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=60)
    p.add_argument("--model", default="qwen2.5-coder:3b")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    pool = [i for i in load_items(split="all") if i.get("gold_sql") and i.get("question_lay")]
    random.Random(args.seed).shuffle(pool)
    items = sorted(pool[:args.n], key=lambda i: i["id"])
    cfg = PipelineConfig(model=args.model, max_rows=EVAL_MAX_ROWS)
    work = RESULTS / "memory_sim_work" / f"{args.model.replace(':', '_')}_n{args.n}_seed{args.seed}"
    work.mkdir(parents=True, exist_ok=True)

    print(f"Phase 1: {len(items)} expert-phrased questions, seed-only memory")
    phase1 = run(items, cfg, Memory(SEEDS), work / "phase1.jsonl")
    by_id = {r["id"]: r for r in phase1}

    stored = {
        "none": [],
        "auto": [(i["question"], by_id[i["id"]]["sql"]) for i in items if by_id[i["id"]]["status"] == "ok"],
        "verified": [(i["question"], by_id[i["id"]]["sql"]) for i in items if by_id[i["id"]]["relaxed"]],
        "corrected": [(i["question"], i["gold_sql"]) for i in items],
    }
    lay_items = [{**i, "question": i["question_lay"]} for i in items]

    phase2 = {}
    for policy in POLICIES:
        print(f"\nPhase 2 [{policy}]: {len(stored[policy])} stored examples + {len(SEEDS)} seeds")
        phase2[policy] = run(lay_items, cfg, Memory(list(SEEDS) + stored[policy]), work / f"phase2_{policy}.jsonl")

    wrong_executed = [i["id"] for i in items if by_id[i["id"]]["status"] == "ok" and not by_id[i["id"]]["relaxed"]]
    summary = {"model": args.model, "n": len(items), "seed": args.seed,
               "phase1_accuracy": sum(r["relaxed"] for r in phase1) / len(items),
               "phase1_wrong_but_executed": len(wrong_executed), "policies": {}}
    print(f"\nPhase 1 accuracy {summary['phase1_accuracy']:.1%}; "
          f"{len(wrong_executed)} answers were wrong but executed (these poison 'auto' memory)")
    for policy in POLICIES:
        recs = {r["id"]: r for r in phase2[policy]}
        acc = sum(r["relaxed"] for r in recs.values()) / len(items)
        # error propagation: the lay question reproduces the exact wrong phase-1 result
        copied = sum(
            1 for i in wrong_executed
            if not recs[i]["relaxed"] and recs[i]["rows"] and by_id[i]["rows"]
            and results_match(by_id[i]["rows"], recs[i]["rows"])["relaxed"])
        summary["policies"][policy] = {"accuracy": acc, "copied_errors": copied,
                                       "stored": len(stored[policy])}
        print(f"  {policy:<10} lay accuracy {acc:.1%}   copied phase-1 errors {copied}/{len(wrong_executed)}")

    from eval.summarize import mcnemar
    for a, b in [("none", "auto"), ("none", "verified"), ("auto", "verified"), ("verified", "corrected")]:
        wa, wb, pval = mcnemar(phase2[a], phase2[b])
        summary.setdefault("mcnemar", {})[f"{a}_vs_{b}"] = {"a_only": wa, "b_only": wb, "p": pval}
        print(f"  McNemar {a} vs {b}: {wa}/{wb}  p={pval:.3f}")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"memory_sim_{args.model.replace(':', '_')}_n{len(items)}.json"
    strip = lambda rs: [{k: v for k, v in r.items() if k != "rows"} for r in rs]
    out.write_text(json.dumps({"summary": summary, "phase1": strip(phase1),
                               "phase2": {k: strip(v) for k, v in phase2.items()}}, indent=1, default=str),
                   encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
