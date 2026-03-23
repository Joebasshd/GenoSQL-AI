from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from app.database import engine
from app.nl_query.text_to_sql import generate_sql

app = FastAPI()

class QueryRequest(BaseModel):
    question: str

@app.get("/")
def root():
    return {"message": "GenoSQL-AI running"}


@app.get("/variants/count")
def variant_count():

    conn = engine.connect()

    result = conn.execute(
        text("SELECT COUNT(*) FROM variants")
    )

    count = result.scalar()

    return {"variant_count": count}

@app.post("/query")
def query(request: QueryRequest):
    """
    Take the natural language question, run it through the RAG pipeline, convert it to SQL, execute it, and return the results.
    """
    result = generate_sql(request.question)

    if not result['valid']:
        raise HTTPException(
            status_code=400,
            detail={
                'error': result['reason'],
                'generated_sql': result['sql']
            }
        )
    with engine.connect() as conn:
        db_result = conn.execute(text(result['sql']))
        rows = db_result.fetchall()
        columns = list(db_result.keys())

    data = [dict(zip(columns, row)) for row in rows]

    return {
        'question': request.question,
        'sql': result['sql'],
        'examples_used': result['examples_used'],
        'row_count': len(data),
        'results': data
    }