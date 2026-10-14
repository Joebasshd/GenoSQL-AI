import pytest
from app.nl_query.sql_guard import clean_sql, validate_sql


@pytest.mark.parametrize("sql", [
    "SELECT chrom, pos FROM variants WHERE chrom = '22' LIMIT 5;",
    "SELECT COUNT(*) AS n FROM variants;",                                 # alias (old guard rejected)
    "SELECT variant_type, COUNT(*) AS n FROM variants GROUP BY variant_type ORDER BY n DESC;",
    "SELECT ROUND(AVG(af)::numeric, 4) FROM variants;",                     # functions + casts
    "SELECT info->>'AA' AS ancestral FROM variants LIMIT 3;",               # JSONB access
    "WITH rare AS (SELECT * FROM variants WHERE af < 0.01) SELECT COUNT(*) FROM rare;",
    "SELECT v.pos FROM variants v WHERE v.af > 0.5;",                       # table alias
    "SELECT x.c FROM (SELECT COUNT(*) AS c FROM variants) x;",              # derived table
    "SELECT * FROM variants WHERE rsid LIKE 'rs%' LIMIT 1;",
    "SELECT s.n FROM generate_series(1, 3) AS s(n);",                       # table function
    "SELECT pos FROM variants WHERE ref = 'A' UNION SELECT pos FROM variants WHERE ref = 'C';",
])
def test_allows_valid_selects(sql):
    ok, reason = validate_sql(sql)
    assert ok, reason


@pytest.mark.parametrize("sql, fragment", [
    ("DROP TABLE variants;", "SELECT"),
    ("DELETE FROM variants;", "SELECT"),
    ("SELECT 1; DROP TABLE variants;", "one SQL statement"),
    ("SELECT * INTO copy_t FROM variants;", "read-only"),
    ("SELECT * FROM sql_examples;", "Unknown table"),
    ('SELECT * FROM "sql_examples";', "Unknown table"),                    # quoted bypass of old regex
    ("SELECT * FROM variants, query_log;", "Unknown table"),               # comma join bypass
    ("SELECT * FROM pg_catalog.pg_user;", "schema"),
    ("SELECT pg_read_file('/etc/passwd');", "not allowed"),
    ("SELECT set_config('role', 'postgres', true);", "not allowed"),
    ("SELECT current_setting('data_directory');", "not allowed"),
    ("SELECT qual FROM variants;", "Unknown column 'qual'"),               # the original hallucination
    ("SELECT chrom FROM variants WHERE gene = 'APOL1';", "Unknown column 'gene'"),
    ("SELEC chrom FROM variants", "error"),
])
def test_rejects_unsafe_or_wrong(sql, fragment):
    ok, reason = validate_sql(sql)
    assert not ok
    assert fragment.lower() in reason.lower()


def test_clean_sql_strips_fences_and_prose():
    raw = "Here you go:\n```sql\nSELECT 1 -- one\n```\nHope it helps!"
    assert clean_sql(raw) == "SELECT 1;"


def test_clean_sql_keeps_single_trailing_semicolon():
    assert clean_sql("SELECT 1;;  ") == "SELECT 1;"
