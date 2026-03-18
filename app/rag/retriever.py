import json
from sqlalchemy import text
from app.database import engine
from app.rag.embeddings import embed_text


def retrieve_similar_examples(question: str, k: int = 3):

    embedding = embed_text(question)
    
    # Convert embedding list to JSON string for pgvector casting
    embedding_json = json.dumps(embedding)
    
    # Escape single quotes in the JSON string for SQL
    embedding_json_escaped = embedding_json.replace("'", "''")

    query = text(f"""
        SELECT question, sql_query
        FROM sql_examples
        ORDER BY embedding <-> '{embedding_json_escaped}'::vector
        LIMIT :k
    """)

    with engine.connect() as conn:

        result = conn.execute(
            query,
            {
                "k": k
            }
        )

        rows = result.fetchall()

    return rows