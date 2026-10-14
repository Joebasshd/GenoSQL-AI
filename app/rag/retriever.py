from sqlalchemy import text
from app.database import engine
from app.rag.embeddings import embed_text, to_pgvector


def retrieve_similar_examples(question: str, k: int = 3, exclude_question: str | None = None):
    """
    Top-k verified examples by cosine distance (matches the HNSW
    vector_cosine_ops index). `exclude_question` prevents an evaluation item
    from retrieving itself if it also exists in memory.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT question, sql_query, embedding <=> CAST(:embedding AS vector) AS distance
                FROM sql_examples
                WHERE verified
                  AND (CAST(:exclude AS text) IS NULL OR lower(question) <> lower(CAST(:exclude AS text)))
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :k
            """),
            {"embedding": to_pgvector(embed_text(question)), "k": k, "exclude": exclude_question},
        )
        return result.fetchall()
