"""
Apply db/schema.sql and (re)create the SELECT-only reader role.

    python -m scripts.setup_db              # create / migrate
    python -m scripts.setup_db --reset      # drop variants + examples first (re-ingest needed)
"""
import argparse
from pathlib import Path

from sqlalchemy import text
from app import config
from app.database import engine

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# Tables the LLM is allowed to read. Keep in sync with app/nl_query/schema.py.
READABLE_TABLES = ["variants"]


def setup(reset: bool = False):
    if not config.READER_PASS:
        raise SystemExit("READER_PASS is not set in .env")

    with engine.begin() as conn:
        if reset:
            conn.execute(text("DROP TABLE IF EXISTS variants, sql_examples CASCADE"))
            print("Dropped variants and sql_examples.")

        conn.exec_driver_sql(SCHEMA_FILE.read_text())
        print("Schema applied.")

        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": config.READER_USER}
        ).scalar()
        verb = "ALTER" if exists else "CREATE"
        # Role names / passwords can't be bound parameters; quote them safely.
        role = conn.execute(text("SELECT quote_ident(:r)"), {"r": config.READER_USER}).scalar()
        pw = conn.execute(text("SELECT quote_literal(:p)"), {"p": config.READER_PASS}).scalar()
        conn.exec_driver_sql(
            f"{verb} ROLE {role} LOGIN PASSWORD {pw} "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
        )
        conn.exec_driver_sql(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        conn.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        db = conn.execute(text("SELECT quote_ident(current_database())")).scalar()
        conn.exec_driver_sql(f"GRANT CONNECT ON DATABASE {db} TO {role}")
        conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role}")
        for table in READABLE_TABLES:
            conn.exec_driver_sql(f"GRANT SELECT ON {table} TO {role}")
        print(f"Reader role {config.READER_USER} ready (SELECT on {', '.join(READABLE_TABLES)}).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    setup(parser.parse_args().reset)
