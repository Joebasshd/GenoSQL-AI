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
- There is NO gene, sample, genotype, phenotype or clinical table, and no rsIDs.
  Questions about genes by name, rsIDs, individuals, diseases or pathogenicity cannot be answered.
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
