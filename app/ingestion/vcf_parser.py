"""
Site-level VCF ingestion.

Reads only the first 8 VCF columns (genotype columns are skipped without
being parsed, which is what makes a 2,504-sample 1000 Genomes file fast to
load), splits multi-allelic records into one row per ALT allele, and
promotes common INFO fields to typed columns.
"""
import gzip
import json
import time

import psycopg2.extras
from app.database import engine

BATCH_SIZE = 5000

# INFO keys with Number=A in 1000 Genomes: one value per ALT allele.
PER_ALT_KEYS = {"AC", "AF", "AFR_AF", "AMR_AF", "EAS_AF", "EUR_AF", "SAS_AF"}
# INFO keys promoted to columns (not duplicated in the JSONB blob).
PROMOTED_KEYS = PER_ALT_KEYS | {"AN", "DP", "VT", "SVTYPE", "END", "MULTI_ALLELIC"}

INSERT_SQL = """
    INSERT INTO variants
        (chrom, pos, end_pos, rsid, ref, alt, variant_type, sv_type, is_multiallelic,
         quality, filter, ac, an, af, afr_af, amr_af, eas_af, eur_af, sas_af, dp, info)
    VALUES %s
    ON CONFLICT (chrom, pos, ref, alt) DO NOTHING
"""


def _num(value: str):
    """Parse a VCF scalar: int, float, or leave as string."""
    if value in (".", ""):
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def parse_info(field: str) -> dict:
    """Parse 'K=V;K2=V2,V3;FLAG' into {K: [V], K2: [V2, V3], FLAG: True}."""
    info = {}
    if field in (".", ""):
        return info
    for item in field.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            info[key] = [_num(v) for v in value.split(",")]
        else:
            info[item] = True
    return info


def _pick(values, i):
    """Value i of a per-allele list (None if missing)."""
    if not isinstance(values, list) or i >= len(values):
        return None
    return values[i]


def _first(values):
    return values[0] if isinstance(values, list) and values else None


def normalize(pos: int, ref: str, alt: str) -> tuple[int, str, str]:
    """
    Minimal allele representation: trim shared trailing bases, then shared
    leading bases (shifting pos), always keeping at least one base. Splitting
    multi-allelic records leaves padding (ALT=CG,TG at REF=CG gives CG>TG,
    really C>T); trimming makes rows joinable with other sources (ClinVar).
    Symbolic SV alleles are left untouched.
    """
    if alt.startswith("<") or alt in (".", "*"):
        return pos, ref, alt
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    return pos, ref, alt


def classify(ref: str, alt: str, sv_type) -> str:
    """
    Per-allele variant class. INFO VT can't be used for this: on multi-allelic
    records it lists the types present at the site, not in ALT order
    (e.g. ALT=AT,T with VT=SNP,INDEL).
    """
    if sv_type or alt.startswith("<"):
        return "SV"
    if len(ref) == 1 and len(alt) == 1:
        return "SNP"
    if len(ref) == len(alt):
        return "MNP"
    return "INDEL"


def parse_line(line: str) -> list[tuple]:
    """Turn one VCF data line into one row tuple per ALT allele."""
    chrom, pos, vid, ref, alts, qual, filt, info_field = line.rstrip("\n").split("\t", 8)[:8]
    pos = int(pos)
    alt_list = alts.split(",")
    info = parse_info(info_field)
    multi = len(alt_list) > 1 or "MULTI_ALLELIC" in info

    end = _first(info.get("END"))
    rsid = None if vid == "." else vid
    quality = _num(qual)
    filt = None if filt == "." else filt
    sv_type = _first(info.get("SVTYPE"))
    an = _first(info.get("AN"))
    dp = _first(info.get("DP"))

    extra = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
             for k, v in info.items() if k not in PROMOTED_KEYS}
    extra_json = json.dumps(extra) if extra else None

    rows = []
    for i, raw_alt in enumerate(alt_list):
        a_pos, a_ref, alt = normalize(pos, ref, raw_alt)
        end_pos = int(end) if isinstance(end, int) else a_pos + len(a_ref) - 1
        rows.append((
            chrom, a_pos, end_pos, rsid, a_ref, alt, classify(a_ref, alt, sv_type), sv_type, multi,
            quality, filt,
            _pick(info.get("AC"), i), an, _pick(info.get("AF"), i),
            _pick(info.get("AFR_AF"), i), _pick(info.get("AMR_AF"), i),
            _pick(info.get("EAS_AF"), i), _pick(info.get("EUR_AF"), i),
            _pick(info.get("SAS_AF"), i), dp, extra_json,
        ))
    return rows


def _open(file_path: str):
    if file_path.endswith((".gz", ".bgz")):
        return gzip.open(file_path, "rt")
    return open(file_path, "rt")


def ingest_vcf(file_path: str, limit: int | None = None):
    """Stream a VCF into the variants table. `limit` caps the number of records (for tests)."""
    raw = engine.raw_connection()
    cursor = raw.cursor()
    started = time.time()
    seen = rows_inserted = 0
    batch = []

    def flush():
        nonlocal rows_inserted
        psycopg2.extras.execute_values(cursor, INSERT_SQL, batch, page_size=len(batch))
        rows_inserted += cursor.rowcount
        raw.commit()
        batch.clear()
        rate = seen / max(time.time() - started, 1e-9)
        print(f"records: {seen:,} | rows inserted: {rows_inserted:,} | {rate:,.0f} records/s")

    try:
        with _open(file_path) as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                batch.extend(parse_line(line))
                seen += 1
                if len(batch) >= BATCH_SIZE:
                    flush()
                if limit and seen >= limit:
                    break
            if batch:
                flush()
        cursor.execute("ANALYZE variants")
        raw.commit()
    finally:
        cursor.close()
        raw.close()

    print(f"\nDone in {time.time() - started:,.0f}s. Records: {seen:,} | rows inserted: {rows_inserted:,}")
