"""
Download chromosome 22 of two public Genome in a Bottle benchmark VCFs: real,
consented reference genomes in a typical lab format, for testing uploads.

    python -m scripts.fetch_giab          # data/real/ (a few MB each, via the tabix index)
"""
from pathlib import Path

from scripts.fetch_clinvar_slice import fetch_slice

BASE = "https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/giab/release"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "real"
FILES = {
    "HG001_GRCh37_chr22.vcf.gz": (f"{BASE}/NA12878_HG001/NISTv4.2.1/GRCh37/HG001_GRCh37_1_22_v4.2.1_benchmark.vcf.gz", "22"),
    "HG002_GRCh38_chr22.vcf.gz": (f"{BASE}/AshkenazimTrio/HG002_NA24385_son/NISTv4.2.1/GRCh38/HG002_GRCh38_1_22_v4.2.1_benchmark.vcf.gz", "chr22"),
}


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, (url, chrom) in FILES.items():
        if (OUT_DIR / name).exists():
            print(f"exists: {name}")
            continue
        fetch_slice(url, chrom, OUT_DIR / name)
