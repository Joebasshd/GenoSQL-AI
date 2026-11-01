"""
Train the out-of-scope classifier: logistic regression on sentence embeddings.

    python -m eval.train_scope --dataset-id 8          # fetch training questions (cached) and fit
    python -m eval.train_scope --dataset-id 8 --test   # held-out score on fresh questions

Inference (app/nl_query/answerability.py) only needs the saved weights and numpy.
The threshold comes from cross-validation so that false declines stay near TARGET_FPR.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

from app import datasets
from app.nl_query.answerability import MODEL_PATH, missing_concept, out_of_scope
from app.nl_query.schema import REFERENCE_SCHEMA
from app.rag.embeddings import embed_text
from eval.run_eval import load_items
from scripts.generate_benchmark import gemini

DATA = Path(__file__).resolve().parent / "scope_data.json"
FRESH = Path(__file__).resolve().parent / "results" / "gate_fresh.json"
TEST_OUT = Path(__file__).resolve().parent / "results" / "scope_heldout.json"
TARGET_FPR = 0.02

PROMPT = """You are testing a natural-language interface to a genomics database. Its documentation:

{schema}

Write {n} realistic questions a researcher or clinician might type that this database CAN answer, and {n}
questions that sound equally plausible but that it CANNOT answer because the needed data is not in it.
Focus for this batch: {focus}.
Vary the phrasing widely, from expert to plain language. Do not quote table or column names.
Return JSON: {{"answerable": [str, ...], "unanswerable": [str, ...]}}"""

TRAIN_FOCUS = [
    "counting and listing variants; the unanswerable ones need functional prediction, regulation or epigenomics data",
    "individuals, genotypes and carriers; the unanswerable ones need clinical records, family or laboratory details",
    "genes and clinical significance; the unanswerable ones need external databases, literature or protein biology",
]
TEST_FOCUS = "a broad mix of whatever a working geneticist would really ask"


def collect(schemas: dict) -> tuple[list[str], list[str]]:
    answerable, unanswerable = [], []
    for item in load_items(split="all"):
        (answerable if item["gold_sql"] else unanswerable).append(item["question"])
        if item["gold_sql"] and item.get("question_lay"):
            answerable.append(item["question_lay"])
    for label in ("fresh_upload", "fresh_reference"):
        batch = json.loads(FRESH.read_text(encoding="utf-8"))[label]["questions"]
        answerable += batch["answerable"]
        unanswerable += batch["unanswerable"]
    for schema in schemas.values():
        for focus in TRAIN_FOCUS:
            batch, _ = gemini("author", PROMPT.format(schema=schema.prompt(True, False), n=40, focus=focus))
            answerable += batch["answerable"]
            unanswerable += batch["unanswerable"]
    answerable, unanswerable = sorted(set(answerable)), sorted(set(unanswerable) - set(answerable))
    DATA.write_text(json.dumps({"answerable": answerable, "unanswerable": unanswerable}, indent=1), encoding="utf-8")
    return answerable, unanswerable


def fit(X, y):
    return LogisticRegression(C=10.0, class_weight="balanced", max_iter=2000).fit(X, y)


def choose_threshold(X, y) -> float:
    scores = np.zeros(len(y))
    for train, held in StratifiedKFold(5, shuffle=True, random_state=7).split(X, y):
        scores[held] = fit(X[train], y[train]).predict_proba(X[held])[:, 1]
    negatives = np.sort(scores[y == 0])
    threshold = float(negatives[int(len(negatives) * (1 - TARGET_FPR))])
    print(f"Cross-validated at threshold {threshold:.3f}: recall {np.mean(scores[y == 1] > threshold):.1%}, "
          f"false declines {np.mean(scores[y == 0] > threshold):.1%}")
    return threshold


def train(schemas):
    answerable, unanswerable = collect(schemas)
    print(f"Training on {len(answerable)} answerable, {len(unanswerable)} unanswerable questions")
    X = np.array([embed_text(q) for q in answerable + unanswerable])
    y = np.array([0] * len(answerable) + [1] * len(unanswerable))
    threshold = choose_threshold(X, y)
    model = fit(X, y)
    MODEL_PATH.write_text(json.dumps({"threshold": threshold, "intercept": float(model.intercept_[0]),
                                      "weights": [round(float(w), 5) for w in model.coef_[0]]}), encoding="utf-8")
    print(f"-> {MODEL_PATH}")


def test(schemas):
    report = {}
    for label, schema in schemas.items():
        batch, model = gemini("author", PROMPT.format(schema=schema.prompt(True, False), n=40, focus=TEST_FOCUS))
        declined = lambda q: bool(missing_concept(q, schema) or out_of_scope(q))
        caught = [q for q in batch["unanswerable"] if declined(q)]
        false = [q for q in batch["answerable"] if declined(q)]
        print(f"{label} ({model}): caught {len(caught)}/{len(batch['unanswerable'])} unanswerable | "
              f"false declines {len(false)}/{len(batch['answerable'])}")
        report[label] = {"model": model, "questions": batch, "caught": len(caught), "false_declines": false,
                         "missed": [q for q in batch["unanswerable"] if not declined(q)]}
        for q in report[label]["missed"]:
            print(f"  missed: {q[:105]}")
        for q in false:
            print(f"  FALSE:  {q[:105]}")
    items = load_items(split="all")
    wrong = [i["question"] for i in items if i["gold_sql"] and (missing_concept(i["question"], REFERENCE_SCHEMA) or out_of_scope(i["question"]))]
    print(f"Benchmark answerable declined: {len(wrong)} (training data, so optimistic)")
    TEST_OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-id", type=int, required=True)
    p.add_argument("--test", action="store_true")
    args = p.parse_args()
    schemas = {"upload": datasets.get_view(args.dataset_id), "reference": REFERENCE_SCHEMA}
    (test if args.test else train)(schemas)


if __name__ == "__main__":
    main()
