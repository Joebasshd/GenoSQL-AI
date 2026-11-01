"""
Evaluate the concept gate on questions written independently of it.

    python -m eval.gate_eval --dataset-id 6

A Gemini model (no knowledge of the gate) writes answerable and unanswerable
questions from the dataset's schema documentation. The gate is deterministic, so
it runs instantly, here and over the whole benchmark (false declines).
"""
import argparse
import json
from pathlib import Path

from app import datasets
from app.nl_query.answerability import missing_concept
from app.nl_query.schema import REFERENCE_SCHEMA
from eval.run_eval import load_items
from scripts.generate_benchmark import gemini

OUT = Path(__file__).resolve().parent / "results" / "gate_fresh.json"
PROMPT = """You are testing a natural-language interface to a genomics database. Its documentation:

{schema}

Write {n} realistic questions a researcher or clinician might type that this database CAN answer, and {n}
questions that sound equally plausible but that it CANNOT answer because the needed data is not in it.
Vary the topics and the phrasing widely. Do not quote table or column names.
Return JSON: {{"answerable": [str, ...], "unanswerable": [str, ...]}}"""


def score(view, answerable: list[str], unanswerable: list[str], label: str) -> dict:
    caught = [q for q in unanswerable if missing_concept(q, view)]
    missed = [q for q in unanswerable if not missing_concept(q, view)]
    false = [(q, missing_concept(q, view)) for q in answerable if missing_concept(q, view)]
    print(f"\n{label}: caught {len(caught)}/{len(unanswerable)} unanswerable | "
          f"false declines {len(false)}/{len(answerable)} answerable")
    for q in missed:
        print(f"  missed: {q[:110]}")
    for q, why in false:
        print(f"  FALSE:  {q[:90]}  [{why[:40]}]")
    return {"caught": len(caught), "unanswerable": len(unanswerable), "false_declines": len(false),
            "answerable": len(answerable), "missed": missed, "false": false}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-id", type=int, required=True)
    p.add_argument("--n", type=int, default=40)
    args = p.parse_args()

    items = load_items(split="all")
    report = {"benchmark": score(REFERENCE_SCHEMA, [i["question"] for i in items if i["gold_sql"]],
                                 [i["question"] for i in items if not i["gold_sql"]], "Benchmark (reference data)")}

    view = datasets.get_view(args.dataset_id)
    for label, schema in (("fresh_upload", view), ("fresh_reference", REFERENCE_SCHEMA)):
        questions, model = gemini("author", PROMPT.format(schema=schema.prompt(True, False), n=args.n))
        report[label] = {"model": model, "questions": questions,
                         **score(schema, questions["answerable"], questions["unanswerable"], f"Fresh, {label} ({model})")}
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
