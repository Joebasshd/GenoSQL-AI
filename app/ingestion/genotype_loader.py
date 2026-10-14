"""
Sparse genotype ingestion: for 1000 Genomes alleles that are also in ClinVar,
store one row per carrier sample (allele_count 1 = het, 2 = hom-alt).

Only target records have their 2,504 genotype columns split, so a pass over
the whole chr22 file costs about as much as the site-level load.
"""
import time

import psycopg2.extras
from sqlalchemy import text
from app.database import engine
from app.ingestion.vcf_parser import _open, normalize

INSERT_SQL = "INSERT INTO genotypes (variant_id, sample_id, allele_count) VALUES %s ON CONFLICT DO NOTHING"


def target_alleles() -> dict[tuple, int]:
    """(chrom, pos, ref, alt) → variants.id for alleles present in ClinVar."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT v.id, v.chrom, v.pos, v.ref, v.alt
            FROM variants v
            JOIN clinvar c ON c.chrom = v.chrom AND c.pos = v.pos AND c.ref = v.ref AND c.alt = v.alt
        """)).all()
    return {(chrom, pos, ref, alt): vid for vid, chrom, pos, ref, alt in rows}


def carriers(gt_fields: list[str], samples: list[str], allele_index: int) -> list[tuple[str, int]]:
    """[(sample, copies)] for samples carrying ALT number `allele_index` (1-based)."""
    k = str(allele_index)
    out = []
    for sample, field in zip(samples, gt_fields):
        gt = field.split(":", 1)[0].replace("/", "|").split("|")
        n = sum(a == k for a in gt)
        if n:
            out.append((sample, n))
    return out


def ingest_genotypes(file_path: str):
    targets = target_alleles()
    print(f"target alleles (1000G and ClinVar): {len(targets):,}")
    raw = engine.raw_connection()
    cursor = raw.cursor()
    started = time.time()
    samples, batch = None, []
    matched = rows = 0

    try:
        with _open(file_path) as fh:
            for line in fh:
                if line.startswith("##"):
                    continue
                if line.startswith("#"):
                    samples = line.rstrip("\n").split("\t")[9:]
                    continue
                chrom, pos, _id, ref, alts, rest = line.split("\t", 5)
                hits = []
                for k, alt in enumerate(alts.split(","), start=1):
                    key = (chrom, *normalize(int(pos), ref, alt))
                    if key in targets:
                        hits.append((k, targets[key]))
                if not hits:
                    continue
                gt_fields = rest.rstrip("\n").split("\t")[4:]   # after QUAL FILTER INFO FORMAT
                for k, variant_id in hits:
                    matched += 1
                    batch.extend((variant_id, s, n) for s, n in carriers(gt_fields, samples, k))
                if len(batch) >= 50_000:
                    psycopg2.extras.execute_values(cursor, INSERT_SQL, batch, page_size=10_000)
                    rows += len(batch)
                    raw.commit()
                    batch.clear()
            if batch:
                psycopg2.extras.execute_values(cursor, INSERT_SQL, batch, page_size=10_000)
                rows += len(batch)
        cursor.execute("ANALYZE genotypes")
        raw.commit()
    finally:
        cursor.close()
        raw.close()
    print(f"genotypes: {matched:,} alleles, {rows:,} carrier rows in {time.time() - started:,.0f}s")
