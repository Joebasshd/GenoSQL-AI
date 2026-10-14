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

    ("which variants overlap position 22,500,000 on chromosome 22, including structural variants",
     "SELECT chrom, pos, end_pos, ref, alt, variant_type FROM variants "
     "WHERE chrom = '22' AND pos <= 22500000 AND end_pos >= 22500000;"),

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

    ("how many ClinVar benign variants have at least two review stars",
     "SELECT COUNT(*) FROM clinvar WHERE significance_class = 'benign' AND review_stars >= 2;"),

    ("European and African frequencies of likely pathogenic ClinVar variants",
     "SELECT c.gene, c.pos, c.ref, c.alt, c.clinical_significance, v.eur_af, v.afr_af "
     "FROM clinvar c JOIN variants v ON v.chrom = c.chrom AND v.pos = c.pos "
     "AND v.ref = c.ref AND v.alt = c.alt WHERE c.significance_class = 'likely_pathogenic';"),

    ("ClinVar variants reported for DiGeorge syndrome",
     "SELECT gene, pos, ref, alt, clinical_significance, conditions FROM clinvar "
     "WHERE conditions ILIKE '%digeorge%';"),

    ("protein-coding genes on the minus strand longer than 500 kb",
     "SELECT gene_name, start_pos, end_pos, end_pos - start_pos + 1 AS length_bp FROM genes "
     "WHERE biotype = 'protein_coding' AND strand = '-' AND end_pos - start_pos + 1 > 500000 ORDER BY length_bp DESC;"),

    ("number of variants of each type in MYH9",
     "SELECT v.variant_type, COUNT(*) AS n FROM variants v "
     "JOIN variant_genes vg ON vg.variant_id = v.id JOIN genes g ON g.gene_id = vg.gene_id "
     "WHERE g.gene_name = 'MYH9' GROUP BY v.variant_type ORDER BY n DESC;"),

    ("carriers per super-population of the ClinVar allele G>A at 22:19951271",
     "SELECT s.super_population, COUNT(*) AS carriers FROM genotypes g "
     "JOIN variants v ON v.id = g.variant_id JOIN samples s ON s.sample_id = g.sample_id "
     "WHERE v.chrom = '22' AND v.pos = 19951271 AND v.ref = 'G' AND v.alt = 'A' "
     "GROUP BY s.super_population ORDER BY carriers DESC;"),

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
