from app.rag.retriever import retrieve_similar_examples

query = "mutations on chr22"

results = retrieve_similar_examples(query)

for i, row in enumerate(results, 1):
    print(f"\nResult {i}")
    print("Question:", row.question)
    print("SQL:", row.sql_query)