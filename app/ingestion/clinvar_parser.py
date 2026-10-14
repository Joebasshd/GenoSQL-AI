"""
ClinVar VCF ingestion (GRCh37 release: ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/).

Alleles are normalized with the same rule as the 1000 Genomes loader so that
clinvar joins variants on (chrom, pos, ref, alt).
"""
import time

import psycopg2.extras
from app.database import engine
from app.ingestion.vcf_parser import BATCH_SIZE, _open, normalize, parse_info

INSERT_SQL = """
    INSERT INTO clinvar
        (chrom, pos, ref, alt, variation_id, allele_id, rsid, gene, clinical_significance,
         significance_class, is_pathogenic, review_status, review_stars, conditions,
         variant_class, consequences, origin)
    VALUES %s
    ON CONFLICT (chrom, pos, md5(ref), md5(alt), variation_id) DO NOTHING
"""

PATHOGENIC = {"pathogenic", "likely pathogenic", "pathogenic/likely pathogenic"}

STARS = {
    "practice guideline": 4,
    "reviewed by expert panel": 3,
    "criteria provided, multiple submitters, no conflicts": 2,
    "criteria provided, conflicting classifications": 1,
    "criteria provided, conflicting interpretations": 1,
    "criteria provided, single submitter": 1,
}


def _text(values) -> str | None:
    """parse_info splits on ','; ClinVar text fields contain commas, so rejoin."""
    if values is None or values is True:
        return None
    return ",".join(str(v) for v in values).replace("_", " ")


def significance_class(clnsig: str | None) -> str:
    """Coarse class from the primary CLNSIG term (before any '|' or ', ' modifier)."""
    if not clnsig:
        return "other"
    primary = clnsig.split("|")[0].split(",")[0].strip().lower()
    if primary in ("pathogenic", "pathogenic/likely pathogenic"):
        return "pathogenic"
    if primary in ("likely pathogenic",):
        return "likely_pathogenic"
    if primary.startswith("uncertain"):
        return "uncertain"
    if primary in ("likely benign",):
        return "likely_benign"
    if primary in ("benign", "benign/likely benign"):
        return "benign"
    if primary.startswith("conflicting"):
        return "conflicting"
    return "other"


def parse_line(line: str) -> list[tuple]:
    chrom, pos, vid, ref, alts, _qual, _filt, info_field = line.rstrip("\n").split("\t", 8)[:8]
    if alts in (".", ""):
        return []  # ClinVar lists some records without an ALT allele
    info = parse_info(info_field)

    clnsig = _text(info.get("CLNSIG"))
    primary = clnsig.split("|")[0].split(",")[0].strip().lower() if clnsig else ""
    review = _text(info.get("CLNREVSTAT"))
    geneinfo = _text(info.get("GENEINFO"))
    gene = geneinfo.split("|")[0].split(":")[0] if geneinfo else None
    mc_raw = info.get("MC")  # SO terms keep underscores: missense_variant
    mc = ",".join(str(v) for v in mc_raw) if isinstance(mc_raw, list) else None
    consequences = ",".join(term.split("|")[-1] for term in mc.split(",")) if mc else None
    rs = info.get("RS")
    rsid = f"rs{rs[0]}" if isinstance(rs, list) and rs else None
    allele = info.get("ALLELEID")
    origin = info.get("ORIGIN")

    rows = []
    for raw_alt in alts.split(","):
        a_pos, a_ref, alt = normalize(int(pos), ref, raw_alt)
        rows.append((
            chrom, a_pos, a_ref, alt, int(vid),
            allele[0] if isinstance(allele, list) else None,
            rsid, gene, clnsig, significance_class(clnsig), primary in PATHOGENIC,
            review, STARS.get(review or "", 0), _text(info.get("CLNDN")),
            _text(info.get("CLNVC")), consequences,
            origin[0] if isinstance(origin, list) and isinstance(origin[0], int) else None,
        ))
    return rows


def ingest_clinvar(file_path: str, chroms: set[str] | None = frozenset({"22"})):
    """Load ClinVar rows for `chroms` (None = all chromosomes)."""
    raw = engine.raw_connection()
    cursor = raw.cursor()
    started = time.time()
    seen = inserted = 0
    batch = []

    def flush():
        nonlocal inserted
        psycopg2.extras.execute_values(cursor, INSERT_SQL, batch, page_size=len(batch))
        inserted += cursor.rowcount
        raw.commit()
        batch.clear()

    try:
        with _open(file_path) as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                if chroms is not None and line.split("\t", 1)[0] not in chroms:
                    continue
                batch.extend(parse_line(line))
                seen += 1
                if len(batch) >= BATCH_SIZE:
                    flush()
            if batch:
                flush()
        cursor.execute("ANALYZE clinvar")
        raw.commit()
    finally:
        cursor.close()
        raw.close()
    print(f"ClinVar: {seen:,} records -> {inserted:,} rows in {time.time() - started:,.0f}s")
