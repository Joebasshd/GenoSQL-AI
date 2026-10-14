"""
Source of truth for eval/benchmark.jsonl (v0). Edit here, then:

    python -m eval.build_benchmark && python -m eval.run_eval --check-gold

Design rules
- Gold answers are small (counts, aggregates, short listings) so execution
  comparison is robust; listings return the identifying columns only.
- Questions avoid paraphrasing scripts/seed_examples.py.
- `ordered` is set only when the question asks for a ranking.
- gold_sql = None marks an unanswerable question (correct behaviour: abstain).
- Add new items at the END of the list: ids are per-category counters, so
  inserting in the middle would renumber (and re-split) existing items.

Dev/test split
- eval/splits.json freezes each item's split. Existing assignments never
  change; new items are assigned so each category stays near DEV_FRACTION dev.
- Iterate on prompts, seeds and schema notes using dev only. Run test rarely,
  and never change anything because of an individual test item.
"""
import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "benchmark.jsonl"
SPLITS = HERE / "splits.json"
DEV_FRACTION = 0.4

ITEMS = [
    # ── lookup ──────────────────────────────────────────────────────────
    ("lookup", 1, "What alternate alleles are observed at position 16051453 on chromosome 22?",
     "SELECT alt FROM variants WHERE chrom = '22' AND pos = 16051453"),
    ("lookup", 1, "What is the European allele frequency of the G>A variant at chr22:23000030?",
     "SELECT eur_af FROM variants WHERE chrom = '22' AND pos = 23000030 AND ref = 'G' AND alt = 'A'"),
    ("lookup", 1, "Give the alternate allele count and total allele number for the variant at 22:16050115.",
     "SELECT ac, an FROM variants WHERE chrom = '22' AND pos = 16050115"),

    # ── region ──────────────────────────────────────────────────────────
    ("region", 1, "How many variants are there between 23,000,000 and 23,100,000 on chr22?",
     "SELECT COUNT(*) FROM variants WHERE chrom = '22' AND pos BETWEEN 23000000 AND 23100000"),
    ("region", 2, "List the indels between positions 23,000,000 and 23,005,000 on chromosome 22.",
     "SELECT pos, ref, alt FROM variants WHERE chrom = '22' AND variant_type = 'INDEL' "
     "AND pos BETWEEN 23000000 AND 23005000"),
    ("region", 3, "How many structural variants overlap the interval 22:23,000,000-23,100,000?",
     "SELECT COUNT(*) FROM variants WHERE chrom = '22' AND variant_type = 'SV' "
     "AND pos <= 23100000 AND end_pos >= 23000000"),
    ("region", 2, "How many SNPs are on chr22 between 30 Mb and 31 Mb?",
     "SELECT COUNT(*) FROM variants WHERE chrom = '22' AND variant_type = 'SNP' "
     "AND pos BETWEEN 30000000 AND 31000000"),

    # ── aggregation ─────────────────────────────────────────────────────
    ("aggregation", 1, "How many distinct positions on chromosome 22 have at least one variant?",
     "SELECT COUNT(DISTINCT pos) FROM variants WHERE chrom = '22'"),
    ("aggregation", 1, "What is the average global allele frequency of indels?",
     "SELECT AVG(af) FROM variants WHERE variant_type = 'INDEL'"),
    ("aggregation", 2, "How many structural variants are there of each SV type?",
     "SELECT sv_type, COUNT(*) FROM variants WHERE variant_type = 'SV' GROUP BY sv_type"),
    ("aggregation", 3, "What fraction of SNPs are transitions?",
     "SELECT AVG(CASE WHEN (ref, alt) IN (('A','G'),('G','A'),('C','T'),('T','C')) THEN 1.0 ELSE 0.0 END) "
     "FROM variants WHERE variant_type = 'SNP'"),
    ("aggregation", 2, "How many variants have a total read depth above 30,000?",
     "SELECT COUNT(*) FROM variants WHERE dp > 30000"),

    # ── frequency (tests the semantic layer's definitions) ──────────────
    ("frequency", 1, "How many singletons are there?",
     "SELECT COUNT(*) FROM variants WHERE ac = 1"),
    ("frequency", 1, "How many common variants are there?",
     "SELECT COUNT(*) FROM variants WHERE af >= 0.05"),
    ("frequency", 1, "How many variants have an alternate allele frequency above 99%?",
     "SELECT COUNT(*) FROM variants WHERE af > 0.99"),
    ("frequency", 2, "How many rare variants are there?",
     "SELECT COUNT(*) FROM variants WHERE af < 0.01"),
    ("frequency", 2, "How many low-frequency variants are there?",
     "SELECT COUNT(*) FROM variants WHERE af >= 0.01 AND af < 0.05"),

    # ── population ──────────────────────────────────────────────────────
    ("population", 2, "How many variants are private to African populations?",
     "SELECT COUNT(*) FROM variants WHERE afr_af > 0 AND amr_af = 0 AND eas_af = 0 "
     "AND eur_af = 0 AND sas_af = 0"),
    ("population", 2, "How many variants are found only in Europeans?",
     "SELECT COUNT(*) FROM variants WHERE eur_af > 0 AND afr_af = 0 AND amr_af = 0 "
     "AND eas_af = 0 AND sas_af = 0"),
    ("population", 2, "Which 3 variants are most strongly enriched in East Asians relative to Europeans?",
     "SELECT pos, ref, alt FROM variants ORDER BY eas_af - eur_af DESC LIMIT 3", True),
    ("population", 2, "How many variants are common in Africans (frequency at least 5%) but absent from Europeans?",
     "SELECT COUNT(*) FROM variants WHERE afr_af >= 0.05 AND eur_af = 0"),
    ("population", 2, "What is the mean South Asian allele frequency among variants absent in East Asians?",
     "SELECT AVG(sas_af) FROM variants WHERE eas_af = 0"),
    ("population", 2, "How many variants are polymorphic in all five continental populations?",
     "SELECT COUNT(*) FROM variants WHERE afr_af > 0 AND amr_af > 0 AND eas_af > 0 "
     "AND eur_af > 0 AND sas_af > 0"),
    ("population", 3, "Among variants with frequency above 50% in Africans, what proportion also exceed 50% in Europeans?",
     "SELECT AVG(CASE WHEN eur_af > 0.5 THEN 1.0 ELSE 0.0 END) FROM variants WHERE afr_af > 0.5"),
    ("population", 3, "How many variants differ in frequency by more than 0.5 between Africans and Europeans, in either direction?",
     "SELECT COUNT(*) FROM variants WHERE ABS(afr_af - eur_af) > 0.5"),

    # ── structural variants ─────────────────────────────────────────────
    ("sv", 2, "What is the longest deletion structural variant? Give its start, end and length.",
     "SELECT pos, end_pos, end_pos - pos + 1 FROM variants WHERE variant_type = 'SV' AND sv_type = 'DEL' "
     "ORDER BY end_pos - pos DESC LIMIT 1"),
    ("sv", 1, "How many Alu insertions are there?",
     "SELECT COUNT(*) FROM variants WHERE sv_type = 'ALU'"),
    ("sv", 2, "How many structural variants are longer than 100 kb?",
     "SELECT COUNT(*) FROM variants WHERE variant_type = 'SV' AND end_pos - pos + 1 > 100000"),
    ("sv", 2, "How many copy-number variant alleles have a frequency above 1%?",
     "SELECT COUNT(*) FROM variants WHERE sv_type = 'CNV' AND af > 0.01"),

    # ── indels ──────────────────────────────────────────────────────────
    ("indel", 1, "How many insertions are there?",
     "SELECT COUNT(*) FROM variants WHERE variant_type = 'INDEL' AND length(alt) > length(ref)"),
    ("indel", 2, "What is the length in bases of the longest insertion?",
     "SELECT MAX(length(alt) - length(ref)) FROM variants WHERE variant_type = 'INDEL'"),
    ("indel", 2, "How many deletions of 10 or more bases are there?",
     "SELECT COUNT(*) FROM variants WHERE variant_type = 'INDEL' AND length(ref) - length(alt) >= 10"),

    # ── JSONB / INFO ────────────────────────────────────────────────────
    ("json", 2, "How many variants lie within the exome capture target?",
     "SELECT COUNT(*) FROM variants WHERE info ? 'EX_TARGET'"),
    ("json", 3, "For how many SNPs is the high-confidence ancestral allele the alternate allele?",
     "SELECT COUNT(*) FROM variants WHERE variant_type = 'SNP' AND split_part(info->>'AA', '|', 1) = alt"),
    ("json", 2, "How many variant rows come from multi-allelic sites?",
     "SELECT COUNT(*) FROM variants WHERE is_multiallelic"),

    # ── multi-step ──────────────────────────────────────────────────────
    ("multistep", 3, "Which 1-Mb window of chr22 contains the most singletons? Give the window start position and the count.",
     "SELECT (pos / 1000000) * 1000000, COUNT(*) FROM variants WHERE ac = 1 "
     "GROUP BY pos / 1000000 ORDER BY COUNT(*) DESC LIMIT 1"),
    ("multistep", 3, "For each variant type, what percentage of its variants are rare? Round to one decimal place.",
     "SELECT variant_type, ROUND(100.0 * AVG(CASE WHEN af < 0.01 THEN 1 ELSE 0 END), 1) "
     "FROM variants GROUP BY variant_type"),
    ("multistep", 3, "Which super-population has the most private variants, and how many?",
     "SELECT pop, n FROM (SELECT 'AFR' AS pop, COUNT(*) AS n FROM variants WHERE afr_af > 0 AND amr_af = 0 AND eas_af = 0 AND eur_af = 0 AND sas_af = 0 "
     "UNION ALL SELECT 'AMR', COUNT(*) FROM variants WHERE amr_af > 0 AND afr_af = 0 AND eas_af = 0 AND eur_af = 0 AND sas_af = 0 "
     "UNION ALL SELECT 'EAS', COUNT(*) FROM variants WHERE eas_af > 0 AND afr_af = 0 AND amr_af = 0 AND eur_af = 0 AND sas_af = 0 "
     "UNION ALL SELECT 'EUR', COUNT(*) FROM variants WHERE eur_af > 0 AND afr_af = 0 AND amr_af = 0 AND eas_af = 0 AND sas_af = 0 "
     "UNION ALL SELECT 'SAS', COUNT(*) FROM variants WHERE sas_af > 0 AND afr_af = 0 AND amr_af = 0 AND eas_af = 0 AND eur_af = 0) t "
     "ORDER BY n DESC LIMIT 1"),

    # ── ClinVar (single table and joins to 1000 Genomes) ───────────────
    ("clinvar", 2, "How many pathogenic or likely pathogenic ClinVar variants are there on chromosome 22?",
     "SELECT COUNT(*) FROM clinvar WHERE is_pathogenic"),
    ("clinvar", 2, "Which gene has the most pathogenic ClinVar variants, and how many does it have?",
     "SELECT gene, COUNT(*) FROM clinvar WHERE is_pathogenic GROUP BY gene ORDER BY COUNT(*) DESC LIMIT 1"),
    ("clinvar", 1, "How many CHEK2 variants of uncertain significance are in ClinVar?",
     "SELECT COUNT(*) FROM clinvar WHERE gene = 'CHEK2' AND significance_class = 'uncertain'"),
    ("clinvar", 1, "What is the ClinVar clinical significance of rs4680?",
     "SELECT clinical_significance FROM clinvar WHERE rsid = 'rs4680'"),
    ("clinvar", 1, "How many ClinVar variants were reviewed by an expert panel or have a practice guideline?",
     "SELECT COUNT(*) FROM clinvar WHERE review_stars >= 3"),
    ("clinvar", 2, "How many missense ClinVar variants are there in COMT?",
     "SELECT COUNT(*) FROM clinvar WHERE gene = 'COMT' AND consequences LIKE '%missense_variant%'"),
    ("clinvar", 1, "How many different genes on chromosome 22 have at least one ClinVar variant?",
     "SELECT COUNT(DISTINCT gene) FROM clinvar"),
    ("clinvar", 1, "How many ClinVar variants have conflicting classifications?",
     "SELECT COUNT(*) FROM clinvar WHERE significance_class = 'conflicting'"),
    ("clinvar", 2, "How many ClinVar variants are associated with schizophrenia?",
     "SELECT COUNT(*) FROM clinvar WHERE conditions ILIKE '%schizophrenia%'"),
    ("join", 2, "What are the African and European allele frequencies of the APOL1 risk variant rs73885319?",
     "SELECT v.afr_af, v.eur_af FROM clinvar c JOIN variants v ON v.chrom = c.chrom AND v.pos = c.pos "
     "AND v.ref = c.ref AND v.alt = c.alt WHERE c.rsid = 'rs73885319'"),
    ("join", 2, "How many pathogenic ClinVar variants are also observed in the 1000 Genomes data?",
     "SELECT COUNT(*) FROM clinvar c JOIN variants v ON v.chrom = c.chrom AND v.pos = c.pos "
     "AND v.ref = c.ref AND v.alt = c.alt WHERE c.is_pathogenic"),
    ("join", 3, "Which pathogenic ClinVar variants are more than 1 percentage point more frequent in Africans than in Europeans?",
     "SELECT c.pos, c.ref, c.alt FROM clinvar c JOIN variants v ON v.chrom = c.chrom AND v.pos = c.pos "
     "AND v.ref = c.ref AND v.alt = c.alt WHERE c.is_pathogenic AND v.afr_af - v.eur_af > 0.01"),
    ("join", 3, "Among APOL1 variants in ClinVar that also appear in 1000 Genomes, what is the highest African allele frequency?",
     "SELECT MAX(v.afr_af) FROM clinvar c JOIN variants v ON v.chrom = c.chrom AND v.pos = c.pos "
     "AND v.ref = c.ref AND v.alt = c.alt WHERE c.gene = 'APOL1'"),
    ("join", 3, "Of the ClinVar variants that are present in 1000 Genomes, what fraction are rare?",
     "SELECT AVG(CASE WHEN v.af < 0.01 THEN 1.0 ELSE 0.0 END) FROM clinvar c JOIN variants v "
     "ON v.chrom = c.chrom AND v.pos = c.pos AND v.ref = c.ref AND v.alt = c.alt"),

    # ── unanswerable (correct behaviour: abstain) ───────────────────────
    ("unanswerable", 3, "List every 1000 Genomes variant located within the APOL1 gene, including variants that are not in ClinVar.", None),
    ("unanswerable", 2, "What is the gnomAD allele frequency of rs4680?", None),
    ("unanswerable", 1, "Which genes on chromosome 22 are most highly expressed in kidney?", None),
    ("unanswerable", 2, "How many individuals carry the variant at 22:23000030?", None),
    ("unanswerable", 2, "What is the allele frequency of the variant at 22:23000030 in the Yoruba (YRI) population?", None),
    ("unanswerable", 2, "Which ClinVar submitters classified rs4680 as benign?", None),
]


def _fingerprint(item) -> str:
    return hashlib.sha256((item["question"] + "|" + str(item["gold_sql"])).encode()).hexdigest()[:12]


def assign_splits(items: list[dict]) -> dict:
    """Keep frozen assignments; stratify new items per category toward DEV_FRACTION."""
    frozen = json.loads(SPLITS.read_text(encoding="utf-8")) if SPLITS.exists() else {}
    splits = {}
    by_cat: dict[str, list[dict]] = {}
    for item in items:
        by_cat.setdefault(item["category"], []).append(item)

    for cat, cat_items in by_cat.items():
        new = []
        for item in cat_items:
            prev = frozen.get(item["id"])
            if prev is None:
                new.append(item)
                continue
            if prev["fingerprint"] != _fingerprint(item):
                print(f"WARNING {item['id']}: question/gold changed since it was split "
                      f"({prev['split']}); keeping its split")
            splits[item["id"]] = {"split": prev["split"], "fingerprint": _fingerprint(item)}

        random.Random(f"genosql-split-{cat}").shuffle(new)
        for item in new:
            n_dev = sum(splits[i["id"]]["split"] == "dev" for i in cat_items if i["id"] in splits)
            n_done = sum(i["id"] in splits for i in cat_items)
            # dev while below target share of the items assigned so far (+1 for this one)
            split = "dev" if n_dev < round(DEV_FRACTION * (n_done + 1)) else "test"
            splits[item["id"]] = {"split": split, "fingerprint": _fingerprint(item)}

    orphans = sorted(set(frozen) - set(splits))
    if orphans:
        print(f"WARNING: items removed since last split: {orphans}")
    return dict(sorted(splits.items()))


def build():
    items = []
    counters = {}
    for cat, difficulty, question, gold, *rest in ITEMS:
        counters[cat] = counters.get(cat, 0) + 1
        item = {"id": f"{cat[:3].upper()}{counters[cat]:02d}", "category": cat,
                "difficulty": difficulty, "question": question,
                "gold_sql": gold + ";" if gold else None}
        if rest and rest[0]:
            item["ordered"] = True
        items.append(item)

    splits = assign_splits(items)
    for item in items:
        item["split"] = splits[item["id"]]["split"]

    OUT.write_text("\n".join(json.dumps(i) for i in items) + "\n", encoding="utf-8")
    SPLITS.write_text(json.dumps(splits, indent=1) + "\n", encoding="utf-8")
    n_dev = sum(i["split"] == "dev" for i in items)
    print(f"Wrote {len(items)} items to {OUT} ({n_dev} dev / {len(items) - n_dev} test)")


if __name__ == "__main__":
    build()
