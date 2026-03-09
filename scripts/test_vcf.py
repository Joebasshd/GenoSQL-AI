import vcfpy
import gzip

reader = vcfpy.Reader.from_stream(
    gzip.open("data/chr22.vcf.gz", "rt")
)

for i, record in enumerate(reader):

    print(
        record.CHROM,
        record.POS,
        record.ID,
        record.REF,
        record.ALT
    )

    if i == 5:
        break