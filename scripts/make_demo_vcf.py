"""
Build a small demo cohort VCF from the local 1000 Genomes chr22 file, shaped like a
typical lab VCF: 'chr' contig names, a GRCh37 contig header, no population frequencies
in INFO, and simulated per-sample DP/GQ and FILTER/QUAL values.

    python -m scripts.make_demo_vcf                 # data/demo/demo_cohort.vcf.gz

The genotypes are real public data; quality fields are synthetic (seeded).
"""
import argparse
import gzip
import random
from pathlib import Path

SOURCE = "data/chr22.vcf.gz"
OUT = Path("data/demo/demo_cohort.vcf.gz")
START, END = 19_900_000, 20_100_000     # includes COMT


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=12)
    args = p.parse_args()
    rng = random.Random(22)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    kept = 0
    with gzip.open(SOURCE, "rt") as src, gzip.open(OUT, "wt") as out:
        columns = None
        for line in src:
            if line.startswith("##"):
                continue
            if line.startswith("#CHROM"):
                header = line.rstrip("\n").split("\t")
                columns = sorted(rng.sample(range(9, len(header)), args.samples))
                out.write("##fileformat=VCFv4.2\n")
                out.write("##reference=GRCh37\n")
                out.write("##contig=<ID=chr22,length=51304566>\n")
                out.write('##INFO=<ID=DP,Number=1,Type=Integer,Description="Total read depth">\n')
                out.write('##INFO=<ID=MQ,Number=1,Type=Float,Description="Mean mapping quality">\n')
                out.write('##FILTER=<ID=LowQual,Description="Low quality">\n')
                out.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
                out.write('##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n')
                out.write('##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n')
                out.write("\t".join(header[:9] + [header[c] for c in columns]) + "\n")
                continue
            head = line[:80].split("\t")
            pos = int(head[1])
            if pos < START:
                continue
            if pos > END:
                break
            f = line.rstrip("\n").split("\t")
            gts = [f[c] for c in columns]
            if not any(a not in ("0", "|", "/") for gt in gts for a in gt):
                continue      # monomorphic in this cohort
            depths = [rng.randint(8, 60) for _ in gts]
            cells = [f"{gt.replace('|', '/')}:{d}:{min(99, d * 2 + rng.randint(0, 30))}"
                     for gt, d in zip(gts, depths)]
            qual = round(rng.uniform(20, 3000), 1)
            filt = "LowQual" if rng.random() < 0.08 else "PASS"
            info = f"DP={sum(depths)};MQ={round(rng.uniform(40, 60), 1)}"
            out.write("\t".join([f"chr{f[0]}", f[1], ".", f[3], f[4], str(qual), filt, info, "GT:DP:GQ"] + cells) + "\n")
            kept += 1
    print(f"{OUT}: {kept:,} variants, {args.samples} samples")


if __name__ == "__main__":
    main()
