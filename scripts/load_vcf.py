"""
    python -m scripts.load_vcf                       # data/chr22.vcf.gz
    python -m scripts.load_vcf path/to/file.vcf.gz --limit 10000
"""
import argparse
from app.ingestion.vcf_parser import ingest_vcf

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data/chr22.vcf.gz")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    ingest_vcf(args.path, args.limit)
