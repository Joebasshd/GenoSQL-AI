"""
    python -m scripts.fetch_clinvar_slice                       # chr22 slice via tabix index (~4 MB)
    python -m scripts.load_clinvar                              # chr22 only (matches the 1000G data)
    python -m scripts.load_clinvar --all-chroms

Source: https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz
"""
import argparse
from app.ingestion.clinvar_parser import ingest_clinvar

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data/clinvar/clinvar_GRCh37_chr22.vcf.gz")
    parser.add_argument("--all-chroms", action="store_true")
    args = parser.parse_args()
    ingest_clinvar(args.path, None if args.all_chroms else {"22"})
