from fastapi import FastAPI
from sqlalchemy import text
from app.database import engine

app = FastAPI()


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