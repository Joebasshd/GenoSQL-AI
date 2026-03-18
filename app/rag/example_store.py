from sqlalchemy import text
from app.database import engine
from app.rag.embeddings import embed_text


def store_example(question: str, sql_query: str):

    embedding = embed_text(question)

    query = text("""
        INSERT INTO sql_examples (question, sql_query, embedding)
        VALUES (:question, :sql_query, :embedding)
    """)

    with engine.connect() as conn:

        conn.execute(
            query,
            {
                "question": question,
                "sql_query": sql_query,
                "embedding": embedding
            }
        )

        conn.commit()