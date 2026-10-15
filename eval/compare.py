"""
Execution-accuracy comparison between a gold and a predicted result set.

strict : same number of columns; rows equal as a multiset (column order may differ)
relaxed: every gold column is matched by some predicted column; extra predicted
         columns are allowed (e.g. the model also returned rsid)
Row order is ignored unless `ordered=True` (questions that ask for a ranking).
relaxed also accepts the same numbers in long vs wide layout, e.g. one row per
genotype class [(1, 242), (2, 53)] vs a single row (242, 53).
"""
from collections import Counter
from decimal import Decimal
from itertools import permutations


def _norm(v):
    if isinstance(v, (float, Decimal)):
        f = float(v)
        return round(f, 4) if abs(f) < 1e6 else round(f)
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v
    return str(v)


def _rows(rows):
    return [tuple(_norm(v) for v in r) for r in rows]


def _column(rows, j):
    return Counter(r[j] for r in rows)


def _match_columns(gold, pred, n_gold, n_pred):
    """Map each gold column to a distinct pred column with the same value multiset."""
    candidates = [
        [k for k in range(n_pred) if _column(pred, k) == _column(gold, j)]
        for j in range(n_gold)
    ]
    if any(not c for c in candidates):
        return None
    # Small search: column counts are tiny in practice.
    def search(j, used):
        if j == n_gold:
            return []
        for k in candidates[j]:
            if k not in used:
                rest = search(j + 1, used | {k})
                if rest is not None:
                    return [k] + rest
        return None
    return search(0, frozenset())


def results_match(gold_rows, pred_rows, ordered: bool = False) -> dict:
    gold, pred = _rows(gold_rows), _rows(pred_rows)
    if len(gold) != len(pred):
        return {"strict": False, "relaxed": _pivot_equal(gold, pred)}
    if not gold:
        return {"strict": True, "relaxed": True}

    n_gold, n_pred = len(gold[0]), len(pred[0])
    if n_pred < n_gold:
        return {"strict": False, "relaxed": False}

    mapping = _match_columns(gold, pred, n_gold, n_pred)
    if mapping is None and n_gold == 1 and n_pred == 1:
        return {"strict": False, "relaxed": False}
    if mapping is None:
        # Value multisets can coincide across columns; fall back to brute force for tiny widths.
        if n_pred > 6:
            return {"strict": False, "relaxed": False}
        mapping = next((list(p) for p in permutations(range(n_pred), n_gold)
                        if _project(pred, list(p), ordered) == _project(gold, list(range(n_gold)), ordered)),
                       None)
        if mapping is None:
            return {"strict": False, "relaxed": False}

    ok = _project(pred, mapping, ordered) == _project(gold, list(range(n_gold)), ordered)
    return {"strict": ok and n_pred == n_gold, "relaxed": ok}


def _pivot_equal(a, b):
    """One side is a single row of k >= 2 values; the other has k rows, one column of which holds those values."""
    if len(a) != 1:
        a, b = b, a
    if len(a) != 1 or not b or len(a[0]) < 2 or len(a[0]) != len(b):
        return False
    wide = Counter(a[0])
    return any(Counter(r[j] for r in b) == wide for j in range(len(b[0])))


def _project(rows, cols, ordered):
    projected = [tuple(r[c] for c in cols) for r in rows]
    return projected if ordered else Counter(projected)
