"""
Download the small reference files used by Phase 1 (< 1 MB total).

    python -m scripts.fetch_reference_data

- 1000 Genomes phase 3 sample panel (sample → population, super-population, sex)
- Ensembl GRCh37 release 87 gene annotation, chromosome 22 (GFF3)
"""
from pathlib import Path

import httpx

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"

FILES = {
    "integrated_call_samples_v3.20130502.ALL.panel":
        "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/integrated_call_samples_v3.20130502.ALL.panel",
    "Homo_sapiens.GRCh37.87.chromosome.22.gff3.gz":
        "https://ftp.ensembl.org/pub/grch37/release-87/gff3/homo_sapiens/Homo_sapiens.GRCh37.87.chromosome.22.gff3.gz",
}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=600) as client:
        for name, url in FILES.items():
            dest = OUT_DIR / name
            if dest.exists():
                print(f"exists: {dest.name}")
                continue
            r = client.get(url)
            r.raise_for_status()
            dest.write_bytes(r.content)
            print(f"downloaded: {dest.name} ({len(r.content):,} bytes)")


if __name__ == "__main__":
    main()
