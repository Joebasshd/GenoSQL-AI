from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import config, datasets
from app.database import engine
from app.nl_query.sql_guard import clean_sql, validate_sql
from app.nl_query.schema import REFERENCE_SCHEMA
from app.nl_query.text_to_sql import PipelineConfig, answer_question
from app.rag.example_store import store_example
from app.rag.retriever import retrieve_similar_examples

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    datasets.fail_interrupted()
    yield


app = FastAPI(title="GenoSQL-AI", lifespan=lifespan)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    dataset_id: int | None = None      # None = the 1000 Genomes reference database


def view_for(dataset_id: int | None):
    if dataset_id is None:
        return REFERENCE_SCHEMA
    view = datasets.get_view(dataset_id)
    if view is None:
        raise HTTPException(404, "Dataset not found or not ready")
    return view


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
    view = view_for(request.dataset_id)
    cfg = PipelineConfig(schema=view)
    if request.dataset_id is not None:
        cfg.retriever = partial(retrieve_similar_examples, dataset_id=request.dataset_id)
    result = answer_question(request.question, cfg)

    with engine.begin() as conn:
        query_id = conn.execute(text("""
            INSERT INTO query_log (question, generated_sql, status, error, attempts, examples_used,
                                   row_count, model, latency_ms, prompt_tokens, output_tokens, dataset_id)
            VALUES (:question, :sql, :status, :error, :attempts, :examples_used,
                    :row_count, :model, :latency_ms, :prompt_tokens, :output_tokens, :dataset_id)
            RETURNING id
        """), {**result, "row_count": len(result["rows"]), "dataset_id": request.dataset_id}).scalar()

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
    with engine.connect() as conn:
        logged = conn.execute(text("SELECT question, generated_sql, status, dataset_id FROM query_log WHERE id = :id"),
                              {"id": request.query_id}).mappings().first()
    if logged is None:
        raise HTTPException(404, "Unknown query_id")

    sql = None
    if request.corrected_sql:
        sql = clean_sql(request.corrected_sql)
        ok, reason = validate_sql(sql, schema=view_for(logged["dataset_id"]))
        if not ok:
            raise HTTPException(422, f"corrected_sql rejected: {reason}")

    with engine.begin() as conn:
        conn.execute(text("UPDATE query_log SET feedback = :ok WHERE id = :id"),
                     {"ok": request.correct, "id": request.query_id})

    if sql is None and request.correct and logged["status"] == "ok":
        sql = logged["generated_sql"]

    if sql:
        store_example(logged["question"], sql, verified=True, source="user", dataset_id=logged["dataset_id"])
    return {"stored_example": bool(sql)}


@app.get("/history")
def history(limit: int = 20, dataset_id: int | None = None):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, created_at, question, generated_sql, status, row_count, latency_ms, feedback
            FROM query_log WHERE dataset_id IS NOT DISTINCT FROM CAST(:dataset AS integer)
            ORDER BY id DESC LIMIT :limit
        """), {"limit": min(limit, 200), "dataset": dataset_id}).mappings().all()
    return jsonable_encoder([dict(r) for r in rows])


@app.get("/datasets")
def list_datasets():
    return jsonable_encoder(datasets.list_datasets())


@app.post("/datasets", status_code=202)
def upload_dataset(file: UploadFile = File(...), name: str = Form("")):
    """Accept a VCF or VCF.gz; it is loaded in the background (poll GET /datasets)."""
    filename = file.filename or "upload.vcf"
    dataset_id = datasets.create_dataset(name or filename, filename)
    path = datasets.upload_path(dataset_id)
    limit = config.UPLOAD_MAX_MB * 1024 * 1024
    written = 0
    with path.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                break
            out.write(chunk)
    if written > limit:
        datasets.delete_dataset(dataset_id)
        raise HTTPException(413, f"File is larger than the {config.UPLOAD_MAX_MB} MB limit")
    datasets.submit_ingest(dataset_id, path)
    return {"dataset_id": dataset_id, "status": "pending"}


@app.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: int):
    """Permanently remove a dataset and everything derived from it."""
    if not datasets.delete_dataset(dataset_id):
        raise HTTPException(404, "Unknown dataset")
    return {"deleted": dataset_id}
