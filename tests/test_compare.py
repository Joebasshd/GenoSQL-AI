from eval.compare import results_match


def test_identical():
    assert results_match([(1, "a")], [(1, "a")]) == {"strict": True, "relaxed": True}


def test_column_order_and_row_order_ignored():
    gold = [(1, "a"), (2, "b")]
    pred = [("b", 2), ("a", 1)]
    assert results_match(gold, pred) == {"strict": True, "relaxed": True}


def test_extra_columns_only_relaxed():
    gold = [(16050075,), (16050115,)]
    pred = [("22", 16050075, "A"), ("22", 16050115, "G")]
    assert results_match(gold, pred) == {"strict": False, "relaxed": True}


def test_float_tolerance():
    assert results_match([(0.123456789,)], [(0.12345,)])["strict"]


def test_wrong_count():
    assert results_match([(10,)], [(11,)]) == {"strict": False, "relaxed": False}


def test_ordered_requires_order():
    gold = [(1,), (2,)]
    assert not results_match(gold, [(2,), (1,)], ordered=True)["strict"]
    assert results_match(gold, [(1,), (2,)], ordered=True)["strict"]


def test_row_alignment_matters_not_just_columns():
    gold = [(1, "a"), (2, "b")]
    pred = [(1, "b"), (2, "a")]          # same column multisets, wrong pairing
    assert not results_match(gold, pred)["relaxed"]
