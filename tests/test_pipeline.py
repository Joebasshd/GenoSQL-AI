import app.nl_query.text_to_sql as t2s
from app.nl_query.llm import LLMResponse
from app.nl_query.text_to_sql import PipelineConfig, answer_question


def stub(monkeypatch, replies):
    seen = []
    it = iter(replies)

    def chat(messages, model=None, backend=None):
        seen.append(messages[-1]["content"])
        return LLMResponse(next(it), "stub", 10, 5, 1)

    monkeypatch.setattr(t2s, "chat", chat)
    return seen


CFG = dict(use_rag=False, max_repairs=2)


def test_first_abstention_is_challenged_then_answer_accepted(monkeypatch):
    seen = stub(monkeypatch, ["CANNOT_ANSWER: no population data",
                              "SELECT COUNT(*) FROM populations WHERE super_population = 'SAS';"])
    out = answer_question("How many SAS populations?", PipelineConfig(**CFG), execute=False)
    assert out["status"] == "ok" and out["attempts"] == 2
    assert "Check again" in seen[1] and "populations" in seen[1]


def test_second_abstention_is_accepted(monkeypatch):
    stub(monkeypatch, ["CANNOT_ANSWER: no age data", "CANNOT_ANSWER: there is no age column"])
    out = answer_question("Age of HG00096?", PipelineConfig(**CFG), execute=False)
    assert out["status"] == "abstained" and out["error"] == "there is no age column"


def test_challenge_can_be_disabled(monkeypatch):
    stub(monkeypatch, ["CANNOT_ANSWER: no age data"])
    out = answer_question("Age?", PipelineConfig(verify_abstain=False, **CFG), execute=False)
    assert out["status"] == "abstained" and out["attempts"] == 1


def test_no_challenge_when_no_attempts_left(monkeypatch):
    stub(monkeypatch, ["CANNOT_ANSWER: no age data"])
    out = answer_question("Age?", PipelineConfig(use_rag=False, max_repairs=0), execute=False)
    assert out["status"] == "abstained"


def test_grounded_abstention_from_schema_misses(monkeypatch):
    stub(monkeypatch, ["SELECT yri_af FROM variants;"] * 3)
    out = answer_question("YRI frequency?", PipelineConfig(allow_abstain=False, grounded_abstain=True, **CFG),
                          execute=False)
    assert out["status"] == "abstained" and "yri_af" in out["error"] and out["attempts"] == 3


def test_grounded_abstention_ignores_other_invalid_sql(monkeypatch):
    stub(monkeypatch, ["SELECT COUNT(*) FROM variants WHERE variant_type = 'INS';"] * 3)
    out = answer_question("insertions?", PipelineConfig(allow_abstain=False, grounded_abstain=True, **CFG),
                          execute=False)
    assert out["status"] == "invalid"     # a fixable value error is not "missing data"


def test_spontaneous_marker_counts_as_abstention_in_grounded_mode(monkeypatch):
    stub(monkeypatch, ["CANNOT_ANSWER;", "CANNOT_ANSWER: no age column"])
    out = answer_question("Age?", PipelineConfig(allow_abstain=False, grounded_abstain=True, **CFG),
                          execute=False)
    assert out["status"] == "abstained"


def test_placeholder_abstain_reason_replaced():
    from app.nl_query.text_to_sql import _abstain_reason
    assert _abstain_reason("CANNOT_ANSWER: <missing information>").startswith("The model judged")
    assert _abstain_reason("CANNOT_ANSWER;").startswith("The model judged")
    assert _abstain_reason("CANNOT_ANSWER: no expression data") == "no expression data"
