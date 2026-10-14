"""
Schema registry: the single source of truth for what the LLM sees and what
the SQL guard allows. The column descriptions and DOMAIN_NOTES form the
"genomic semantic layer" — an ablation target in the evaluation
(see eval/run_eval.py --no-semantic).
"""

TABLES = {
    "variants": {
        "description": "1000 Genomes Project phase 3 variant sites (GRCh37), one row per ALT allele.",
        "columns": [
            ("id", "BIGINT", "primary key"),
            ("chrom", "TEXT", "chromosome name WITHOUT 'chr' prefix, e.g. '22'"),
            ("pos", "INTEGER", "1-based start position (GRCh37)"),
            ("end_pos", "INTEGER", "end position; for structural variants the SV end, otherwise pos + length(ref) - 1"),
            ("rsid", "TEXT", "dbSNP identifier; NULL for every row in this release (IDs were not distributed)"),
            ("ref", "TEXT", "reference allele"),
            ("alt", "TEXT", "single alternate allele; symbolic for SVs, e.g. '<CN0>', '<INS:ME:ALU>'"),
            ("variant_type", "TEXT", "per-allele class: 'SNP' (single base change), 'INDEL' (ref and alt lengths differ), "
                                     "'MNP' (equal-length multi-base change), 'SV' (structural variant)"),
            ("sv_type", "TEXT", "structural variant class when variant_type = 'SV': 'DEL', 'DUP', 'CNV', 'INV', 'ALU', 'LINE1', 'SVA', ...; else NULL"),
            ("is_multiallelic", "BOOLEAN", "true if the site has more than one ALT allele"),
            ("quality", "REAL", "QUAL; constant (100) in this dataset, not informative"),
            ("filter", "TEXT", "FILTER status, 'PASS' for all sites in this release"),
            ("ac", "INTEGER", "alternate allele count across all 2,504 samples"),
            ("an", "INTEGER", "total number of called alleles (usually 5008 = 2 x 2504)"),
            ("af", "REAL", "global alternate allele frequency, 0..1"),
            ("afr_af", "REAL", "allele frequency in African-ancestry samples"),
            ("amr_af", "REAL", "allele frequency in admixed American samples"),
            ("eas_af", "REAL", "allele frequency in East Asian samples"),
            ("eur_af", "REAL", "allele frequency in European samples"),
            ("sas_af", "REAL", "allele frequency in South Asian samples"),
            ("dp", "INTEGER", "total read depth at the site"),
            ("info", "JSONB", "other INFO fields: info->>'AA' ancestral allele as 'A|||' (upper case = high confidence, lower case = low confidence, '.' = unknown); "
                              "info ? 'EX_TARGET' is true when the site is inside the exome capture target; info->>'CS' SV call set; info->>'SVLEN', info->>'CIPOS', info->>'CIEND' for SVs"),
        ],
    },
    "clinvar": {
        "description": "ClinVar clinical variant classifications on chromosome 22 (GRCh37, release 2026-10-11), one row per ALT allele.",
        "columns": [
            ("id", "BIGINT", "primary key"),
            ("chrom", "TEXT", "chromosome without 'chr' prefix; always '22'"),
            ("pos", "INTEGER", "1-based position (GRCh37), normalized the same way as variants.pos"),
            ("ref", "TEXT", "reference allele"),
            ("alt", "TEXT", "alternate allele"),
            ("variation_id", "INTEGER", "ClinVar Variation ID"),
            ("allele_id", "INTEGER", "ClinVar Allele ID"),
            ("rsid", "TEXT", "dbSNP rsID such as 'rs4680'; NULL when ClinVar has none"),
            ("gene", "TEXT", "HGNC gene symbol, e.g. 'CHEK2', 'COMT', 'APOL1' (first gene if several)"),
            ("clinical_significance", "TEXT", "ClinVar classification text, e.g. 'Pathogenic', 'Likely pathogenic', "
                                              "'Pathogenic/Likely pathogenic', 'Uncertain significance', 'Likely benign', "
                                              "'Benign', 'Benign/Likely benign', 'Conflicting classifications of pathogenicity', 'drug response'"),
            ("significance_class", "TEXT", "normalized class: 'pathogenic', 'likely_pathogenic', 'uncertain', "
                                           "'likely_benign', 'benign', 'conflicting', 'other'"),
            ("is_pathogenic", "BOOLEAN", "true for Pathogenic, Likely pathogenic, or Pathogenic/Likely pathogenic"),
            ("review_status", "TEXT", "ClinVar review status, e.g. 'criteria provided, multiple submitters, no conflicts'"),
            ("review_stars", "SMALLINT", "ClinVar gold stars 0-4 (4 practice guideline, 3 expert panel, 2 multiple submitters no conflicts, 1 single submitter or conflicting)"),
            ("conditions", "TEXT", "associated condition names separated by '|', e.g. 'Li-Fraumeni syndrome 2|not provided'"),
            ("variant_class", "TEXT", "e.g. 'single nucleotide variant', 'Deletion', 'Duplication', 'Insertion', 'Indel'"),
            ("consequences", "TEXT", "comma-separated Sequence Ontology terms, e.g. 'missense_variant', 'nonsense', 'frameshift_variant', 'intron_variant'"),
            ("origin", "INTEGER", "allele origin bit flags: 1 germline, 2 somatic, 4 inherited, 8 paternal, 16 maternal, 32 de novo"),
        ],
    },
}

DOMAIN_NOTES = """
Conventions:
- "chr22", "chromosome 22" and "22" all mean chrom = '22'.
- Positions are 1-based. A variant overlaps region [s, e] when pos <= e AND end_pos >= s.
- "Rare" means af < 0.01; "low frequency" 0.01 <= af < 0.05; "common" af >= 0.05.
- "Singleton" means ac = 1. "Fixed" or near-fixed alt alleles have af > 0.99.
- Population codes: AFR = African, AMR = Admixed American, EAS = East Asian,
  EUR = European, SAS = South Asian. Use the *_af columns for population questions.
- "Population-specific" / "private to X" means the X frequency is > 0 and all
  other population frequencies are 0.
- A transition is A<->G or C<->T; any other single-base change is a transversion.
- Indel length = abs(length(alt) - length(ref)); insertion when length(alt) > length(ref).
- Do not filter on quality or filter; they carry no information in this dataset.
- variants.rsid is always NULL; rsIDs exist only in clinvar.rsid.
- To get population frequencies of ClinVar variants, join clinvar to variants
  ON chrom, pos, ref AND alt. Many ClinVar variants are absent from 1000 Genomes.
- "Pathogenic" means is_pathogenic (Pathogenic and/or Likely pathogenic). Never match
  pathogenicity with LIKE '%pathogenic%': it also matches 'Conflicting classifications
  of pathogenicity'. Match conditions and genes case-insensitively (ILIKE).
- Gene membership is known only for variants in clinvar (clinvar.gene); there are no
  gene coordinates, so "all 1000 Genomes variants in gene X" cannot be answered.
- There is NO sample, genotype, individual, sub-population (e.g. YRI, CEU), expression
  or gnomAD data. Questions needing those cannot be answered.
""".strip()


def schema_prompt(semantic: bool = True) -> str:
    """Render the schema for the system prompt. semantic=False gives names/types only."""
    parts = []
    for table, spec in TABLES.items():
        parts.append(f"Table: {table}" + (f" — {spec['description']}" if semantic else ""))
        for name, typ, desc in spec["columns"]:
            parts.append(f"- {name} ({typ})" + (f": {desc}" if semantic else ""))
        parts.append("")
    if semantic:
        parts.append(DOMAIN_NOTES)
    return "\n".join(parts).strip()


ALLOWED_TABLES = set(TABLES)
ALLOWED_COLUMNS = {name for spec in TABLES.values() for name, _, _ in spec["columns"]}
TABLE_COLUMNS = {t: {name for name, _, _ in spec["columns"]} for t, spec in TABLES.items()}
