"""
NL → SQL pipeline:

    question ─► retrieve verified examples (RAG) ─► LLM ─► clean ─► guard ─► execute
                                                    ▲                │         │
                                                    └── repair ◄─────┴─────────┘
                                                       (guard / DB error fed back)

Each stage can be switched off via PipelineConfig, which is what the
evaluation harness uses for ablations.
"""
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import psycopg2

from app import config
from app.database import run_readonly
from app.nl_query.llm import chat
from app.nl_query.schema import ALLOWED_TABLES, schema_prompt
from app.nl_query.sql_guard import clean_sql, validate_sql
from app.rag.retriever import retrieve_similar_examples

ABSTAIN_MARKER = "CANNOT_ANSWER"
ALLOWED_TABLE_LIST = sorted(ALLOWED_TABLES)
# Guard messages meaning "the query needs schema that doesn't exist"
SCHEMA_MISS_PREFIXES = ("Unknown table", "Unknown column", "Table '")


@dataclass
class PipelineConfig:
    model: str = field(default_factory=lambda: config.LLM_MODEL)
    backend: str = field(default_factory=lambda: config.LLM_BACKEND)
    use_rag: bool = True
    semantic_schema: bool = True        # column descriptions + domain notes
    routing_rules: bool = True          # answer-shape / table-routing / abstention guidance
    value_check: bool = True            # guard rejects unknown category values
    max_repairs: int = field(default_factory=lambda: config.MAX_REPAIR_ATTEMPTS)
    # Abstention. Dev results (docs/RESEARCH_STRATEGY.md): offering small models a
    # "CANNOT_ANSWER" option makes them refuse answerable questions; declining only
    # when every attempt needs non-existent schema (grounded) was best for 3B.
    allow_abstain: bool = False         # tell the model it may reply CANNOT_ANSWER
    verify_abstain: bool = True         # challenge a first CANNOT_ANSWER once before accepting it
    grounded_abstain: bool = True       # decline when every attempt needs tables/columns that don't exist
    rag_k: int = field(default_factory=lambda: config.RAG_K)
    max_rows: int = field(default_factory=lambda: config.MAX_ROWS)
    retriever: Callable | None = None   # (question, k, exclude_question) -> rows; default: pgvector memory


def system_prompt(cfg: PipelineConfig) -> str:
    abstain = (
        f"\n- If the question cannot be answered from this schema, reply exactly:\n"
        f"  {ABSTAIN_MARKER}: <one-sentence reason>"
        if cfg.allow_abstain else ""
    )
    return f"""You are a bioinformatics SQL expert. Convert questions about genomic variant data into a single PostgreSQL SELECT query.

Database schema:
{schema_prompt(cfg.semantic_schema, cfg.routing_rules and cfg.semantic_schema)}

Rules:
- Return only the SQL query: no explanation, no markdown.
- Use only the tables and columns listed above, with their exact names.
- Return the columns that answer the question; for listings include chrom, pos, ref, alt.{abstain}"""


def build_user_prompt(question: str, examples: list) -> str:
    if not examples:
        return f"Question: {question}"
    shots = "\n\n".join(f"Question: {q}\nSQL: {s}" for q, s, *_ in examples)
    return f"Examples of correct queries for similar questions:\n\n{shots}\n\nQuestion: {question}"


def _abstain_reason(text: str) -> str | None:
    m = re.search(rf"{ABSTAIN_MARKER}\s*:?\s*(.*)", text, flags=re.IGNORECASE)
    if m and not re.search(r"\bSELECT\b", text[: m.start()], flags=re.IGNORECASE):
        reason = m.group(1).strip().rstrip(";").strip()
        if not reason or reason.startswith("<"):   # empty, or the prompt's placeholder echoed back
            return "The model judged that the database does not contain this information."
        return reason
    return None


def answer_question(question: str, cfg: PipelineConfig | None = None, execute: bool = True,
                    exclude_question: str | None = None) -> dict:
    """
    Run the full pipeline. Returns a dict with status in
    {ok, invalid, db_error, abstained, llm_error}.
    """
    cfg = cfg or PipelineConfig()
    started = time.perf_counter()
    out = {
        "question": question, "sql": None, "status": None, "error": None,
        "attempts": 0, "examples_used": 0, "columns": [], "rows": [], "truncated": False,
        "model": cfg.model, "prompt_tokens": 0, "output_tokens": 0, "llm_ms": 0,
        "trace": [],
    }

    examples = []
    if cfg.use_rag:
        retrieve = cfg.retriever or retrieve_similar_examples
        examples = retrieve(question, k=cfg.rag_k, exclude_question=exclude_question)
    out["examples_used"] = len(examples)

    messages = [
        {"role": "system", "content": system_prompt(cfg)},
        {"role": "user", "content": build_user_prompt(question, examples)},
    ]

    challenged = False
    for attempt in range(cfg.max_repairs + 1):
        out["attempts"] = attempt + 1
        try:
            resp = chat(messages, model=cfg.model, backend=cfg.backend)
        except Exception as e:  # network / model not pulled / server down
            out.update(status="llm_error", error=f"LLM call failed: {e}")
            break
        out["prompt_tokens"] += resp.prompt_tokens or 0
        out["output_tokens"] += resp.output_tokens or 0
        out["llm_ms"] += resp.latency_ms

        # Honour the marker even when it wasn't offered (allow_abstain=False):
        # models sometimes decline spontaneously, and the text is not SQL anyway.
        reason = _abstain_reason(resp.text)
        if reason and cfg.verify_abstain and not challenged and attempt < cfg.max_repairs:
            # Small models give up on answerable questions with false claims
            # ("no populations table"). Make them check once; accept a second refusal.
            challenged = True
            out["trace"].append({"attempt": attempt + 1, "abstain_challenged": reason})
            messages += [
                {"role": "assistant", "content": resp.text},
                {"role": "user", "content": (
                    f"Check again before giving up. The available tables are: {', '.join(ALLOWED_TABLE_LIST)}. "
                    "List the table and column that hold each piece of information the question needs. "
                    "If they all exist, return the SQL query only. "
                    f"If something needed is truly missing, reply {ABSTAIN_MARKER}: <what is missing>.")},
            ]
            continue
        if reason:
            out.update(status="abstained", error=reason, sql=None)
            out["trace"].append({"attempt": attempt + 1, "abstained": reason})
            break

        sql = clean_sql(resp.text)
        out["sql"] = sql
        ok, problem = validate_sql(sql, check_values=cfg.value_check)
        status = "invalid"

        if ok and execute:
            try:
                result = run_readonly(sql, max_rows=cfg.max_rows)
                out.update(status="ok", error=None, **result)
                out["trace"].append({"attempt": attempt + 1, "sql": sql, "ok": True})
                break
            except psycopg2.Error as e:
                problem = (e.pgerror or str(e)).strip().splitlines()[0]
                status = "db_error"
        elif ok:
            out.update(status="ok", error=None)
            break

        out.update(status=status, error=problem)
        out["trace"].append({"attempt": attempt + 1, "sql": sql, "error": problem})
        messages += [
            {"role": "assistant", "content": resp.text},
            {"role": "user", "content": f"That query failed: {problem}\nReturn a corrected SQL query only."},
        ]

    if (cfg.grounded_abstain and out["status"] == "invalid"
            and (out["error"] or "").startswith(SCHEMA_MISS_PREFIXES)):
        # The model could only express the question with data that isn't in the
        # database: decline, naming what is missing (deterministic, schema-grounded).
        missing = out["error"].split(". ")[0].split(" for ")[0]    # e.g. "Unknown column 'yri_af'"
        out.update(status="abstained", sql=None,
                   error=f"This needs data that isn't in the database ({missing}).")

    out["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return out
