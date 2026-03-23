import re
import ollama
from app.rag.retriever import retrieve_similar_examples
from app.rag.example_store import store_example

MODEL = "qwen2.5-coder:7b"

SCHEMA = """
Database: Genomic Variants

Table: variants

Columns:
- chrom (TEXT): chromosome identifier
- pos (INTEGER): genomic position
- ref (TEXT): reference allele
- alt (TEXT): alternate allele
- quality (FLOAT): variant quality score
"""

SYSTEM_PROMPT = f"""
You are a bioinformatics SQL expert.

Your job is to convert natural language questions into PostgreSQL queries.

Database schema:
{SCHEMA}

Rules:
- Only return SQL
- Do not explain anything
- Do not include markdown
- Use valid PostgreSQL syntax
- Only query the variants table
"""

# ── Dangerous keywords that should never appear in generated SQL ──

BLOCKED_KEYWORDS = [
    "DROP", "DELETE", "TRUNCATE", "ALTER", "INSERT",
    "UPDATE", "CREATE", "GRANT", "REVOKE", "EXEC",
    "EXECUTE", "INTO OUTFILE", "LOAD_FILE", "COPY",
]

ALLOWED_TABLES = {"variants"}


# SQL Cleaner 
def clean_sql(raw: str) -> str:
    """
    Strips markdown fences, inline comments, and leading/trailing
    whitespace from LLM-generated SQL output.
    """
    sql = raw.strip()

    # Strip markdown code fences (```sql ... ``` or ``` ... ```)
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)

    # Remove SQL single-line comments (-- ...)
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)

    # Remove SQL block comments (/* ... */)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)

    # Collapse extra whitespace into single spaces
    sql = re.sub(r"\s+", " ", sql).strip()

    # Ensure it ends with a semicolon
    if sql and not sql.endswith(";"):
        sql += ";"

    return sql


# SQL Guard

def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Validates that the SQL is safe to execute.
    Returns (is_valid, reason).
    """
    upper = sql.upper()

    # Must be a SELECT statement
    if not upper.lstrip().startswith("SELECT"):
        return False, "Only SELECT queries are allowed."
    
    # Prevent multiple SQL statements
    if sql.count(";") > 1:
        return False, "Multiple SQL statements are not allowed."

    # Block dangerous keywords
    for keyword in BLOCKED_KEYWORDS:
        # Word-boundary match to avoid false positives
        # e.g. "DELETION" shouldn't trigger "DELETE"
        pattern = rf"\b{keyword}\b"
        if re.search(pattern, upper):
            return False, f"Blocked: query contains '{keyword}'."

    # Check that only allowed tables are referenced
    # Simple FROM / JOIN clause extraction
    table_refs = re.findall(
        r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)",
        upper
    )
    referenced_tables = {
        t.lower() for pair in table_refs for t in pair if t
    }

    disallowed = referenced_tables - ALLOWED_TABLES
    if disallowed:
        return False, f"Blocked: references unauthorized table(s): {disallowed}"

    return True, "OK"

def enforce_limit(sql: str, limit: int = 50) -> str:

    upper = sql.upper()

    if "LIMIT" not in upper:
        sql = sql.rstrip(";") + f" LIMIT {limit};"

    return sql

def build_prompt(question: str, examples: list) -> str:
    """
    Builds the user-facing prompt that gets sent to Ollama.

    If we have similar past examples, we inject them here as
    few-shot demonstrations. The LLM sees real (question → SQL)
    pairs and uses them as a pattern to follow.

    If there are no examples yet (cold start — empty sql_examples table),
    we skip the examples block and just send the bare question.
    This means the system works fine on day one, before any history exists,
    and gets progressively smarter as it accumulates examples.
    """
    if examples:
        # Format each example as a clear Q→SQL pair
        examples_block = "\n\n".join(
            f"Question: {row[0]}\nSQL: {row[1]}"
            for row in examples
        )
        return f"""Here are some similar questions that were answered correctly:

{examples_block}

Now convert this new question into SQL.

Question:
{question}"""

    else:
        # Cold start: no examples yet, just ask directly
        return f"""Convert the following question into SQL.

Question:
{question}"""

# Main entry point

def generate_sql(question: str) -> dict:
    """
    Full pipeline:
    1. Retrieve similar past examples from pgvector
    2. Build an augmented prompt with those examples injected
    3. Call Ollama to generate SQL
    4. Clean and validate the SQL
    5. If valid, store this (question → SQL) pair back into sql_examples
       so future queries can learn from it
    """

    # Retrieve similar examples from the vector store.
    examples = retrieve_similar_examples(question, k=3)

    # Build the prompt, injecting examples if we have any.
    prompt = build_prompt(question, examples)

    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        options={
            "temperature": 0
        }
    )

    raw_sql = response["message"]["content"].strip()

    # Clean and validate
    sql = clean_sql(raw_sql)
    is_valid, reason = validate_sql(sql)

    if is_valid:
        sql = enforce_limit(sql)
        store_example(question, sql)

    return {
        "sql": sql,
        "raw": raw_sql,
        "valid": is_valid,
        "reason": reason,
        "examples_used": len(examples)
    }