"""
AST-level validation of LLM-generated SQL (sqlglot, PostgreSQL dialect).

This is defence in depth: the database-side boundary is the SELECT-only
reader role + READ ONLY transaction (app/database.py). The guard's main job
is to fail fast with an error message precise enough for the LLM to repair
its own query.
"""
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.nl_query.schema import ALLOWED_COLUMNS, ALLOWED_TABLES

# Functions that read server state/files, change settings, or reach other DBs.
DENIED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "txid_")
DENIED_FUNCTIONS = {
    "set_config", "current_setting", "query_to_xml", "query_to_xmlschema",
    "table_to_xml", "cursor_to_xml", "database_to_xml", "copy", "version",
}

WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
    exp.Alter, exp.Command, exp.Into, exp.Grant, exp.TruncateTable, exp.Set,
)

# PostgreSQL implicit column-like identifiers that aren't real columns.
PSEUDO_COLUMNS = {"ctid", "excluded"}


def clean_sql(raw: str) -> str:
    """Strip markdown fences, comments and surrounding prose from model output."""
    sql = raw.strip()
    fenced = re.search(r"```(?:sql|postgresql)?\s*(.*?)```", sql, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        sql = fenced.group(1)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = sql.strip().rstrip(";").strip()
    return sql + ";" if sql else ""


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def validate_sql(sql: str) -> tuple[bool, str]:
    """Return (ok, reason). `reason` is phrased so it can be fed back to the LLM."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        first = str(e).splitlines()[0]
        return False, f"SQL syntax error: {first}"

    if len(statements) != 1:
        return False, "Exactly one SQL statement is allowed."
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        return False, "Only SELECT queries are allowed."
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            return False, f"Only read-only SELECT queries are allowed (found {node.key.upper()})."

    # Tables: anything that isn't a CTE name must be an allowed table.
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            continue  # table-valued function such as generate_series(...)
        schema = (table.db or "").lower()
        if schema and schema != "public":
            return False, f"Access to schema '{schema}' is not allowed. Use only: {sorted(ALLOWED_TABLES)}."
        if name not in ALLOWED_TABLES and name not in cte_names:
            return False, f"Unknown table '{table.name}'. Available tables: {sorted(ALLOWED_TABLES)}."

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name in DENIED_FUNCTIONS or name.startswith(DENIED_FUNCTION_PREFIXES):
            return False, f"Function '{name}' is not allowed."

    # Columns: real columns, or names introduced by the query itself (aliases,
    # CTE / derived-table column lists).
    introduced = {a.alias.lower() for a in tree.find_all(exp.Alias)}
    for ta in tree.find_all(exp.TableAlias):
        introduced |= {c.name.lower() for c in ta.columns}
    known = ALLOWED_COLUMNS | introduced | PSEUDO_COLUMNS
    for col in tree.find_all(exp.Column):
        name = col.name.lower()
        if name and name != "*" and name not in known:
            return False, (
                f"Unknown column '{col.name}'. Valid columns: {', '.join(sorted(ALLOWED_COLUMNS))}."
            )

    return True, "OK"
