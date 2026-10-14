import gzip

from app.ingestion.genotype_loader import carriers
from app.ingestion.reference_loader import POPULATIONS, parse_gff_genes


def test_carriers_counts_copies_of_the_requested_allele():
    samples = ["A", "B", "C", "D", "E"]
    gts = ["0|0", "0|1", "1|1", "2|1", "2|2"]
    assert carriers(gts, samples, 1) == [("B", 1), ("C", 2), ("D", 1)]
    assert carriers(gts, samples, 2) == [("D", 1), ("E", 2)]


def test_carriers_handles_unphased_and_extra_format_fields():
    assert carriers(["0/1:35", "1/1:20"], ["A", "B"], 1) == [("A", 1), ("B", 2)]


def test_parse_gff_keeps_gene_level_features_only(tmp_path):
    gff = tmp_path / "g.gff3.gz"
    with gzip.open(gff, "wt") as fh:
        fh.write("##gff-version 3\n")
        fh.write("22\tensembl_havana\tgene\t36649117\t36663576\t.\t+\t.\t"
                 "ID=gene:ENSG00000100342;Name=APOL1;biotype=protein_coding;"
                 "description=apolipoprotein L1 %5BSource:HGNC Symbol%3BAcc:618%5D;gene_id=ENSG00000100342\n")
        fh.write("22\thavana\tmRNA\t36649117\t36663576\t.\t+\t.\tID=transcript:ENST1;Parent=gene:ENSG00000100342\n")
    [row] = parse_gff_genes(str(gff))
    assert row[:7] == ("ENSG00000100342", "APOL1", "protein_coding", "22", 36649117, 36663576, "+")
    assert row[7] == "apolipoprotein L1 [Source:HGNC Symbol;Acc:618]"


def test_population_table_is_complete():
    assert len(POPULATIONS) == 26
    assert {sp for _, sp in POPULATIONS.values()} == {"AFR", "AMR", "EAS", "EUR", "SAS"}
