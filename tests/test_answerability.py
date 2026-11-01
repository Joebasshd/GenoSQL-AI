import pytest

import app.nl_query.text_to_sql as t2s
from app.datasets import build_view
from app.nl_query.answerability import ignored_entity, missing_concept
from app.nl_query.llm import LLMResponse
from app.nl_query.schema import REFERENCE_SCHEMA
from app.nl_query.text_to_sql import PipelineConfig, answer_question


def upload_view(annotated=True):
    return build_view(1, {
        "build": "GRCh37", "n_samples": 12, "sample_examples": ["A"], "annotated": annotated,
        "chromosomes": [["22", 100]], "variant_types": [["SNP", 100]], "sv_types": [], "filters": [],
        "pos_range": [1, 100], "quality_range": [None, None], "info_keys": [],
        "has": {"rsid": False, "af": True, "ac": True, "dp": False, "multiallelic": False,
                "genotype_gq": False, "genotype_dp": False, "genotypes": True},
    })


@pytest.mark.parametrize("question", [
    "What disease does sample HG00259 have?",
    "What is the age of sample NA18609?",
    "What is the gnomAD frequency of the variant at 19902526?",
    "Which of these samples are of African ancestry?",
    "How many women are in this dataset?",
    "Which genes are most highly expressed in kidney?",
    "What does AlphaFold say about this variant?",
])
def test_upload_declines_what_it_does_not_hold(question):
    assert missing_concept(question, upload_view()) is not None


@pytest.mark.parametrize("question", [
    "How many variants are in this dataset?",
    "How many ClinVar benign variants are in this dataset?",
    "Which sample carries the most variants?",
    "How many variants have an average mapping quality above 55?",
    "How many ClinVar variants are associated with schizophrenia?",
    "What conditions does ClinVar list for rs4680?",
    "What is the coverage at this position?",        # 'age' inside a word
])
def test_upload_answers_what_it_holds(question):
    assert missing_concept(question, upload_view()) is None


@pytest.mark.parametrize("question", [
    "How many variants are private to African populations?",
    "How many female samples are in the African super-population?",
])
def test_reference_has_population_and_sex(question):
    assert missing_concept(question, REFERENCE_SCHEMA) is None


def test_gate_depends_on_the_dataset():
    question = "How many variants are common in Europeans?"
    assert missing_concept(question, REFERENCE_SCHEMA) is None
    assert "population" in missing_concept(question, upload_view())


def test_clinical_exception_needs_clinvar_table():
    question = "What phenotypes does ClinVar list for this variant?"
    assert missing_concept(question, upload_view(annotated=True)) is None
    assert missing_concept(question, upload_view(annotated=False)) is not None


def test_ignored_entity():
    assert ignored_entity("Age of NA18609?", "SELECT sample_id FROM samples;") == "NA18609"
    assert ignored_entity("Frequency of rs4680?", "SELECT af FROM v WHERE rsid = 'rs4680';") is None
    assert ignored_entity("How many variants?", "SELECT COUNT(*) FROM variants;") is None


def stub(monkeypatch, replies):
    calls = []
    it = iter(replies)

    def chat(messages, model=None, backend=None):
        calls.append(messages[-1]["content"])
        return LLMResponse(next(it), "stub", 1, 1, 1)

    monkeypatch.setattr(t2s, "chat", chat)
    return calls


def test_gate_runs_before_the_model(monkeypatch):
    calls = stub(monkeypatch, [])
    out = answer_question("What is the age of NA18609?", PipelineConfig(use_rag=False, schema=upload_view()), execute=False)
    assert out["status"] == "abstained" and "age" in out["error"] and calls == []
    assert out["latency_ms"] >= 0


def test_gate_can_be_disabled(monkeypatch):
    stub(monkeypatch, ["SELECT sample_id FROM samples;"])
    cfg = PipelineConfig(use_rag=False, schema=upload_view(), concept_gate=False, entity_check=False, scope_gate=False)
    assert answer_question("What is the age of NA18609?", cfg, execute=False)["status"] == "ok"


def test_ignored_entity_triggers_repair(monkeypatch):
    calls = stub(monkeypatch, ["SELECT COUNT(*) FROM genotypes;",
                               "SELECT COUNT(*) FROM genotypes WHERE sample_id = 'HG00451';"])
    out = answer_question("How many variants does HG00451 carry?",
                          PipelineConfig(use_rag=False, schema=upload_view()), execute=False)
    assert out["status"] == "ok" and out["attempts"] == 2
    assert "does not use 'HG00451'" in calls[1]


def test_single_person_file_hides_computed_frequency():
    view = build_view(1, {
        "build": "GRCh37", "n_samples": 1, "sample_examples": ["A"], "annotated": False,
        "chromosomes": [["22", 100]], "variant_types": [["SNP", 100]], "sv_types": [], "filters": [],
        "pos_range": [1, 100], "quality_range": [None, None], "info_keys": [],
        "has": {"rsid": False, "af": True, "ac": True, "dp": False, "multiallelic": False,
                "genotype_gq": False, "genotype_dp": False, "genotypes": True},
    })
    assert not {"af", "ac", "an"} & view.table_columns["variants"]
    assert "Rare" not in view.notes


def test_multiple_submitters_is_review_status_not_a_submitter_question():
    question = "Which ClinVar variants have review status criteria provided, multiple submitters, no conflicts?"
    assert missing_concept(question, upload_view()) is None
    assert missing_concept("Who submitted this ClinVar record?", upload_view()) is not None
