from app.ingestion.clinvar_parser import parse_line, significance_class

COLS = ["chrom", "pos", "ref", "alt", "variation_id", "allele_id", "rsid", "gene",
        "clinical_significance", "significance_class", "is_pathogenic", "review_status",
        "review_stars", "conditions", "variant_class", "consequences", "origin"]

PATHOGENIC = ("22\t29091857\t12345\tG\tA\t.\t.\t"
              "ALLELEID=27394;CLNDN=Hereditary_cancer-predisposing_syndrome|Li-Fraumeni_syndrome_2;"
              "CLNREVSTAT=criteria_provided,_multiple_submitters,_no_conflicts;"
              "CLNSIG=Pathogenic/Likely_pathogenic;CLNVC=single_nucleotide_variant;"
              "GENEINFO=CHEK2:11200;MC=SO:0001587|nonsense,SO:0001627|intron_variant;ORIGIN=1;RS=137853007\n")


def row(line):
    [r] = parse_line(line)
    return dict(zip(COLS, r))


def test_pathogenic_record():
    r = row(PATHOGENIC)
    assert r["rsid"] == "rs137853007" and r["gene"] == "CHEK2"
    assert r["clinical_significance"] == "Pathogenic/Likely pathogenic"
    assert r["is_pathogenic"] and r["significance_class"] == "pathogenic"
    assert r["review_status"] == "criteria provided, multiple submitters, no conflicts"
    assert r["review_stars"] == 2
    assert r["conditions"] == "Hereditary cancer-predisposing syndrome|Li-Fraumeni syndrome 2"
    assert r["consequences"] == "nonsense,intron_variant"
    assert r["origin"] == 1


def test_conflicting_is_not_pathogenic():
    # "pathogenicity" contains "pathogenic" — a classic LIKE '%pathogenic%' trap
    r = row(PATHOGENIC.replace("CLNSIG=Pathogenic/Likely_pathogenic",
                               "CLNSIG=Conflicting_classifications_of_pathogenicity"))
    assert not r["is_pathogenic"] and r["significance_class"] == "conflicting"


def test_alleles_normalized_like_1000g():
    line = "22\t100\t1\tCG\tTG\t.\t.\tALLELEID=1;CLNSIG=Benign\n"
    r = row(line)
    assert (r["pos"], r["ref"], r["alt"]) == (100, "C", "T")


def test_missing_alt_skipped():
    assert parse_line("22\t100\t1\tC\t.\t.\t.\tALLELEID=1\n") == []


def test_significance_class_modifiers():
    assert significance_class("Likely benign|other") == "likely_benign"
    assert significance_class("Pathogenic, low penetrance") == "pathogenic"
    assert significance_class("drug response") == "other"
    assert significance_class(None) == "other"
