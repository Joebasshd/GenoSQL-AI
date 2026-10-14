import json

import eval.build_benchmark as bb


def make(cat, n, start=1):
    return [{"id": f"{cat[:3].upper()}{i:02d}", "category": cat, "question": f"{cat} q{i}",
             "gold_sql": f"SELECT {i};"} for i in range(start, start + n)]


def test_split_is_stratified_and_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "SPLITS", tmp_path / "splits.json")

    items = make("population", 10) + make("sv", 5)
    first = bb.assign_splits(items)
    bb.SPLITS.write_text(json.dumps(first))
    assert sum(v["split"] == "dev" for k, v in first.items() if k.startswith("POP")) == 4
    assert sum(v["split"] == "dev" for k, v in first.items() if k.startswith("SV")) == 2

    # Adding items must not move any existing assignment
    grown = items + make("population", 5, start=11) + make("indel", 3)
    second = bb.assign_splits(grown)
    assert all(second[k]["split"] == first[k]["split"] for k in first)
    assert sum(v["split"] == "dev" for k, v in second.items() if k.startswith("POP")) == 6


def test_edited_question_keeps_split(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bb, "SPLITS", tmp_path / "splits.json")
    items = make("lookup", 3)
    first = bb.assign_splits(items)
    bb.SPLITS.write_text(json.dumps(first))
    items[0]["question"] = "reworded"
    second = bb.assign_splits(items)
    assert second["LOO01"]["split"] == first["LOO01"]["split"]
    assert "changed since it was split" in capsys.readouterr().out
