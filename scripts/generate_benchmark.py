"""
Phase 2 benchmark generation with two independent Gemini annotators.

    python -m scripts.generate_benchmark run                  # generate + review every category to its target
    python -m scripts.generate_benchmark run --only carrier,join
    python -m scripts.generate_benchmark status
    python -m scripts.generate_benchmark recheck              # re-score disputes after comparator fixes
    python -m scripts.generate_benchmark audit                # flag near-seed and ambiguous-region items
    python -m scripts.generate_benchmark adjudicate           # apply eval/generation/adjudication.jsonl

Protocol
1. Author model gets the schema documentation and a data card of real values and
   proposes questions with gold SQL (+ a lay paraphrase).
2. Each proposal must pass the SQL guard, execute, return a small non-trivial
   result, and not near-duplicate an existing question.
3. Reviewer model independently writes SQL from the question alone.
4. Both queries are executed; matching results = agreement -> accepted.
   Otherwise the item goes to the adjudication queue.

Accepted items are appended to eval/generated_items.jsonl, which
eval/build_benchmark.py appends after the hand-written items.
"""
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx
import numpy as np
import sqlglot
from dotenv import load_dotenv
from sqlglot import exp

from app.database import run_readonly
from app.nl_query.schema import schema_prompt
from app.nl_query.sql_guard import clean_sql, validate_sql
from app.rag.embeddings import embed_text
from eval.compare import results_match

ROOT = Path(__file__).resolve().parent.parent
GEN_DIR = ROOT / "eval" / "generation"
CACHE_DIR = GEN_DIR / "cache"
CANDIDATES = GEN_DIR / "candidates.jsonl"
ADJUDICATION = GEN_DIR / "adjudication.jsonl"
AUDIT = GEN_DIR / "audit.jsonl"
ACCEPTED = ROOT / "eval" / "generated_items.jsonl"
BENCHMARK = ROOT / "eval" / "benchmark.jsonl"

load_dotenv(ROOT / ".env")
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Free tier allows ~20 requests/day per model, so each role has fallbacks.
# The two lists must never share a model: author and reviewer stay independent.
AUTHOR_MODELS = os.getenv("BENCH_AUTHOR_MODELS", "gemini-3.8-flash,gemini-3.5-flash,gemini-3.1-flash-lite,gemini-3.5-flash-lite").split(",")
REVIEWER_MODELS = os.getenv("BENCH_REVIEWER_MODELS",
                            "gemini-3.7-flash,gemini-3-flash-preview,gemini-3.6-flash").split(",")
assert not set(AUTHOR_MODELS) & set(REVIEWER_MODELS)
MIN_CALL_INTERVAL_S = float(os.getenv("BENCH_MIN_CALL_INTERVAL_S", "10"))

MAX_ROWS = 30
DUPLICATE_SIMILARITY = 0.88
SEED_SIMILARITY = 0.80
OVERLAP_PREDICATE = re.compile(r"(\w+\.)?pos\s*<=\s*(\d+)\s+AND\s+(\w+\.)?end_pos\s*>=\s*(\d+)", re.IGNORECASE)
ORDER_WORDS = ("order", "sort", "rank", "top", "highest", "lowest", "most", "fewest", "least",
               "largest", "smallest", "longest", "shortest", "descending", "ascending", "first", "last")
BATCH_SIZE = 8
REVIEW_BATCH_SIZE = 16

# new items to add per category; ~10 % unanswerable, joins well above 20 %
CATEGORIES = {
    "lookup": (6, "Look up specific variants by position/alleles, or ClinVar variants by rsID."),
    "region": (8, "Variants within or overlapping a chr22 interval, possibly filtered by type or frequency."),
    "aggregation": (10, "Counts, averages, distributions and GROUP BY summaries over variants."),
    "frequency": (6, "Allele-frequency classes: rare, low-frequency, common, singleton, near-fixed."),
    "population": (12, "Comparisons of super-population frequencies (afr_af, eur_af, ...): private, enriched, differentiated."),
    "sv": (6, "Structural variants: types, lengths, frequencies, overlaps."),
    "indel": (6, "Insertions/deletions: lengths, counts, frequencies."),
    "json": (5, "Questions needing the info JSONB column (ancestral allele, exome target, SV call set)."),
    "multistep": (10, "Questions needing subqueries, CTEs, ranking or windowing over one or more tables."),
    "clinvar": (12, "ClinVar-only questions: significance, review stars, genes, conditions, consequences."),
    "join": (14, "ClinVar joined to 1000 Genomes frequencies (and genes): clinically relevant variants by population."),
    "gene": (10, "Genes and their coordinates, and variants within genes via variant_genes."),
    "samples": (6, "Individuals, populations, super-populations and sex."),
    "carrier": (12, "Carrier genotypes: how many / which individuals or populations carry ClinVar variants; per-population "
                    "frequency. Join path: genotypes.variant_id = variants.id, then variants to clinvar ON chrom, pos, ref "
                    "AND alt (never genotypes.variant_id = clinvar.id), and genotypes.sample_id = samples.sample_id."),
    "unanswerable": (16, "Plausible questions this database cannot answer (needs data that is absent)."),
}

_last_call = 0.0
_exhausted: set[str] = set()


# ── Gemini ───────────────────────────────────────────────────────────────

def gemini(role: str, prompt: str) -> tuple[dict | list, str]:
    """
    JSON-mode call for role 'author' or 'reviewer'. Returns (result, model used).
    Responses are cached on disk per (role, prompt), so interrupted runs resume for free.
    A daily-quota 429 moves on to the role's next model.
    """
    global _last_call
    models = AUTHOR_MODELS if role == "author" else REVIEWER_MODELS
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{hashlib.sha256((role + prompt).encode()).hexdigest()[:24]}.json"
    legacy = [CACHE_DIR / f"{hashlib.sha256((m + prompt).encode()).hexdigest()[:24]}.json" for m in models]
    for f, model in [(cache_file, None)] + list(zip(legacy, models)):
        if f.exists():
            cached = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and "model" in cached and "result" in cached:
                return cached["result"], cached["model"]
            return cached, model

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
    }
    for model in models:
        if model in _exhausted:
            continue
        for attempt in range(8):
            wait = MIN_CALL_INTERVAL_S - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.time()
            r = httpx.post(API_URL.format(model=model), json=body, timeout=300,
                           headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
            if r.status_code == 429 and "PerDay" in r.text:
                print(f"  {model}: daily quota used up, switching model")
                _exhausted.add(model)
                break
            if r.status_code in (500, 503) and attempt >= 2:
                print(f"  {model}: still overloaded, switching model")
                break
            if r.status_code in (429, 500, 503):
                backoff = min(240, 15 * 2 ** attempt)
                print(f"  {model}: HTTP {r.status_code}, retrying in {backoff}s")
                time.sleep(backoff)
                continue
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            result = json.loads(text)
            cache_file.write_text(json.dumps({"model": model, "result": result}, indent=1), encoding="utf-8")
            return result, model
    raise RuntimeError(f"all {role} models are rate limited or out of daily quota; re-run later to resume")


# ── data card: real values the author can ground questions in ──────────

def _rows(sql: str) -> list:
    return run_readonly(sql, max_rows=200, timeout_ms=120_000)["rows"]


def data_card() -> str:
    pathogenic_genes = _rows("""SELECT gene, COUNT(*) FROM clinvar WHERE is_pathogenic AND gene IS NOT NULL
                                GROUP BY gene ORDER BY COUNT(*) DESC LIMIT 25""")
    shared_rsids = _rows("""SELECT c.rsid, c.gene, c.clinical_significance, v.af
                            FROM clinvar c JOIN variants v ON v.chrom = c.chrom AND v.pos = c.pos
                                 AND v.ref = c.ref AND v.alt = c.alt
                            WHERE c.rsid IS NOT NULL AND v.af BETWEEN 0.01 AND 0.9
                            ORDER BY md5(c.rsid) LIMIT 25""")
    clinical_shared = _rows("""SELECT c.rsid, c.gene, c.clinical_significance, v.af
                               FROM clinvar c JOIN variants v ON v.chrom = c.chrom AND v.pos = c.pos
                                    AND v.ref = c.ref AND v.alt = c.alt
                               WHERE c.rsid IS NOT NULL AND v.af > 0
                                 AND (c.is_pathogenic OR c.clinical_significance ILIKE '%risk factor%'
                                      OR c.significance_class = 'conflicting')
                               ORDER BY v.af DESC LIMIT 20""")
    genes = _rows("""SELECT gene_name, start_pos, end_pos FROM genes WHERE biotype = 'protein_coding'
                     ORDER BY md5(gene_name) LIMIT 30""")
    conditions = _rows("""SELECT trim(c), COUNT(*) FROM clinvar, unnest(string_to_array(conditions, '|')) c
                          WHERE c NOT IN ('not provided', 'not specified')
                          GROUP BY 1 ORDER BY 2 DESC LIMIT 25""")
    populations = _rows("SELECT population, population_name, super_population FROM populations ORDER BY 1")
    sv_types = _rows("SELECT sv_type, COUNT(*) FROM variants WHERE sv_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    return "\n".join([
        "Data card (real values present in the database):",
        "- chrom is always '22'; positions range 16,050,075 - 51,244,237.",
        "- Genes with most pathogenic ClinVar variants: " + ", ".join(f"{g} ({n})" for g, n in pathogenic_genes),
        "- ClinVar rsIDs also present in 1000 Genomes (rsid, gene, significance, global af): "
        + "; ".join(f"{r} {g} {s} {af:.3f}" for r, g, s, af in shared_rsids),
        "- Pathogenic / risk-factor / conflicting ClinVar variants present in 1000 Genomes (rsid, gene, "
        "significance, global af): " + "; ".join(f"{r} {g} {s} {af:.4f}" for r, g, s, af in clinical_shared),
        "- Protein-coding genes (name start-end): " + ", ".join(f"{g} {s}-{e}" for g, s, e in genes),
        "- Frequent ClinVar conditions: " + "; ".join(f"{c} ({n})" for c, n in conditions),
        "- Populations: " + "; ".join(f"{p} = {n} ({sp})" for p, n, sp in populations),
        "- SV types: " + ", ".join(f"{t} ({n})" for t, n in sv_types),
        "- Genotypes exist only for the ~13,800 variants present in both ClinVar and 1000 Genomes.",
    ])


# ── prompts ──────────────────────────────────────────────────────────────

def author_prompt(category: str, description: str, n: int, card: str, existing: list[str]) -> str:
    if category == "unanswerable":
        task = (f"Write {n} realistic questions a geneticist might ask that this database CANNOT answer because the "
                "needed data is absent (e.g. gnomAD, expression, phenotypes, ages, other chromosomes' genes, "
                "protein structure, submitters, drug dosing). Make some of them tempting: close to answerable "
                "questions. Do not write questions that could be answered by joining the existing tables.\n"
                'Return a JSON array of objects: {"question": str, "question_lay": str, "missing": str, '
                '"difficulty": 1|2|3}.')
    else:
        task = (f"Write {n} diverse, unambiguous questions in category '{category}' ({description}) with a gold "
                "PostgreSQL query each.\n"
                "Requirements:\n"
                "- Use specific real values from the data card; vary entities, filters and phrasing.\n"
                "- The answer must be small: a count, an aggregate, or at most 20 rows, and must not be 0 or empty.\n"
                "- Follow the schema conventions exactly (they define words like 'rare', 'private', 'pathogenic').\n"
                "- Select only the columns that answer the question.\n"
                "- Phrase questions as a researcher would: never mention table or column names, and do not "
                "quote database codes (write 'Admixed American', 'Yoruba', 'European', not 'AMR' or 'YRI').\n"
                "- Avoid ties: for 'top N' / 'which X has the most' questions choose cases with a clear winner.\n"
                "- If the question asks for a ranking, use ORDER BY with a tie-free key and set ordered=true.\n"
                "- question_lay: the same question phrased by a biologist who does not know the schema or jargon "
                "like 'af' (same meaning, same answer).\n"
                'Return a JSON array of objects: {"question": str, "question_lay": str, "gold_sql": str, '
                '"difficulty": 1|2|3, "ordered": bool}.')
    avoid = "\n".join(f"- {q}" for q in existing) or "- (none)"
    return f"""You are building an evaluation benchmark for natural-language-to-SQL over a genomics database.

Database documentation:
{schema_prompt(semantic=True)}

{card}

{task}

Do not repeat or paraphrase these existing questions:
{avoid}"""


def reviewer_prompt(questions: list[dict], card: str) -> str:
    listing = "\n".join(f'{q["key"]}: {q["question"]}' for q in questions)
    return f"""You are an expert annotator writing reference SQL for a genomics NL-to-SQL benchmark.

Database documentation:
{schema_prompt(semantic=True)}

{card}

For each question write one PostgreSQL SELECT query that answers it exactly, following the conventions above.
Select only the columns that answer the question. If a question cannot be answered from this database,
say so instead of writing SQL. If a question is ambiguous (two reasonable readings give different answers),
flag it.

Questions:
{listing}

Return a JSON array with one object per question:
{{"key": str, "sql": str or null, "cannot_answer": str or null, "ambiguous": str or null}}"""


# ── validation ───────────────────────────────────────────────────────────

class Deduper:
    def __init__(self, questions: list[str]):
        self.vectors = [np.array(embed_text(q)) for q in questions]

    def is_duplicate(self, question: str) -> bool:
        v = np.array(embed_text(question))
        return any(float(v @ u) > DUPLICATE_SIMILARITY for u in self.vectors)

    def add(self, question: str):
        self.vectors.append(np.array(embed_text(question)))


def execute(sql: str) -> dict:
    try:
        return {"ok": True, **run_readonly(sql, max_rows=MAX_ROWS, timeout_ms=30_000)}
    except Exception as e:
        return {"ok": False, "error": str(e).strip().splitlines()[0]}


def asks_for_order(question: str) -> bool:
    q = re.sub(r"\bat (least|most)\b", "", question.lower())
    return any(re.search(rf"\b{w}", q) for w in ORDER_WORDS)


def order_key_values(sql: str, extra: int = 1) -> list | None:
    """Values of the first ORDER BY key for LIMIT n + extra rows, or None if not a LIMIT-n ranking."""
    tree = sqlglot.parse_one(sql, read="postgres")
    limit, order = tree.args.get("limit"), tree.args.get("order")
    if not (isinstance(tree, exp.Select) and limit and order):
        return None
    try:
        n = int(limit.expression.name)
    except (AttributeError, ValueError):
        return None
    first = order.expressions[0]
    key = first.this.copy()
    aliases = {a.alias.lower(): a.this for a in tree.expressions if isinstance(a, exp.Alias)}
    if isinstance(key, exp.Column) and not key.table and key.name.lower() in aliases:
        key = aliases[key.name.lower()].copy()
    probe = tree.copy()
    probe.set("expressions", [exp.alias_(key, "k")])
    probe.set("order", exp.Order(expressions=[exp.Ordered(this=key.copy(), desc=first.args.get("desc"))]))
    probe.set("limit", exp.Limit(expression=exp.Literal.number(n + extra)))
    rows = run_readonly(probe.sql(dialect="postgres"), max_rows=n + extra, timeout_ms=30_000)["rows"]
    return [r[0] for r in rows]


def tie_at_limit(sql: str) -> bool:
    """True if the first ORDER BY key has equal values at positions n and n+1 of a LIMIT n query."""
    keys = order_key_values(sql)
    if keys is None:
        return False
    n = _limit(sql)
    return len(keys) == n + 1 and keys[n - 1] == keys[n]


def ties_within(sql: str) -> bool:
    """True if two of the returned rows share the first ORDER BY key, so their relative order is arbitrary."""
    keys = order_key_values(sql, extra=0)
    return bool(keys) and len(set(keys)) < len(keys)


def _limit(sql: str) -> int:
    return int(sqlglot.parse_one(sql, read="postgres").args["limit"].expression.name)


def check_author_item(item: dict) -> tuple[str | None, dict | None]:
    """Return (rejection_reason, execution_result)."""
    sql = clean_sql(item.get("gold_sql") or "")
    item["gold_sql"] = sql
    ok, reason = validate_sql(sql)
    if not ok:
        return f"guard: {reason}", None
    item["ordered"] = bool(item.get("ordered")) and asks_for_order(item["question"])
    if item["ordered"] and "order by" not in sql.lower():
        return "ordered without ORDER BY", None
    res = execute(sql)
    if not res["ok"]:
        return f"error: {res['error']}", None
    if res["truncated"]:
        return "too many rows", None
    if not res["rows"]:
        return "empty result", None
    if len(res["rows"]) == 1 and all(v in (None, 0) for v in res["rows"][0]):
        return "trivial result (0 or NULL)", None
    try:
        if tie_at_limit(sql):
            return "tie at LIMIT boundary", None
        if item["ordered"] and ties_within(sql):
            item["ordered"] = False
    except Exception as e:
        return f"tie check failed: {str(e).splitlines()[0]}", None
    return None, res


def agree(author_rows: list, reviewer_rows: list, ordered: bool) -> bool:
    return (results_match(author_rows, reviewer_rows, ordered)["relaxed"]
            or results_match(reviewer_rows, author_rows, ordered)["relaxed"])


# ── state ────────────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def existing_questions() -> list[dict]:
    from scripts.seed_examples import SEEDS
    items = load_jsonl(BENCHMARK) + load_jsonl(ACCEPTED)
    return items + [{"question": q, "category": "seed"} for q, _ in SEEDS]


# ── pipeline ─────────────────────────────────────────────────────────────

def run_category(category: str, card: str, deduper: Deduper, max_rounds: int = 4):
    target, description = CATEGORIES[category]
    for round_no in range(1, max_rounds + 1):
        accepted = [c for c in load_jsonl(CANDIDATES) if c["category"] == category and c["status"] == "accepted"]
        if len(accepted) >= target:
            break
        need = target - len(accepted)
        ask = min(BATCH_SIZE * 2, need + max(3, need // 2))
        seen = [q["question"] for q in existing_questions() if q.get("category") == category]
        seen += [c["question"] for c in load_jsonl(CANDIDATES) if c["category"] == category]
        print(f"[{category}] round {round_no}: have {len(accepted)}/{target}, asking for {ask}")

        proposals, author_model = gemini("author", author_prompt(category, description, ask, card, seen))
        if isinstance(proposals, dict):
            proposals = next((v for v in proposals.values() if isinstance(v, list)), [])

        pending = []
        for i, item in enumerate(proposals):
            key = f"{category}-{hashlib.sha1(item.get('question', '').encode()).hexdigest()[:8]}"
            record = {"key": key, "category": category, "question": item.get("question", "").strip(),
                      "question_lay": (item.get("question_lay") or "").strip(),
                      "difficulty": item.get("difficulty", 2), "ordered": bool(item.get("ordered")),
                      "author_model": author_model}
            if not record["question"] or any(c["key"] == key for c in load_jsonl(CANDIDATES)):
                continue
            if deduper.is_duplicate(record["question"]):
                append_jsonl(CANDIDATES, {**record, "status": "rejected", "reason": "near-duplicate"})
                continue
            if category == "unanswerable":
                record.update(gold_sql=None, missing=item.get("missing"))
                pending.append((record, None))
                continue
            record["gold_sql"] = item.get("gold_sql")
            reason, res = check_author_item(record)
            if reason:
                append_jsonl(CANDIDATES, {**record, "status": "rejected", "reason": reason})
                continue
            pending.append((record, res))

        for start in range(0, len(pending), REVIEW_BATCH_SIZE):
            batch = pending[start:start + REVIEW_BATCH_SIZE]
            reviews, reviewer_model = gemini("reviewer", reviewer_prompt([r for r, _ in batch], card))
            if isinstance(reviews, dict):
                reviews = next((v for v in reviews.values() if isinstance(v, list)), [])
            by_key = {rv.get("key"): rv for rv in reviews}
            for record, author_res in batch:
                review = by_key.get(record["key"], {})
                record = review_one({**record, "reviewer_model": reviewer_model}, author_res, review)
                append_jsonl(CANDIDATES, record)
                if record["status"] in ("accepted", "disputed"):
                    deduper.add(record["question"])
                if record["status"] == "accepted":
                    publish(record)
                print(f"  {record['status']:<9} {record['question'][:90]}")


def review_one(record: dict, author_res: dict | None, review: dict) -> dict:
    record = {**record, "reviewer_sql": review.get("sql"), "reviewer_cannot_answer": review.get("cannot_answer"),
              "reviewer_ambiguous": review.get("ambiguous")}
    if not review:
        return {**record, "status": "disputed", "reason": "reviewer returned nothing"}
    if review.get("ambiguous"):
        return {**record, "status": "disputed", "reason": f"ambiguous: {review['ambiguous']}"}

    if record["category"] == "unanswerable":
        if review.get("cannot_answer"):
            return {**record, "status": "accepted", "agreement": "both unanswerable"}
        return {**record, "status": "disputed", "reason": "reviewer wrote SQL for an 'unanswerable' question"}

    if review.get("cannot_answer"):
        return {**record, "status": "disputed", "reason": f"reviewer: cannot answer ({review['cannot_answer']})"}
    reviewer_sql = clean_sql(review.get("sql") or "")
    record["reviewer_sql"] = reviewer_sql
    reviewer_res = execute(reviewer_sql)
    if not reviewer_res["ok"]:
        return {**record, "status": "disputed", "reason": f"reviewer SQL error: {reviewer_res['error']}"}
    if agree(author_res["rows"], reviewer_res["rows"], record["ordered"]):
        return {**record, "status": "accepted", "agreement": "results match"}
    return {**record, "status": "disputed", "reason": "results differ",
            "author_preview": author_res["rows"][:3], "reviewer_preview": reviewer_res["rows"][:3]}


def publish(record: dict, gold_sql: str | None = None, agreement: str | None = None):
    append_jsonl(ACCEPTED, {
        "key": record["key"], "category": record["category"], "difficulty": record["difficulty"],
        "question": record["question"], "question_lay": record["question_lay"],
        "gold_sql": gold_sql if gold_sql is not None else record.get("gold_sql"),
        "ordered": record["ordered"], "agreement": agreement or record.get("agreement"),
        "author_model": record["author_model"], "reviewer_model": record["reviewer_model"],
    })


def audit():
    """Flag published items that are near-copies of seed examples or whose region reading is ambiguous."""
    from scripts.seed_examples import SEEDS
    seeds = [(q, np.array(embed_text(q))) for q, _ in SEEDS]
    AUDIT.unlink(missing_ok=True)
    flagged = 0
    for item in load_jsonl(ACCEPTED):
        flags = []
        v = np.array(embed_text(item["question"]))
        seed_q, sim = max(((q, float(v @ u)) for q, u in seeds), key=lambda x: x[1])
        if sim > SEED_SIMILARITY:
            flags.append(f"close to seed ({sim:.2f}): {seed_q}")
        sql = item.get("gold_sql") or ""
        if OVERLAP_PREDICATE.search(sql):
            starts_in = OVERLAP_PREDICATE.sub(lambda m: f"{m.group(1) or ''}pos BETWEEN {m.group(4)} AND {m.group(2)}", sql)
            a, b = execute(sql), execute(starts_in)
            if a["ok"] and b["ok"] and not agree(a["rows"], b["rows"], item.get("ordered", False)):
                flags.append(f"'overlaps' and 'starts within' readings differ: {a['rows'][:2]} vs {b['rows'][:2]}")
        if flags:
            flagged += 1
            append_jsonl(AUDIT, {"key": item["key"], "question": item["question"], "flags": flags})
            print(f"{item['key']}: {item['question'][:90]}\n   " + "\n   ".join(flags))
    print(f"\n{flagged} flagged items -> {AUDIT.relative_to(ROOT)}")


def adjudicate():
    """
    Apply decisions from adjudication.jsonl, one JSON object per line:
      disputed items:  {"key", "decision": "author" | "reviewer" | "sql" | "drop", "sql"?, "note"}
      published items: {"key", "decision": "exclude" | "sql", "sql"?, "note"}
    Must run before the benchmark split is frozen: excluding items renumbers later ids.
    """
    decided = {d["key"]: d for d in load_jsonl(ADJUDICATION)}

    kept = []
    for item in load_jsonl(ACCEPTED):
        d = decided.get(item["key"])
        if d and d["decision"] == "exclude":
            continue
        if d and d["decision"] == "sql" and item["category"] != "unanswerable":
            reason, _ = check_author_item({**item, "gold_sql": d["sql"]})
            if reason:
                print(f"  {item['key']}: corrected SQL rejected ({reason})")
            else:
                item = {**item, "gold_sql": clean_sql(d["sql"]), "agreement": "adjudicated (corrected)"}
        kept.append(item)
    ACCEPTED.write_text("".join(json.dumps(i) + "\n" for i in kept), encoding="utf-8")

    published = {a["key"] for a in kept}
    applied = 0
    for c in load_jsonl(CANDIDATES):
        d = decided.get(c["key"])
        if c["status"] != "disputed" or not d or c["key"] in published or d["decision"] == "drop":
            continue
        sql = {"author": c.get("gold_sql"), "reviewer": c.get("reviewer_sql"), "sql": d.get("sql")}[d["decision"]]
        if c["category"] != "unanswerable":
            reason, _ = check_author_item({**c, "gold_sql": sql})
            if reason:
                print(f"  {c['key']}: adjudicated SQL rejected ({reason})")
                continue
        publish(c, gold_sql=clean_sql(sql) if sql else None, agreement=f"adjudicated ({d['decision']})")
        applied += 1
    print(f"Published {applied} adjudicated items.")
    normalize_order_flags()


def normalize_order_flags():
    """Re-derive `ordered` for every published item with the current rule (asks for order, no internal ties)."""
    items = load_jsonl(ACCEPTED)
    changed = 0
    for item in items:
        sql = item.get("gold_sql")
        ordered = bool(sql) and asks_for_order(item["question"]) and "order by" in sql.lower() and not ties_within(sql)
        if ordered != bool(item.get("ordered")):
            item["ordered"] = ordered
            changed += 1
    ACCEPTED.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    print(f"Order flags updated on {changed} items.")


def recheck():
    """Re-score disputed 'results differ' items with the current comparator and ordering rules."""
    cands = load_jsonl(CANDIDATES)
    published = {a["key"] for a in load_jsonl(ACCEPTED)}
    resolved = 0
    for i, c in enumerate(cands):
        if c["status"] != "disputed" or c.get("reason") != "results differ" or c["key"] in published:
            continue
        item = {**c, "ordered": asks_for_order(c["question"])}
        reason, author_res = check_author_item(item)
        reviewer_res = execute(c["reviewer_sql"] or "")
        if reason or not reviewer_res["ok"]:
            continue
        if agree(author_res["rows"], reviewer_res["rows"], item["ordered"]):
            cands[i] = {**c, "ordered": item["ordered"], "status": "accepted", "agreement": "results match (recheck)"}
            publish(cands[i])
            resolved += 1
            print(f"  resolved: {c['question'][:90]}")
    CANDIDATES.write_text("".join(json.dumps(c, default=str) + "\n" for c in cands), encoding="utf-8")
    print(f"{resolved} disputes resolved by recheck")


def status():
    cands = load_jsonl(CANDIDATES)
    accepted = load_jsonl(ACCEPTED)
    print(f"{'category':<14}{'target':>7}{'published':>10}{'disputed':>9}{'rejected':>9}")
    for cat, (target, _) in CATEGORIES.items():
        cs = [c for c in cands if c["category"] == cat]
        print(f"{cat:<14}{target:>7}{sum(a['category'] == cat for a in accepted):>10}"
              f"{sum(c['status'] == 'disputed' for c in cs):>9}{sum(c['status'] == 'rejected' for c in cs):>9}")
    reviewed = [c for c in cands if c["status"] in ("accepted", "disputed")]
    if reviewed:
        rate = sum(c["status"] == "accepted" for c in reviewed) / len(reviewed)
        print(f"\nInter-annotator agreement (execution match): {rate:.1%} of {len(reviewed)} reviewed items")
    reasons = {}
    for c in cands:
        if c["status"] == "rejected":
            key = c["reason"].split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        print("Rejections: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["run", "status", "recheck", "audit", "adjudicate"])
    p.add_argument("--only", help="comma-separated categories")
    args = p.parse_args()

    if args.command == "status":
        return status()
    if args.command == "adjudicate":
        return adjudicate()
    if args.command == "audit":
        return audit()
    if args.command == "recheck":
        return recheck()

    card = data_card()
    deduper = Deduper([q["question"] for q in existing_questions()]
                      + [c["question"] for c in load_jsonl(CANDIDATES) if c["status"] != "rejected"])
    for category in (args.only.split(",") if args.only else CATEGORIES):
        run_category(category, card, deduper)
    status()


if __name__ == "__main__":
    main()
