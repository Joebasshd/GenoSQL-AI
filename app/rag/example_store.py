from sqlalchemy import text
from app.database import engine
from app.rag.embeddings import embed_text, to_pgvector


def store_example(question: str, sql_query: str, verified: bool = True, source: str = "user"):
    """
    Save a (question → SQL) pair to RAG memory.

    Only call this for SQL a human has confirmed (seed set or user feedback).
    Storing unverified model output lets one wrong answer become the few-shot
    "ground truth" for every similar future question.
    """
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO sql_examples (question, sql_query, embedding, verified, source)
                VALUES (:question, :sql_query, CAST(:embedding AS vector), :verified, :source)
                ON CONFLICT (question, sql_query)
                DO UPDATE SET verified = sql_examples.verified OR EXCLUDED.verified
            """),
            {
                "question": question.strip(),
                "sql_query": sql_query.strip(),
                "embedding": to_pgvector(embed_text(question)),
                "verified": verified,
                "source": source,
            },
        )
