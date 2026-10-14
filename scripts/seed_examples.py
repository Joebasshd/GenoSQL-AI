"""
Seed RAG memory with hand-verified (question → SQL) pairs.

    python -m scripts.seed_examples            # add seeds
    python -m scripts.seed_examples --reset    # delete ALL examples first

Seeds teach conventions (chrom without 'chr', overlap logic, population
columns, JSONB access), not answers. They must not paraphrase items in
eval/benchmark.jsonl, or the evaluation measures memorisation.
"""
import argparse
from sqlalchemy import text
from app.database import engine
from app.rag.example_store import store_example

SEEDS = [
    ("show variants between 17,000,000 and 17,010,000 on chr22",
     "SELECT chrom, pos, ref, alt, af FROM variants "
     "WHERE chrom = '22' AND pos BETWEEN 17000000 AND 17010000 ORDER BY pos;"),

    ("which variants overlap position 18,500,000 on chromosome 22, including structural variants",
     "SELECT chrom, pos, end_pos, ref, alt, variant_type FROM variants "
     "WHERE chrom = '22' AND pos <= 18500000 AND end_pos >= 18500000;"),

    ("population frequencies of the C>T variant at 22:23000215",
     "SELECT chrom, pos, ref, alt, af, afr_af, amr_af, eas_af, eur_af, sas_af "
     "FROM variants WHERE chrom = '22' AND pos = 23000215 AND ref = 'C' AND alt = 'T';"),

    ("how many variants of each type are there",
     "SELECT variant_type, COUNT(*) AS n FROM variants GROUP BY variant_type ORDER BY n DESC;"),

    ("how many ultra-rare variants with frequency under 0.1% are there",
     "SELECT COUNT(*) FROM variants WHERE af < 0.001;"),

    ("variants at least 40 percentage points more frequent in East Asians than in Europeans",
     "SELECT chrom, pos, ref, alt, eas_af, eur_af FROM variants "
     "WHERE eas_af - eur_af >= 0.4 ORDER BY eas_af - eur_af DESC;"),

    ("count variants seen only in South Asian samples",
     "SELECT COUNT(*) FROM variants WHERE sas_af > 0 "
     "AND afr_af = 0 AND amr_af = 0 AND eas_af = 0 AND eur_af = 0;"),

    ("list duplication structural variants longer than 100 kb",
     "SELECT chrom, pos, end_pos, alt, end_pos - pos + 1 AS length_bp, af FROM variants "
     "WHERE variant_type = 'SV' AND sv_type = 'DUP' AND end_pos - pos + 1 > 100000 ORDER BY length_bp DESC;"),

    ("how many deletions of exactly 2 bases are there",
     "SELECT COUNT(*) FROM variants WHERE variant_type = 'INDEL' "
     "AND length(ref) - length(alt) = 2;"),

    ("what is the ancestral allele at 22:16050075",
     "SELECT chrom, pos, ref, alt, info->>'AA' AS ancestral_allele FROM variants "
     "WHERE chrom = '22' AND pos = 16050075;"),
]


def seed(reset: bool = False):
    if reset:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sql_examples"))
        print("Cleared sql_examples.")
    for question, sql in SEEDS:
        store_example(question, sql, verified=True, source="seed")
    print(f"Seeded {len(SEEDS)} verified examples.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    seed(parser.parse_args().reset)
