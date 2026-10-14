"""
Load Phase 1 reference data (run after load_vcf and load_clinvar):

    python -m scripts.fetch_reference_data
    python -m scripts.load_reference                 # samples, populations, genes, variant_genes, genotypes
"""
import argparse
from app.ingestion.genotype_loader import ingest_genotypes
from app.ingestion.reference_loader import build_variant_genes, load_genes, load_samples

REF = "data/reference"

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--panel", default=f"{REF}/integrated_call_samples_v3.20130502.ALL.panel")
    p.add_argument("--genes", default=f"{REF}/Homo_sapiens.GRCh37.87.chromosome.22.gff3.gz")
    p.add_argument("--vcf", default="data/chr22.vcf.gz")
    p.add_argument("--skip-genotypes", action="store_true")
    args = p.parse_args()
    load_samples(args.panel)
    load_genes(args.genes)
    build_variant_genes()
    if not args.skip_genotypes:
        ingest_genotypes(args.vcf)
