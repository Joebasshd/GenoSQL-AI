import itertools
import re

from sqlalchemy import create_engine
from app import config

# Admin engine: ingestion, example memory, query log. Never runs LLM output.
engine = create_engine(config.admin_url(), pool_pre_ping=True)

# Reader engine: executes LLM-generated SQL as a SELECT-only role.
# This is the real security boundary; the SQL guard is defence in depth.
reader_engine = create_engine(config.reader_url(), pool_pre_ping=True)

_cursor_ids = itertools.count()


def run_readonly(sql: str, max_rows: int = config.MAX_ROWS,
                 timeout_ms: int = config.STATEMENT_TIMEOUT_MS, search_path: str | None = None) -> dict:
    """
    Execute a query in a READ ONLY transaction with a statement timeout,
    returning at most `max_rows` rows.

    Uses a raw psycopg2 named (server-side) cursor:
    - rows are streamed, so `SELECT *` over a million rows isn't pulled into memory
    - DECLARE CURSOR only accepts SELECT/VALUES, one more barrier against writes
    - executed without parameters, so '%' and ':' in the SQL are taken literally
    `search_path` selects an uploaded dataset ("ds_3, public").
    Raises psycopg2.Error on database errors.
    """
    if search_path is not None and not re.fullmatch(r"ds_\d+, public", search_path):
        raise ValueError(f"Invalid search_path: {search_path!r}")
    raw = reader_engine.raw_connection()
    try:
        with raw.cursor() as setup:
            setup.execute("SET TRANSACTION READ ONLY")
            setup.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            if search_path:
                setup.execute(f"SET LOCAL search_path = {search_path}")
        cur = raw.cursor(name=f"genosql_{next(_cursor_ids)}")
        cur.itersize = max_rows + 1
        cur.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        columns = [d.name for d in cur.description]
        cur.close()
    finally:
        raw.rollback()
        raw.close()

    truncated = len(rows) > max_rows
    return {"columns": columns, "rows": [tuple(r) for r in rows[:max_rows]], "truncated": truncated}
