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

from app.nl_query.schema import ALLOWED_COLUMNS, ALLOWED_TABLES, CATEGORICAL_VALUES, TABLE_COLUMNS

# Functions that read server state/files, change settings, or reach other DBs.
DENIED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "txid_")
DENIED_FUNCTIONS = {
    "set_config", "current_setting", "query_to_xml", "query_to_xmlschema",
    "table_to_xml", "cursor_to_xml", "database_to_xml", "copy",
    # server identity: sqlglot normalises e.g. version() to CURRENT_VERSION
    "version", "current_version", "current_user", "session_user", "user", "current_database",
    "current_schema", "current_schemas", "inet_server_addr", "inet_server_port",
    "inet_client_addr", "inet_client_port",
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


def validate_sql(sql: str, check_values: bool = True) -> tuple[bool, str]:
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

    # Columns: real columns of the tables in the query, or names introduced by
    # the query itself (aliases, CTE / derived-table column lists).
    introduced = {a.alias.lower() for a in tree.find_all(exp.Alias)}
    for ta in tree.find_all(exp.TableAlias):
        introduced |= {c.name.lower() for c in ta.columns}

    # alias/name -> real table, for qualified references like v.af
    alias_to_table = {}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in ALLOWED_TABLES:
            alias_to_table[table.alias_or_name.lower()] = name
    used_tables = set(alias_to_table.values())
    has_derived = bool(cte_names) or any(True for _ in tree.find_all(exp.Subquery))
    unqualified_ok = (ALLOWED_COLUMNS if has_derived or not used_tables
                      else set().union(*(TABLE_COLUMNS[t] for t in used_tables)))

    for col in tree.find_all(exp.Column):
        name = col.name.lower()
        if not name or name == "*" or name in introduced or name in PSEUDO_COLUMNS:
            continue
        qualifier = col.table.lower()
        if qualifier in alias_to_table:
            table = alias_to_table[qualifier]
            if name not in TABLE_COLUMNS[table]:
                return False, (f"Table '{table}' has no column '{col.name}'. "
                               f"Its columns: {', '.join(sorted(TABLE_COLUMNS[table]))}.")
        elif name not in (ALLOWED_COLUMNS if qualifier else unqualified_ok):
            where = ", ".join(sorted(t for t, cols in TABLE_COLUMNS.items() if name in cols))
            hint = f" ('{col.name}' exists only in: {where} — join it.)" if where else ""
            tables = sorted(used_tables) or sorted(ALLOWED_TABLES)
            return False, (f"Unknown column '{col.name}' for {', '.join(tables)}.{hint} "
                           f"Valid columns: {', '.join(sorted(set().union(*(TABLE_COLUMNS[t] for t in tables))))}.")

    if check_values:
        problem = _check_category_values(tree)
        if problem:
            return False, problem

    return True, "OK"


def _check_category_values(tree) -> str | None:
    """
    Value linking: `variant_type = 'INS'` is valid SQL that silently returns
    nothing. Reject literals outside a category column's known values.
    """
    def check(col, lit):
        if not (isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string):
            return None
        allowed = CATEGORICAL_VALUES.get(col.name.lower())
        if allowed is not None and lit.this not in allowed:
            return (f"'{lit.this}' is not a valid value for {col.name}. "
                    f"Valid values: {', '.join(sorted(allowed))}.")
        return None

    for node in tree.find_all(exp.EQ, exp.NEQ):
        problem = check(node.left, node.right) or check(node.right, node.left)
        if problem:
            return problem
    for node in tree.find_all(exp.In):
        for lit in node.expressions:
            problem = check(node.this, lit)
            if problem:
                return problem
    return None
