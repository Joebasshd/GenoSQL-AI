"""
Evaluate querying an uploaded dataset (generated schema view, starter examples).

    python -m scripts.make_demo_vcf                  # then upload data/demo/demo_cohort.vcf.gz in the UI
    python -m eval.byo_eval --dataset-id 4 --check-gold
    python -m eval.byo_eval --dataset-id 4 --model qwen2.5-coder:3b --tag byo_3b

Questions are hand-written for the demo cohort (12 samples, chr22:19.9-20.1 Mb), with
one record per item fsynced to eval/results/<tag>.jsonl, so a crash loses at most one item.
"""
import argparse
import json
import os
from functools import partial
from pathlib import Path

from app import datasets
from app.database import run_readonly
from app.nl_query.text_to_sql import PipelineConfig, answer_question
from app.rag.retriever import retrieve_similar_examples
from eval.compare import results_match

RESULTS = Path(__file__).resolve().parent / "results"
MAX_ROWS = 5000
G = "genotypes g JOIN variants v ON v.id = g.variant_id"
C = "variants v JOIN clinvar c ON c.chrom = v.chrom AND c.pos = v.pos AND c.ref = v.ref AND c.alt = v.alt"
VG = "variants v JOIN variant_genes vg ON vg.variant_id = v.id JOIN genes gn ON gn.gene_id = vg.gene_id"

# (category, question, gold_sql) -- gold None means the system should decline
ITEMS = [
    ("sites", "How many variants are in this dataset?", "SELECT COUNT(*) FROM variants"),
    ("sites", "How many indels are there?", "SELECT COUNT(*) FROM variants WHERE variant_type = 'INDEL'"),
    ("sites", "How many SNPs are between positions 19,950,000 and 20,000,000?",
     "SELECT COUNT(*) FROM variants WHERE variant_type = 'SNP' AND pos BETWEEN 19950000 AND 20000000"),
    ("sites", "What is the first variant position in the file?", "SELECT MIN(pos) FROM variants"),
    ("sites", "How many multi-allelic rows are there?", "SELECT COUNT(*) FROM variants WHERE is_multiallelic"),
    ("quality", "How many variants failed filtering?", "SELECT COUNT(*) FROM variants WHERE filter <> 'PASS'"),
    ("quality", "How many passing variants have a quality score above 1000?",
     "SELECT COUNT(*) FROM variants WHERE filter = 'PASS' AND quality > 1000"),
    ("quality", "What is the average variant quality of the LowQual variants?",
     "SELECT AVG(quality) FROM variants WHERE filter = 'LowQual'"),
    ("quality", "How many variants have an average mapping quality above 55?",
     "SELECT COUNT(*) FROM variants WHERE (info->>'MQ')::float > 55"),
    ("frequency", "How many variants are singletons in this cohort?", "SELECT COUNT(*) FROM variants WHERE ac = 1"),
    ("frequency", "How many common variants are there?", "SELECT COUNT(*) FROM variants WHERE af >= 0.05"),
    ("frequency", "What is the highest alternate allele frequency in the dataset?", "SELECT MAX(af) FROM variants"),
    ("genotype", "How many samples are in this dataset?", "SELECT COUNT(*) FROM samples"),
    ("genotype", "How many variants does sample HG00451 carry?",
     "SELECT COUNT(*) FROM genotypes WHERE sample_id = 'HG00451'"),
    ("genotype", "How many homozygous alternate genotypes does HG02839 have?",
     "SELECT COUNT(*) FROM genotypes WHERE sample_id = 'HG02839' AND allele_count = 2"),
    ("genotype", "Which sample has the fewest variants, and how many?",
     "SELECT sample_id, COUNT(*) FROM genotypes GROUP BY sample_id ORDER BY COUNT(*) ASC LIMIT 1"),
    ("genotype", "How many samples carry the variant at position 19902526?",
     f"SELECT COUNT(DISTINCT g.sample_id) FROM {G} WHERE v.pos = 19902526"),
    ("genotype", "How many genotype calls have a depth below 10?", "SELECT COUNT(*) FROM genotypes WHERE dp < 10"),
    ("genotype", "What is the mean genotype quality for sample NA18609?",
     "SELECT AVG(gq) FROM genotypes WHERE sample_id = 'NA18609'"),
    ("genotype", "How many passing variants does HG01054 carry?",
     f"SELECT COUNT(*) FROM {G} WHERE g.sample_id = 'HG01054' AND v.filter = 'PASS'"),
    ("annotation", "How many variants fall in the COMT gene?", f"SELECT COUNT(*) FROM {VG} WHERE gn.gene_name = 'COMT'"),
    ("annotation", "Which gene has the most variants in this dataset, and how many?",
     f"SELECT gn.gene_name, COUNT(*) FROM {VG} GROUP BY gn.gene_name ORDER BY COUNT(*) DESC LIMIT 1"),
    ("annotation", "How many variants carried by HG02470 are in TXNRD2?",
     f"SELECT COUNT(*) FROM genotypes g JOIN {VG} ON v.id = g.variant_id "
     "WHERE g.sample_id = 'HG02470' AND gn.gene_name = 'TXNRD2'"),
    ("annotation", "How many variants in this dataset are in ClinVar?", f"SELECT COUNT(*) FROM {C}"),
    ("annotation", "How many ClinVar benign variants are in this dataset?",
     f"SELECT COUNT(*) FROM {C} WHERE c.significance_class = 'benign'"),
    ("annotation", "What is the ClinVar classification of the variant with rsID rs2073771 in this dataset?",
     f"SELECT c.clinical_significance FROM {C} WHERE c.rsid = 'rs2073771'"),
    ("unanswerable", "What disease does sample HG00259 have?", None),
    ("unanswerable", "What is the age of sample NA18609?", None),
    ("unanswerable", "What is the gnomAD frequency of the variant at 19902526?", None),
    ("unanswerable", "Which of these samples are of African ancestry?", None),
]


def check_gold(view):
    for category, question, gold in ITEMS:
        if gold:
            res = run_readonly(gold + ";", max_rows=MAX_ROWS, search_path=view.search_path)
            print(f"{category:<12}{str(res['rows'][:2])[:50]:<52}{question[:60]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-id", type=int, required=True)
    p.add_argument("--model", default="qwen2.5-coder:3b")
    p.add_argument("--tag", default="byo_3b")
    p.add_argument("--check-gold", action="store_true")
    args = p.parse_args()

    view = datasets.get_view(args.dataset_id)
    if view is None:
        raise SystemExit(f"Dataset {args.dataset_id} is not ready")
    if args.check_gold:
        return check_gold(view)

    cfg = PipelineConfig(model=args.model, schema=view, max_rows=MAX_ROWS,
                         retriever=partial(retrieve_similar_examples, dataset_id=args.dataset_id))
    path = RESULTS / f"{args.tag}.jsonl"
    records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {r["question"] for r in records}
    with path.open("a", encoding="utf-8") as fh:
        for category, question, gold in ITEMS:
            if question in done:
                continue
            out = answer_question(question, cfg)
            if gold is None:
                correct = out["status"] == "abstained"
            elif out["status"] != "ok":
                correct = False
            else:
                gold_rows = run_readonly(gold + ";", max_rows=MAX_ROWS, search_path=view.search_path)["rows"]
                correct = results_match(gold_rows, out["rows"])["relaxed"]
            rec = {"category": category, "question": question, "answerable": gold is not None, "correct": correct,
                   "status": out["status"], "sql": out["sql"], "error": out["error"],
                   "attempts": out["attempts"], "latency_ms": out["latency_ms"]}
            records.append(rec)
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            print(f"{'ok' if correct else '--'} {category:<12}{out['latency_ms'] // 1000:>4}s  {question[:70]}")

    answerable = [r for r in records if r["answerable"]]
    print(f"\nEX {100 * sum(r['correct'] for r in answerable) / len(answerable):.1f}% of {len(answerable)} answerable | "
          f"declined {sum(r['correct'] for r in records if not r['answerable'])}/{len(records) - len(answerable)} unanswerable | "
          f"false declines {sum(r['status'] == 'abstained' for r in answerable)} | "
          f"silent wrong {sum(r['status'] == 'ok' and not r['correct'] for r in records)}/{len(records)}")
    for category in dict.fromkeys(c for c, _, _ in ITEMS):
        rs = [r for r in records if r["category"] == category]
        print(f"  {category:<13}{sum(r['correct'] for r in rs)}/{len(rs)}")


if __name__ == "__main__":
    main()
