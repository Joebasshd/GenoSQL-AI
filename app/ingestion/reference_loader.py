"""
Load the 1000 Genomes sample panel (+ population names) and Ensembl genes.
"""
import gzip
from urllib.parse import unquote

import psycopg2.extras
from app.database import engine

# 1000 Genomes phase 3 population codes (IGSR documentation).
POPULATIONS = {
    "ACB": ("African Caribbean in Barbados", "AFR"),
    "ASW": ("African Ancestry in Southwest US", "AFR"),
    "ESN": ("Esan in Nigeria", "AFR"),
    "GWD": ("Gambian in Western Division, The Gambia", "AFR"),
    "LWK": ("Luhya in Webuye, Kenya", "AFR"),
    "MSL": ("Mende in Sierra Leone", "AFR"),
    "YRI": ("Yoruba in Ibadan, Nigeria", "AFR"),
    "CLM": ("Colombian in Medellin, Colombia", "AMR"),
    "MXL": ("Mexican Ancestry in Los Angeles, California", "AMR"),
    "PEL": ("Peruvian in Lima, Peru", "AMR"),
    "PUR": ("Puerto Rican in Puerto Rico", "AMR"),
    "CDX": ("Chinese Dai in Xishuangbanna, China", "EAS"),
    "CHB": ("Han Chinese in Beijing, China", "EAS"),
    "CHS": ("Southern Han Chinese", "EAS"),
    "JPT": ("Japanese in Tokyo, Japan", "EAS"),
    "KHV": ("Kinh in Ho Chi Minh City, Vietnam", "EAS"),
    "CEU": ("Utah residents (CEPH) with Northern and Western European ancestry", "EUR"),
    "FIN": ("Finnish in Finland", "EUR"),
    "GBR": ("British in England and Scotland", "EUR"),
    "IBS": ("Iberian populations in Spain", "EUR"),
    "TSI": ("Toscani in Italy", "EUR"),
    "BEB": ("Bengali in Bangladesh", "SAS"),
    "GIH": ("Gujarati Indians in Houston, Texas", "SAS"),
    "ITU": ("Indian Telugu in the UK", "SAS"),
    "PJL": ("Punjabi in Lahore, Pakistan", "SAS"),
    "STU": ("Sri Lankan Tamil in the UK", "SAS"),
}

SUPER_POPULATIONS = {
    "AFR": "African", "AMR": "Admixed American", "EAS": "East Asian",
    "EUR": "European", "SAS": "South Asian",
}


def load_samples(panel_path: str):
    pop_rows = [(code, name, sp, SUPER_POPULATIONS[sp]) for code, (name, sp) in POPULATIONS.items()]
    sample_rows = []
    with open(panel_path, encoding="utf-8") as fh:
        next(fh)  # header: sample pop super_pop gender
        for line in fh:
            sample, pop, super_pop, sex = line.rstrip("\n").split("\t")[:4]
            if POPULATIONS[pop][1] != super_pop:
                raise ValueError(f"{sample}: {pop} is not in {super_pop}")
            sample_rows.append((sample, pop, super_pop, sex))

    with engine.begin() as conn:
        cur = conn.connection.cursor()
        psycopg2.extras.execute_values(cur, """
            INSERT INTO populations VALUES %s ON CONFLICT (population) DO NOTHING""", pop_rows)
        psycopg2.extras.execute_values(cur, """
            INSERT INTO samples VALUES %s ON CONFLICT (sample_id) DO NOTHING""", sample_rows)
    print(f"populations: {len(pop_rows)} | samples: {len(sample_rows):,}")


def parse_gff_genes(path: str) -> list[tuple]:
    """Gene-level features (ID=gene:...) from an Ensembl GFF3."""
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 9 or not f[8].startswith("ID=gene:"):
                continue
            attrs = dict(kv.split("=", 1) for kv in f[8].split(";") if "=" in kv)
            rows.append((
                attrs["gene_id"], attrs.get("Name"), attrs.get("biotype"),
                f[0], int(f[3]), int(f[4]), f[6],
                unquote(attrs["description"]) if "description" in attrs else None,
            ))
    return rows


def load_genes(gff_path: str):
    rows = parse_gff_genes(gff_path)
    with engine.begin() as conn:
        cur = conn.connection.cursor()
        psycopg2.extras.execute_values(cur, """
            INSERT INTO genes (gene_id, gene_name, biotype, chrom, start_pos, end_pos, strand, description)
            VALUES %s ON CONFLICT (gene_id) DO NOTHING""", rows)
        cur.execute("ANALYZE genes")
    print(f"genes: {len(rows):,}")


def build_variant_genes():
    """
    Materialise variant/gene overlaps. Short variants are matched with an
    index-friendly pos window (no non-SV allele here exceeds 1 kb); structural
    variants, whose spans can be hundreds of kb, use the full overlap test.
    """
    with engine.begin() as conn:
        cur = conn.connection.cursor()
        cur.execute("TRUNCATE variant_genes")
        cur.execute("""
            INSERT INTO variant_genes (variant_id, gene_id)
            SELECT v.id, g.gene_id FROM genes g
            JOIN variants v ON v.chrom = g.chrom
                AND v.pos BETWEEN g.start_pos - 1000 AND g.end_pos
                AND v.end_pos >= g.start_pos
            WHERE v.variant_type <> 'SV'
            UNION
            SELECT v.id, g.gene_id FROM variants v
            JOIN genes g ON g.chrom = v.chrom AND v.pos <= g.end_pos AND v.end_pos >= g.start_pos
            WHERE v.variant_type = 'SV'
        """)
        n = cur.rowcount
        cur.execute("SELECT MAX(end_pos - pos) FROM variants WHERE variant_type <> 'SV'")
        longest = cur.fetchone()[0]
        if longest >= 1000:
            raise RuntimeError(f"non-SV allele spans {longest} bp; widen the window in build_variant_genes")
        cur.execute("ANALYZE variant_genes")
    print(f"variant_genes: {n:,} overlaps")
