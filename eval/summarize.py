"""
Compare eval runs side by side (markdown), with McNemar tests between pairs.

    python -m eval.summarize                       # all eval/results/*.jsonl
    python -m eval.summarize run_a run_b           # specific tags
"""
import json
import statistics
import sys
from math import comb
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def load(tag):
    return [json.loads(l) for l in (RESULTS / f"{tag}.jsonl").read_text(encoding="utf-8").splitlines() if l]


def row(tag, recs):
    ans = [r for r in recs if r["answerable"]]
    una = [r for r in recs if not r["answerable"]]
    pct = lambda xs, k: 100 * sum(r[k] for r in xs) / len(xs) if xs else float("nan")
    lat = statistics.median(r["latency_ms"] for r in recs) / 1000
    toks = statistics.mean(r["prompt_tokens"] + r["output_tokens"] for r in recs)
    # "Silent wrong": the user receives a result table that is wrong (answerable)
    # or any result table at all (unanswerable). Errors and abstentions are visible.
    silent = sum(r["status"] == "ok" and not r["relaxed"] for r in ans) + sum(r["status"] == "ok" for r in una)
    return (f"| {tag} | {pct(ans, 'relaxed'):.1f} | {pct(ans, 'strict'):.1f} | {pct(una, 'strict'):.0f} "
            f"| {sum(r['status'] == 'abstained' for r in ans)} "
            f"| {sum(r['status'] in ('invalid', 'db_error') for r in ans)} "
            f"| {100 * silent / len(recs):.0f} | {lat:.1f} | {toks:,.0f} |")


def mcnemar(a, b):
    """Exact two-sided McNemar p-value on paired correctness (relaxed)."""
    pa = {r["id"]: r["relaxed"] for r in a}
    pb = {r["id"]: r["relaxed"] for r in b}
    ids = pa.keys() & pb.keys()
    only_a = sum(pa[i] and not pb[i] for i in ids)
    only_b = sum(pb[i] and not pa[i] for i in ids)
    n = only_a + only_b
    if n == 0:
        return only_a, only_b, 1.0
    k = min(only_a, only_b)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)
    return only_a, only_b, p


def main():
    tags = sys.argv[1:] or sorted(p.stem for p in RESULTS.glob("*.jsonl"))
    runs = {t: load(t) for t in tags}
    print("| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |")
    print("|---|---|---|---|---|---|---|---|---|")
    for t, recs in runs.items():
        print(row(t, recs))

    cats = sorted({r["category"] for recs in runs.values() for r in recs})
    print("\n| category | " + " | ".join(tags) + " |")
    print("|---|" + "---|" * len(tags))
    for c in cats:
        cells = []
        for recs in runs.values():
            rs = [r for r in recs if r["category"] == c]
            cells.append(f"{sum(r['relaxed'] for r in rs)}/{len(rs)}")
        print(f"| {c} | " + " | ".join(cells) + " |")

    if len(tags) > 1:
        print("\nPaired McNemar (relaxed): wins A only / wins B only / p")
        for i, a in enumerate(tags):
            for b in tags[i + 1:]:
                wa, wb, p = mcnemar(runs[a], runs[b])
                print(f"  {a} vs {b}: {wa} / {wb} / p={p:.3f}")


if __name__ == "__main__":
    main()
