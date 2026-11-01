import json

import pytest

from app.ingestion.user_vcf import (VcfError, VcfHeader, detect_build, normalize_chrom, parse_genotypes,
                                    parse_record, read_header)

HEADER = ('##fileformat=VCFv4.2\n'
          '##reference=GRCh38\n'
          '##contig=<ID=chr1,length=248956422>\n'
          '##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
          '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence">\n'
          '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
          '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tA\tB\tC\n')


def header(samples=("A", "B", "C")):
    return VcfHeader(samples=list(samples))


def test_read_header_and_build(tmp_path):
    path = tmp_path / "x.vcf"
    path.write_text(HEADER)
    h = read_header(str(path))
    assert h.samples == ["A", "B", "C"]
    assert h.contigs == {"1": 248956422}
    assert "CSQ" in h.info
    assert detect_build(h) == "GRCh38"


def test_rejects_non_vcf(tmp_path):
    path = tmp_path / "x.vcf"
    path.write_text("gene,score\nAPOL1,3\n")
    with pytest.raises(VcfError):
        read_header(str(path))


@pytest.mark.parametrize("contigs, reference, build", [
    ({"22": 51304566}, "", "GRCh37"),
    ({}, "file:///ref/hs37d5.fa", "GRCh37"),
    ({}, "hg38.fa", "GRCh38"),
    ({}, "", "unknown"),
])
def test_detect_build(contigs, reference, build):
    assert detect_build(VcfHeader(contigs=contigs, reference=reference)) == build


def test_normalize_chrom():
    assert [normalize_chrom(c) for c in ("chr22", "CHRX", "22", "chrM", "MT")] == ["22", "X", "22", "MT", "MT"]


def test_genotypes_keep_quality_and_depth():
    called, carriers = parse_genotypes("GT:DP:GQ", ["0/1:30:99", "1|1:12:50", "0/0:20:90"], ["A", "B", "C"], 1)
    assert called == 6
    assert carriers == {1: [("A", 1, "0/1", 99, 30), ("B", 2, "1|1", 50, 12)]}


def test_genotypes_missing_and_haploid():
    called, carriers = parse_genotypes("GT", ["./.", "1", ".|1"], ["A", "B", "C"], 1)
    assert called == 2
    assert carriers[1] == [("B", 1, "1", None, None), ("C", 1, ".|1", None, None)]


def test_multiallelic_het_counts_each_allele():
    _, carriers = parse_genotypes("GT", ["1/2", "2/2"], ["A", "B"], 2)
    assert carriers[1] == [("A", 1, "1/2", None, None)]
    assert carriers[2] == [("A", 1, "1/2", None, None), ("B", 2, "2/2", None, None)]


def test_record_computes_frequency_from_genotypes_when_info_lacks_it():
    line = "chr1\t100\trs1\tA\tG\t50.5\tPASS\tDP=60;CSQ=x\tGT\t0/1\t1/1\t0/0\n"
    [allele] = parse_record(line, header())
    row = allele["row"]
    assert row[:7] == ["1", 100, 100, "rs1", "A", "G", "SNP"]
    assert row[9:15] == [50.5, "PASS", 3, 6, 0.5, 60]
    assert json.loads(row[15]) == {"CSQ": "x"}
    assert [c[0] for c in allele["carriers"]] == ["A", "B"]


def test_record_prefers_info_frequencies_and_splits_alleles():
    line = "1\t100\t.\tCG\tTG,C\t.\t.\tAC=5,7;AF=0.1,0.2;AN=50\tGT\t0/1\t0/2\t0/0\n"
    snp, deletion = parse_record(line, header())
    assert (snp["row"][4], snp["row"][5], snp["row"][6], snp["row"][11], snp["row"][13]) == ("C", "T", "SNP", 5, 0.1)
    assert (deletion["row"][6], deletion["row"][11], deletion["row"][13]) == ("INDEL", 7, 0.2)
    assert snp["row"][8] is True and snp["row"][3] is None


def test_sites_only_vcf_and_no_alt():
    [allele] = parse_record("2\t5\t.\tA\tT\t10\tPASS\t.\n", header(()))
    assert allele["carriers"] == [] and allele["row"][11] is None
    assert parse_record("2\t5\t.\tA\t.\t10\tPASS\t.\n", header(())) == []


def test_structural_variant_end():
    [allele] = parse_record("3\t10\t.\tA\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=500\n", header(()))
    assert allele["row"][2] == 500 and allele["row"][6] == "SV" and allele["row"][7] == "DEL"


def test_malformed_line_raises():
    with pytest.raises(VcfError):
        parse_record("chr1\t100\n", header())
