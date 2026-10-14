import json
from app.ingestion.vcf_parser import parse_info, parse_line

GENOTYPES = "\tGT" + "\t0|0" * 5

SNP = ("22\t16050115\trs587755077\tG\tA\t100\tPASS\t"
       "AC=32;AF=0.00638978;AN=5008;NS=2504;DP=11468;EAS_AF=0;AMR_AF=0.0014;"
       "AFR_AF=0.0234;EUR_AF=0;SAS_AF=0;AA=.|||;VT=SNP" + GENOTYPES + "\n")

MULTI = ("22\t16051453\t.\tA\tC,G\t100\tPASS\t"
         "AC=478,17;AF=0.0954473,0.00339457;AN=5008;NS=2504;DP=22548;"
         "EAS_AF=0.0744,0;AMR_AF=0.1239,0;AFR_AF=0.003,0;EUR_AF=0.0746,0.003;"
         "SAS_AF=0.2434,0.0143;AA=.|||;VT=SNP;MULTI_ALLELIC" + GENOTYPES + "\n")

SV = ("22\t16050654\tesv3647175\tA\t<CN0>\t100\tPASS\t"
      "AC=9;AF=0.00179712;AN=5008;CS=DUP_gs;END=16063474;NS=2504;SVTYPE=CNV;"
      "DP=22545;EAS_AF=0.001;AMR_AF=0;AFR_AF=0.0061;EUR_AF=0;SAS_AF=0;VT=SV" + GENOTYPES + "\n")

COLS = ["chrom", "pos", "end_pos", "rsid", "ref", "alt", "variant_type", "sv_type",
        "is_multiallelic", "quality", "filter", "ac", "an", "af", "afr_af", "amr_af",
        "eas_af", "eur_af", "sas_af", "dp", "info"]


def as_dicts(line):
    return [dict(zip(COLS, row)) for row in parse_line(line)]


def test_parse_info_flags_and_lists():
    assert parse_info("AC=1,2;DB;AF=0.5") == {"AC": [1, 2], "DB": True, "AF": [0.5]}


def test_snp_promotes_fields_and_ignores_genotypes():
    [row] = as_dicts(SNP)
    assert row["rsid"] == "rs587755077"
    assert row["ac"] == 32 and row["an"] == 5008
    assert row["af"] == 0.00638978 and row["afr_af"] == 0.0234
    assert row["variant_type"] == "SNP" and row["end_pos"] == 16050115
    assert json.loads(row["info"]) == {"NS": 2504, "AA": ".|||"}


def test_multiallelic_split_aligns_per_allele_values():
    a, b = as_dicts(MULTI)
    assert (a["alt"], a["ac"], a["sas_af"]) == ("C", 478, 0.2434)
    assert (b["alt"], b["ac"], b["sas_af"]) == ("G", 17, 0.0143)
    assert a["is_multiallelic"] and b["is_multiallelic"]
    assert a["rsid"] is None


def test_variant_type_from_alleles_not_vt_order():
    # Real 1000G record: VT order does not follow ALT order
    line = ("22\t18217968\t.\tA\tAT,T\t100\tPASS\t"
            "AC=36,5;AF=0.0072,0.001;AN=5008;VT=SNP,INDEL;MULTI_ALLELIC" + GENOTYPES + "\n")
    ins, snp = as_dicts(line)
    assert (ins["alt"], ins["variant_type"]) == ("AT", "INDEL")
    assert (snp["alt"], snp["variant_type"]) == ("T", "SNP")


def test_split_alleles_are_normalized():
    # REF=CG, ALT=TG,C  ->  C>T at the same pos, and a 1-base deletion CG>C
    line = ("22\t20000000\t.\tCG\tTG,C\t100\tPASS\t"
            "AC=5,7;AF=0.001,0.0014;AN=5008;VT=SNP,INDEL;MULTI_ALLELIC" + GENOTYPES + "\n")
    snp, dele = as_dicts(line)
    assert (snp["pos"], snp["ref"], snp["alt"], snp["variant_type"], snp["end_pos"]) == (20000000, "C", "T", "SNP", 20000000)
    assert (dele["pos"], dele["ref"], dele["alt"], dele["variant_type"]) == (20000000, "CG", "C", "INDEL")


def test_normalize_leading_trim_shifts_pos():
    from app.ingestion.vcf_parser import normalize
    assert normalize(100, "ACGT", "ACTT") == (102, "G", "T")
    assert normalize(100, "A", "<CN0>") == (100, "A", "<CN0>")


def test_structural_variant_end_and_type():
    [row] = as_dicts(SV)
    assert row["variant_type"] == "SV" and row["sv_type"] == "CNV"
    assert row["end_pos"] == 16063474
