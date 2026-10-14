from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import config
from app.database import engine
from app.nl_query.sql_guard import clean_sql, validate_sql
from app.nl_query.text_to_sql import answer_question
from app.rag.example_store import store_example

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

app = FastAPI(title="GenoSQL-AI")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class FeedbackRequest(BaseModel):
    query_id: int
    correct: bool
    corrected_sql: str | None = None   # optional fix supplied by an expert


@app.get("/")
def serve_ui():
    return FileResponse(FRONTEND)


@app.get("/ui")
def serve_ui_alias():
    return FileResponse(FRONTEND)


@app.get("/health")
def health():
    status = {"db": "ok", "llm": "ok", "model": config.LLM_MODEL}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        status["db"] = f"error: {e.__class__.__name__}"
    if config.LLM_BACKEND == "ollama":
        try:
            tags = httpx.get(f"{config.OLLAMA_HOST}/api/tags", timeout=3).json()
            if not any(m["name"] == config.LLM_MODEL for m in tags.get("models", [])):
                status["llm"] = f"model {config.LLM_MODEL} not pulled"
        except Exception as e:
            status["llm"] = f"error: {e.__class__.__name__}"
    return status


@app.get("/stats")
def stats():
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT (SELECT reltuples::bigint FROM pg_class WHERE relname = 'variants') AS variants,
                   (SELECT COUNT(*) FROM sql_examples WHERE verified) AS verified_examples,
                   (SELECT COUNT(*) FROM query_log) AS queries
        """)).mappings().one()
    return dict(row)


@app.post("/query")
def query(request: QueryRequest):
    """Natural-language question → SQL → results. Every call is logged."""
    result = answer_question(request.question)

    with engine.begin() as conn:
        query_id = conn.execute(text("""
            INSERT INTO query_log (question, generated_sql, status, error, attempts, examples_used,
                                   row_count, model, latency_ms, prompt_tokens, output_tokens)
            VALUES (:question, :sql, :status, :error, :attempts, :examples_used,
                    :row_count, :model, :latency_ms, :prompt_tokens, :output_tokens)
            RETURNING id
        """), {**result, "row_count": len(result["rows"])}).scalar()

    body = {
        "query_id": query_id,
        "question": result["question"],
        "sql": result["sql"],
        "status": result["status"],
        "error": result["error"],
        "attempts": result["attempts"],
        "examples_used": result["examples_used"],
        "latency_ms": result["latency_ms"],
        "columns": result["columns"],
        "results": [dict(zip(result["columns"], r)) for r in result["rows"]],
        "row_count": len(result["rows"]),
        "truncated": result["truncated"],
    }
    if result["status"] in ("ok", "abstained"):
        return jsonable_encoder(body)
    code = 503 if result["status"] == "llm_error" else 422
    raise HTTPException(status_code=code, detail=jsonable_encoder(body))


@app.post("/feedback")
def feedback(request: FeedbackRequest):
    """
    Human verification is the only way a query enters RAG memory.
    correct=True stores the generated SQL; corrected_sql stores the expert's fix.
    """
    sql = None
    if request.corrected_sql:
        sql = clean_sql(request.corrected_sql)
        ok, reason = validate_sql(sql)
        if not ok:
            raise HTTPException(422, f"corrected_sql rejected: {reason}")

    with engine.begin() as conn:
        row = conn.execute(
            text("UPDATE query_log SET feedback = :ok WHERE id = :id RETURNING question, generated_sql, status"),
            {"ok": request.correct, "id": request.query_id},
        ).mappings().first()
    if row is None:
        raise HTTPException(404, "Unknown query_id")

    if sql is None and request.correct and row["status"] == "ok":
        sql = row["generated_sql"]

    if sql:
        store_example(row["question"], sql, verified=True, source="user")
    return {"stored_example": bool(sql)}


@app.get("/history")
def history(limit: int = 20):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, created_at, question, generated_sql, status, row_count, latency_ms, feedback
            FROM query_log ORDER BY id DESC LIMIT :limit
        """), {"limit": min(limit, 200)}).mappings().all()
    return jsonable_encoder([dict(r) for r in rows])
