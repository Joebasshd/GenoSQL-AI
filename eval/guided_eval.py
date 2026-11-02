"""
Measure the guided-answers changes on fresh questions about an uploaded genome.

    python -m eval.guided_eval --dataset-id 8 --build     # Gemini writes and agrees the question set
    python -m eval.guided_eval --dataset-id 8             # score baseline vs guided, resumable

    python -m eval.guided_eval --dataset-id 8 --questions eval/guided_questions_r2.json --build
    python -m eval.guided_eval --dataset-id 8 --questions eval/guided_questions_r2.json --label r2_after
                                                          # one arm of the current code, to compare commits

The author writes questions with gold SQL; a different reviewer writes its own SQL from
the question alone. Only questions whose two queries return the same result are kept.
Both arms use in-process example memory, so stored examples aren't touched.
"""
import argparse
import json
import os
from pathlib import Path

from app import datasets
from app.database import run_readonly
from app.nl_query.sql_guard import clean_sql
from app.nl_query.text_to_sql import PipelineConfig, answer_question
from eval.compare import results_match
from eval.memory_sim import Memory
from eval.summarize import mcnemar
from scripts.generate_benchmark import gemini

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "guided_questions.json"
RESULTS = HERE / "results"
MAX_ROWS = 200

AUTHOR = """You are writing a test set for a natural-language interface to one person's genome.
Database documentation:

{schema}

Write {n} varied, unambiguous questions with a PostgreSQL query answering each, split evenly across:
1. the genotype or zygosity at a specific variant
2. how many heterozygous or homozygous variants this person has in a gene or region
3. how many of this person's variants are in ClinVar, by class, gene or condition
4. questions needing a numeric field inside the info column
Use rsIDs, genes and values that really occur: {facts}.
Answers must be small: a count, an aggregate or at most 20 rows, and never empty.
Return JSON: [{{"question": str, "gold_sql": str}}, ...]"""

REVIEWER = """Database documentation:

{schema}

Write one PostgreSQL SELECT query answering each question below, selecting only the columns that answer it.

{questions}

Return JSON: [{{"key": int, "sql": str}}, ...]"""


def run(sql: str, view):
    try:
        return run_readonly(clean_sql(sql), max_rows=31, timeout_ms=30_000, search_path=view.search_path)["rows"]
    except Exception:
        return None


def agree(a, b) -> bool:
    return results_match(a, b)["relaxed"] or results_match(b, a)["relaxed"]


def build(view, path: Path):
    facts = run_readonly(
        "SELECT c.rsid, c.gene, c.significance_class FROM variants v JOIN clinvar c ON c.chrom = v.chrom AND "
        "c.pos = v.pos AND c.ref = v.ref AND c.alt = v.alt WHERE c.rsid IS NOT NULL ORDER BY md5(c.rsid) LIMIT 25",
        max_rows=25, search_path=view.search_path)["rows"]
    keys = run_readonly("SELECT DISTINCT jsonb_object_keys(info) FROM variants", max_rows=40,
                        search_path=view.search_path)["rows"]
    genes = run_readonly("SELECT g.gene_name, COUNT(*) FROM variant_genes vg JOIN genes g ON g.gene_id = vg.gene_id "
                         "GROUP BY 1 ORDER BY 2 DESC LIMIT 15", max_rows=15, search_path=view.search_path)["rows"]
    prompt = AUTHOR.format(schema=view.prompt(True, True), n=36,
                           facts=f"ClinVar variants {facts}; genes with variant counts {genes}; "
                                 f"info keys {[k[0] for k in keys]}")
    proposals, author = gemini("author", prompt)
    usable = []
    for item in proposals:
        rows = run(item["gold_sql"], view)
        if rows and len(rows) <= 30 and not (len(rows) == 1 and all(v in (None, 0) for v in rows[0])):
            usable.append({**item, "rows": rows})
    listing = "\n".join(f"{i}: {q['question']}" for i, q in enumerate(usable))
    reviews, reviewer = gemini("reviewer", REVIEWER.format(schema=view.prompt(True, True), questions=listing))
    by_key = {int(r["key"]): r.get("sql") for r in reviews}
    kept = []
    for i, item in enumerate(usable):
        rows = run(by_key.get(i) or "", view)
        if rows is not None and agree(item["rows"], rows):
            kept.append({"question": item["question"], "gold_sql": clean_sql(item["gold_sql"])})
    print(f"{author} / {reviewer}: {len(proposals)} proposed, {len(usable)} usable, {len(kept)} agreed")
    path.write_text(json.dumps(kept, indent=1), encoding="utf-8")


def arm(name: str, items, view, memory, sql_fixes: bool) -> list[dict]:
    path = RESULTS / f"guided_{name}.jsonl"
    records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {r["question"] for r in records}
    cfg = PipelineConfig(schema=view, retriever=memory, sql_fixes=sql_fixes, max_rows=MAX_ROWS)
    with path.open("a", encoding="utf-8") as fh:
        for item in items:
            if item["question"] in done:
                continue
            out = answer_question(item["question"], cfg)
            gold = run_readonly(item["gold_sql"], max_rows=MAX_ROWS, search_path=view.search_path)["rows"]
            correct = out["status"] == "ok" and results_match(gold, out["rows"])["relaxed"]
            rec = {"id": item["question"], "question": item["question"], "relaxed": correct, "status": out["status"],
                   "sql": out["sql"], "error": out["error"], "latency_ms": out["latency_ms"]}
            records.append(rec)
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            print(f"{name:<9}{'ok' if correct else '--'} {out['status']:<10}{item['question'][:70]}")
    return records


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-id", type=int, required=True)
    p.add_argument("--build", action="store_true")
    p.add_argument("--questions", type=Path, default=QUESTIONS)
    p.add_argument("--label", help="score only the current code under this name")
    args = p.parse_args()
    view = datasets.get_view(args.dataset_id)
    if args.build:
        return build(view, args.questions)

    items = json.loads(args.questions.read_text(encoding="utf-8"))
    with datasets.engine.connect() as conn:
        profile = conn.execute(datasets.text("SELECT profile FROM datasets WHERE id = :i"),
                               {"i": args.dataset_id}).scalar()
    if args.label:
        memory = Memory(datasets.starter_examples(args.dataset_id, profile, view))
        records = arm(args.label, items, view, memory, True)
        return print(f"\n{args.label} {sum(r['relaxed'] for r in records)}/{len(items)}")
    plain = datasets.build_view(args.dataset_id, profile, guidance=False)
    baseline = arm("baseline", items, plain,
                   Memory(datasets.starter_examples(args.dataset_id, profile, plain, guidance=False)), False)
    guided = arm("guided", items, view,
                 Memory(datasets.starter_examples(args.dataset_id, profile, view, guidance=True)), True)
    n = len(items)
    wa, wb, pval = mcnemar(baseline, guided)
    print(f"\nbaseline {sum(r['relaxed'] for r in baseline)}/{n} | guided {sum(r['relaxed'] for r in guided)}/{n} | "
          f"baseline-only {wa}, guided-only {wb}, p = {pval:.3f}")


if __name__ == "__main__":
    main()
