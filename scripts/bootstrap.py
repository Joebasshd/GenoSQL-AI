"""
One command to build the whole local database from public data:

    python -m scripts.bootstrap

Steps already done are skipped, so it is safe to re-run after an interruption.
Downloads (~210 MB total; the 1000 Genomes chr22 VCF is ~205 MB of it):
  - 1000 Genomes phase 3 chr22 VCF          -> data/chr22.vcf.gz
  - ClinVar GRCh37 chr22 slice (tabix)      -> data/clinvar/
  - 1000G sample panel, Ensembl chr22 genes -> data/reference/
Then loads everything into Postgres and seeds the verified examples.
Expect ~15-25 minutes on a laptop, most of it parsing the chr22 VCF.
"""
import argparse
import time
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
CHR22_URL = ("https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/"
             "ALL.chr22.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz")
CHR22 = ROOT / "data" / "chr22.vcf.gz"
CLINVAR = ROOT / "data" / "clinvar" / "clinvar_GRCh37_chr22.vcf.gz"


def count(table: str) -> int:
    from app.database import engine
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar()
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() if exists else 0


def download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    done = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=None) as r:
        r.raise_for_status()
        if done and r.status_code != 206:
            done = 0  # server ignored Range: start over
        total = done + int(r.headers.get("content-length", 0))
        with part.open("ab" if done else "wb") as fh:
            last = time.time()
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if time.time() - last > 10:
                    print(f"  {done / 1e6:,.0f} / {total / 1e6:,.0f} MB")
                    last = time.time()
    part.rename(dest)


def step(name: str, skip: bool, fn):
    if skip:
        print(f"[skip] {name}")
        return
    print(f"[run ] {name}")
    started = time.time()
    fn()
    print(f"[done] {name} ({time.time() - started:,.0f}s)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true", help="drop all data tables first")
    args = p.parse_args()

    from scripts import fetch_clinvar_slice, fetch_reference_data, seed_examples, setup_db
    from app.ingestion.clinvar_parser import ingest_clinvar
    from app.ingestion.genotype_loader import ingest_genotypes
    from app.ingestion.reference_loader import build_variant_genes, load_genes, load_samples
    from app.ingestion.vcf_parser import ingest_vcf

    ref = ROOT / "data" / "reference"
    step("schema + reader role", False, lambda: setup_db.setup(args.reset))
    step("download 1000G chr22 VCF (~205 MB)", CHR22.exists(), lambda: download(CHR22_URL, CHR22))
    step("load variants", count("variants") > 0, lambda: ingest_vcf(str(CHR22)))
    step("download ClinVar chr22 slice", CLINVAR.exists(), fetch_clinvar_slice.main)
    step("load clinvar", count("clinvar") > 0, lambda: ingest_clinvar(str(CLINVAR)))
    step("download sample panel + genes", False, fetch_reference_data.main)
    step("load samples", count("samples") > 0,
         lambda: load_samples(str(ref / "integrated_call_samples_v3.20130502.ALL.panel")))
    step("load genes", count("genes") > 0,
         lambda: load_genes(str(ref / "Homo_sapiens.GRCh37.87.chromosome.22.gff3.gz")))
    step("variant/gene overlaps", count("variant_genes") > 0, build_variant_genes)
    step("carrier genotypes", count("genotypes") > 0, lambda: ingest_genotypes(str(CHR22)))
    step("seed verified examples", count("sql_examples") > 0, seed_examples.seed)
    print("\nReady. Start the app with:  uvicorn app.main:app")


if __name__ == "__main__":
    main()
