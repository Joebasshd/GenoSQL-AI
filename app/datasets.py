"""
User-uploaded datasets: lifecycle, profiling and the generated schema view.

Each dataset lives in schema ds_<id>. Its SchemaView is generated from what is
actually in the data, so the model is never told about columns that carry no
information, and the guard's allowed values match this file.
"""
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import text

from app import config
from app.database import engine, run_readonly
from app.ingestion.user_vcf import VcfError, create_tables, detect_build, load_vcf, read_header
from app.nl_query.schema import TABLES, SchemaView
from app.nl_query.sql_guard import validate_sql
from app.rag.example_store import store_example

log = logging.getLogger(__name__)
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
MAX_INFO_KEYS = 12
MIN_SAMPLES_FOR_COHORT_AF = 10     # below this, a frequency computed from genotypes means nothing
MAX_LISTED_VALUES = 30

_ingest_pool = ThreadPoolExecutor(max_workers=1)   # one load at a time on a laptop
_views: dict[int, SchemaView] = {}
_views_lock = threading.Lock()


def schema_name(dataset_id: int) -> str:
    return f"ds_{int(dataset_id)}"


def sanitize(value: str, limit: int = 80) -> str:
    """File-supplied text reaches the LLM prompt: keep it short and free of quotes and newlines."""
    return re.sub(r"[^A-Za-z0-9 _.,:;()/%+=-]", " ", str(value))[:limit].strip()


# ── profile: what is actually in the data ────────────────────────────────

def _scalar(conn, sql: str):
    return conn.execute(text(sql)).scalar()


def _top(conn, sql: str) -> list:
    return [list(r) for r in conn.execute(text(sql)).all()]


def annotate_variant_genes(conn, schema: str) -> int:
    """Overlap this dataset's chromosome-22 variants with the reference genes (GRCh37 only)."""
    window = _scalar(conn, f"SELECT COALESCE(MAX(end_pos - pos), 0) FROM {schema}.variants "
                           f"WHERE variant_type <> 'SV' AND chrom = '22'")
    conn.execute(text(f"""
        CREATE TABLE {schema}.variant_genes AS
        SELECT v.id AS variant_id, g.gene_id FROM public.genes g
        JOIN {schema}.variants v ON v.chrom = g.chrom
             AND v.pos BETWEEN g.start_pos - {int(window) + 1} AND g.end_pos AND v.end_pos >= g.start_pos
        WHERE v.variant_type <> 'SV'
        UNION
        SELECT v.id, g.gene_id FROM {schema}.variants v
        JOIN public.genes g ON g.chrom = v.chrom AND v.pos <= g.end_pos AND v.end_pos >= g.start_pos
        WHERE v.variant_type = 'SV'
    """))
    conn.execute(text(f"CREATE INDEX ON {schema}.variant_genes (gene_id)"))
    conn.execute(text(f"CREATE INDEX ON {schema}.variant_genes (variant_id)"))
    return _scalar(conn, f"SELECT COUNT(*) FROM {schema}.variant_genes")


def clean_sample_ids(samples: list) -> list:
    """Names the guard may quote back to the LLM; a file with unsafe names just goes unchecked."""
    return list(samples) if all(sanitize(x, 30) == x for x in samples) else []


def build_profile(schema: str, header, build: str, info_keys: set) -> dict:
    s = schema
    with engine.begin() as conn:
        chr22 = _scalar(conn, f"SELECT COUNT(*) FROM {s}.variants WHERE chrom = '22'")
        annotated = build == "GRCh37" and chr22 > 0
        gene_links = annotate_variant_genes(conn, s) if annotated else 0
        clinvar_matches = _scalar(conn, f"""
            SELECT COUNT(*) FROM {s}.variants v JOIN public.clinvar c
            ON c.chrom = v.chrom AND c.pos = v.pos AND c.ref = v.ref AND c.alt = v.alt""") if annotated else 0
        profile = {
            "build": build,
            "n_samples": len(header.samples),
            "file_has_af": "AF" in header.info,
            "sample_examples": [sanitize(x, 30) for x in header.samples[:8]],
            "sample_ids": clean_sample_ids(header.samples),
            "annotated": annotated,
            "gene_links": gene_links,
            "clinvar_matches": clinvar_matches,
            "chromosomes": _top(conn, f"SELECT chrom, COUNT(*) FROM {s}.variants GROUP BY 1 ORDER BY 2 DESC"),
            "variant_types": _top(conn, f"SELECT variant_type, COUNT(*) FROM {s}.variants GROUP BY 1 ORDER BY 2 DESC"),
            "sv_types": _top(conn, f"SELECT sv_type, COUNT(*) FROM {s}.variants WHERE sv_type IS NOT NULL "
                                   f"GROUP BY 1 ORDER BY 2 DESC LIMIT {MAX_LISTED_VALUES}"),
            "filters": _top(conn, f"SELECT filter, COUNT(*) FROM {s}.variants WHERE filter IS NOT NULL "
                                  f"GROUP BY 1 ORDER BY 2 DESC LIMIT {MAX_LISTED_VALUES}"),
            "pos_range": list(conn.execute(text(f"SELECT MIN(pos), MAX(pos) FROM {s}.variants")).one()),
            "quality_range": [float(x) if x is not None else None for x in conn.execute(
                text(f"SELECT MIN(quality), MAX(quality) FROM {s}.variants")).one()],
            "has": {
                "rsid": _scalar(conn, f"SELECT COUNT(rsid) > 0 FROM {s}.variants"),
                "af": _scalar(conn, f"SELECT COUNT(af) > 0 FROM {s}.variants"),
                "ac": _scalar(conn, f"SELECT COUNT(ac) > 0 FROM {s}.variants"),
                "dp": _scalar(conn, f"SELECT COUNT(dp) > 0 FROM {s}.variants"),
                "multiallelic": _scalar(conn, f"SELECT bool_or(is_multiallelic) FROM {s}.variants"),
                "genotype_gq": _scalar(conn, f"SELECT COUNT(gq) > 0 FROM {s}.genotypes"),
                "genotype_dp": _scalar(conn, f"SELECT COUNT(dp) > 0 FROM {s}.genotypes"),
                "genotypes": _scalar(conn, f"SELECT EXISTS (SELECT 1 FROM {s}.genotypes)"),
            },
            "all_info_keys": sorted(info_keys),
            "numeric_info_keys": sorted(k for k in info_keys if header.info.get(k, ("", "", ""))[0] == "1"
                                        and header.info[k][2] in ("Integer", "Float")),
            "info_keys": [
                {"id": sanitize(k, 30), "description": sanitize(header.info.get(k, ("", "", ""))[1], 45)}
                for k in sorted(info_keys)[:MAX_INFO_KEYS]
            ],
        }
    return profile


# ── generated schema view ────────────────────────────────────────────────

def _quoted(values) -> str:
    return ", ".join(f"'{sanitize(v, 30)}'" for v in values)


def build_view(dataset_id: int, profile: dict, guidance: bool = True) -> SchemaView:
    has = dict(profile["has"])
    if not profile.get("file_has_af") and profile["n_samples"] < MIN_SAMPLES_FOR_COHORT_AF:
        has["ac"] = has["af"] = False
    chroms = [c for c, _ in profile["chromosomes"]]
    types = [t for t, _ in profile["variant_types"] if t]
    filters = [f for f, _ in profile["filters"]]
    sv_types = [t for t, _ in profile["sv_types"]]
    quality = profile["quality_range"]
    informative_quality = quality[0] is not None and quality[0] != quality[1]
    n = profile["n_samples"]

    columns = [
        ("id", "BIGINT", "primary key"),
        ("chrom", "TEXT", f"chromosome WITHOUT 'chr' prefix; values: {_quoted(chroms[:MAX_LISTED_VALUES])}"),
        ("pos", "INTEGER", "1-based start position"),
        ("end_pos", "INTEGER", "end position; the SV end for structural variants"),
    ]
    if has["rsid"]:
        columns.append(("rsid", "TEXT", "variant identifier from the file, e.g. a dbSNP rsID; may be NULL"))
    columns += [("ref", "TEXT", "reference allele"), ("alt", "TEXT", "single alternate allele"),
                ("variant_type", "TEXT", f"per-allele class; values: {_quoted(types)}")]
    if sv_types:
        columns.append(("sv_type", "TEXT", f"structural variant class; values: {_quoted(sv_types)}"))
    if has["multiallelic"]:
        columns.append(("is_multiallelic", "BOOLEAN", "true if the site has more than one ALT allele"))
    if informative_quality:
        columns.append(("quality", "REAL", f"variant call quality (QUAL), {quality[0]:g} to {quality[1]:g}"))
    if len(filters) > 1:
        columns.append(("filter", "TEXT", f"FILTER status; values: {_quoted(filters)}"))
    if has["ac"]:
        columns += [("ac", "INTEGER", "alternate allele count in this dataset"),
                    ("an", "INTEGER", "total called alleles at the site")]
    if has["af"]:
        columns.append(("af", "REAL", "alternate allele frequency, 0..1"))
    if has["dp"]:
        columns.append(("dp", "INTEGER", "total read depth at the site"))
    if profile["info_keys"]:
        keys = "; ".join(f"info->>'{k['id']}'" + (f" = {k['description']}" if k["description"] else "")
                         for k in profile["info_keys"])
        columns.append(("info", "JSONB", f"other INFO fields: {keys}"))

    tables = {"variants": {"description": "Variants in the uploaded file, one row per ALT allele.",
                           "columns": columns}}
    notes = [
        "Conventions:",
        f"- This is a user-uploaded dataset ({profile['build']} genome build) with {n} sample(s).",
        "- 'chr1', 'chromosome 1' and '1' all mean chrom = '1'.",
        "- Positions are 1-based. A variant overlaps region [s, e] when pos <= e AND end_pos >= s.",
        "- Insertion when length(alt) > length(ref); deletion when length(ref) > length(alt).",
    ]
    if has["af"]:
        notes += ["- 'Rare' means af < 0.01; 'low frequency' 0.01 <= af < 0.05; 'common' af >= 0.05.",
                  "- Frequencies describe this dataset, not any reference population."]
    if len(filters) > 1:
        notes.append("- 'Passing' or 'high-confidence' variants have filter = 'PASS'.")

    routing = [
        "How to answer:",
        "- Match the answer shape to the question: 'how many' -> one COUNT; 'average/mean/fraction' -> one "
        "aggregate value; 'which/list/show' -> rows. Do not list rows for a counting question.",
    ]
    categorical = {"chrom": set(chroms), "variant_type": set(types)}
    if sv_types:
        categorical["sv_type"] = set(sv_types)
    if len(filters) > 1:
        categorical["filter"] = set(filters)
    if profile.get("sample_ids"):
        categorical["sample_id"] = set(profile["sample_ids"])

    if n and has["genotypes"]:
        genotype_columns = [
            ("variant_id", "BIGINT", "variants.id"),
            ("sample_id", "TEXT", "samples.sample_id"),
            ("allele_count", "SMALLINT", "copies of the ALT allele: 1 = heterozygous, 2 = homozygous alternate"),
            ("gt", "TEXT", "genotype as written in the file, e.g. '0/1' or '1|1'"),
        ]
        if has["genotype_gq"]:
            genotype_columns.append(("gq", "INTEGER", "genotype quality"))
        if has["genotype_dp"]:
            genotype_columns.append(("dp", "INTEGER", "read depth for this sample at this variant"))
        tables["samples"] = {"description": "Individuals in the uploaded file.",
                             "columns": [("sample_id", "TEXT",
                                          f"sample name, e.g. {_quoted(profile['sample_examples'][:4])}")]}
        tables["genotypes"] = {"description": "One row per sample carrying an ALT allele.",
                               "columns": genotype_columns}
        notes += ["- A sample with no genotypes row for a variant does not carry it (or had no call).",
                  "- Carriers are individuals: count genotypes rows, not allele copies. Join genotypes to "
                  "variants ON genotypes.variant_id = variants.id."]
        routing.append("- Who carries a variant, per-sample counts -> genotypes JOIN variants.")

    if profile["annotated"]:
        tables["genes"] = TABLES["genes"]
        tables["variant_genes"] = {"description": "Which variants in this dataset lie in which genes.",
                                   "columns": TABLES["variant_genes"]["columns"]}
        tables["clinvar"] = TABLES["clinvar"]
        notes += ["- genes, variant_genes and clinvar cover chromosome 22 only; other chromosomes have no "
                  "gene or clinical annotation.",
                  "- Variants in a gene: variants JOIN variant_genes ON variant_genes.variant_id = variants.id "
                  "JOIN genes ON genes.gene_id = variant_genes.gene_id.",
                  "- clinvar joins variants ON chrom, pos, ref AND alt.",
                  "- 'Pathogenic' means is_pathogenic. Never match pathogenicity with LIKE '%pathogenic%'."]
        routing.append("- Clinical significance, conditions -> clinvar; variants in a gene -> variant_genes.")
        categorical["significance_class"] = {"pathogenic", "likely_pathogenic", "uncertain", "likely_benign",
                                             "benign", "conflicting", "other"}
    else:
        notes.append("- There is no gene or clinical annotation for this dataset.")
    notes.append("- There is no phenotype, expression or population data for this dataset.")
    if guidance:
        if has["ac"]:
            notes.append("- 'Singleton' means a variant seen once in the cohort: ac = 1 in variants. "
                         "It is not a count of heterozygous genotypes.")
        if profile["info_keys"]:
            notes.append("- info values are text. Cast with parentheses: (info->>'MQ')::float > 55.")
            numeric = [k for k in profile.get("numeric_info_keys", [])][:MAX_INFO_KEYS]
            if numeric:
                notes.append(f"- Numeric info fields: {_quoted(numeric)}. Other info keys are text. Use a numeric "
                             "field for counts and comparisons; do not count or pattern-match a text field.")
        notes.append("- Give each table its own alias: v variants, gt genotypes, gn genes, vg variant_genes, "
                     "c clinvar, s samples.")
        if has["rsid"]:
            notes.append("- When a question gives an rsID, filter on rsid alone; do not add a position.")
        if "genotypes" in tables:
            notes.append("- For 'the genotype at' a variant, select genotypes.gt (and sample_id) by joining "
                         "variants. allele_count 1 is heterozygous, 2 is homozygous.")
            if n == 1:
                notes.append("- The file holds one person, so 'this person' or 'this genome' needs no "
                             "sample_id filter, and every row of variants is one they carry.")
        if "clinvar" in tables:
            notes.append("- 'In ClinVar' means variants JOIN clinvar ON chrom, pos, ref AND alt. clinvar has "
                         "no variant_id column.")
            notes.append("- For a ClinVar class use significance_class: 'benign' also covers Benign/Likely "
                         "benign. Do not compare clinical_significance to exact text.")

    return SchemaView(tables, "\n".join(notes), "\n".join(routing), categorical,
                      search_path=f"{schema_name(dataset_id)}, public",
                      info_keys=frozenset(profile.get("all_info_keys", [])),
                      numeric_info_keys=frozenset(profile.get("numeric_info_keys", [])))


def get_view(dataset_id: int) -> SchemaView | None:
    with _views_lock:
        if dataset_id in _views:
            return _views[dataset_id]
    with engine.connect() as conn:
        row = conn.execute(text("SELECT profile FROM datasets WHERE id = :id AND status = 'ready'"),
                           {"id": dataset_id}).first()
    if row is None:
        return None
    view = build_view(dataset_id, row.profile)
    with _views_lock:
        _views[dataset_id] = view
    return view


# ── verified examples built from this file's own values ─────────────────

def starter_examples(dataset_id: int, profile: dict, view: SchemaView,
                     guidance: bool = True) -> list[tuple[str, str]]:
    schema = schema_name(dataset_id)
    chrom = profile["chromosomes"][0][0]
    with engine.connect() as conn:
        v = conn.execute(text(f"""
            SELECT v.chrom, v.pos, v.ref, v.alt FROM {schema}.variants v
            JOIN {schema}.genotypes gt ON gt.variant_id = v.id LIMIT 1""")).first()
        if v is None:
            v = conn.execute(text(f"SELECT chrom, pos, ref, alt FROM {schema}.variants LIMIT 1")).first()
        rsid = conn.execute(text(f"""
            SELECT v.rsid FROM {schema}.variants v JOIN {schema}.genotypes gt ON gt.variant_id = v.id
            WHERE v.rsid ~ '^rs[0-9]+$' LIMIT 1""")).scalar()
    low, high = profile["pos_range"]
    mid = (low + high) // 2
    span = f"pos BETWEEN {mid} AND {mid + 100000}"
    at = f"v.chrom = '{v.chrom}' AND v.pos = {v.pos} AND v.ref = '{v.ref}' AND v.alt = '{v.alt}'"
    seeds = [
        (f"how many variants are on chromosome {chrom}", f"SELECT COUNT(*) FROM variants WHERE chrom = '{chrom}';"),
        ("how many variants of each type are there",
         "SELECT variant_type, COUNT(*) AS n FROM variants GROUP BY variant_type ORDER BY n DESC;"),
        (f"show variants in the 100 kb after {mid} on chr{chrom}",
         f"SELECT chrom, pos, ref, alt FROM variants WHERE chrom = '{chrom}' AND {span} ORDER BY pos;"),
    ]
    if "genotypes" in view.tables:
        seeds += [
            (f"who carries the {v.ref}>{v.alt} variant at {v.chrom}:{v.pos}",
             f"SELECT gt.sample_id, gt.gt FROM genotypes gt JOIN variants v ON v.id = gt.variant_id WHERE {at};"),
            ("how many variants does each sample carry",
             "SELECT sample_id, COUNT(*) AS n FROM genotypes GROUP BY sample_id ORDER BY n DESC;"),
            ("how many homozygous alternate genotypes does each sample have",
             "SELECT sample_id, COUNT(*) AS n FROM genotypes WHERE allele_count = 2 GROUP BY sample_id ORDER BY n DESC;"),
        ]
    if "af" in view.table_columns["variants"]:
        seeds.append(("how many rare variants are there", "SELECT COUNT(*) FROM variants WHERE af < 0.01;"))
    if "filter" in view.table_columns["variants"]:
        seeds.append(("how many variants pass all filters", "SELECT COUNT(*) FROM variants WHERE filter = 'PASS';"))
    if "clinvar" in view.tables:
        seeds.append(("pathogenic ClinVar variants present in this dataset",
                      "SELECT v.chrom, v.pos, v.ref, v.alt, c.gene, c.clinical_significance FROM variants v "
                      "JOIN clinvar c ON c.chrom = v.chrom AND c.pos = v.pos AND c.ref = v.ref AND c.alt = v.alt "
                      "WHERE c.is_pathogenic;"))
        seeds.append(("number of variants in each gene",
                      "SELECT gn.gene_name, COUNT(*) AS n FROM variants v JOIN variant_genes vg ON vg.variant_id = v.id "
                      "JOIN genes gn ON gn.gene_id = vg.gene_id GROUP BY gn.gene_name ORDER BY n DESC;"))
    if guidance:
        if "genotypes" in view.tables:
            seeds.append((f"what is the genotype at {v.chrom}:{v.pos}",
                          f"SELECT gt.sample_id, gt.gt FROM genotypes gt JOIN variants v ON v.id = gt.variant_id WHERE {at};"))
            if rsid:
                seeds.append((f"what is the genotype at {rsid}",
                              "SELECT gt.sample_id, gt.gt FROM genotypes gt JOIN variants v ON v.id = gt.variant_id "
                              f"WHERE v.rsid = '{rsid}';"))
                seeds.append((f"is {rsid} heterozygous or homozygous",
                              "SELECT CASE WHEN gt.allele_count = 1 THEN 'heterozygous' WHEN gt.allele_count = 2 "
                              "THEN 'homozygous' END AS zygosity FROM genotypes gt JOIN variants v ON "
                              f"v.id = gt.variant_id WHERE v.rsid = '{rsid}';"))
            if "variant_genes" in view.tables:
                seeds.append(("how many heterozygous variants are in the gene with the most variants",
                              "SELECT COUNT(*) FROM genotypes gt JOIN variant_genes vg ON vg.variant_id = gt.variant_id "
                              "JOIN genes gn ON gn.gene_id = vg.gene_id WHERE gt.allele_count = 1 AND gn.gene_name = "
                              "(SELECT gn2.gene_name FROM variant_genes vg2 JOIN genes gn2 ON gn2.gene_id = vg2.gene_id "
                              "GROUP BY gn2.gene_name ORDER BY COUNT(*) DESC LIMIT 1);"))
        if "clinvar" in view.tables:
            on = "c.chrom = v.chrom AND c.pos = v.pos AND c.ref = v.ref AND c.alt = v.alt"
            seeds.append(("how many of these variants are listed in ClinVar",
                          f"SELECT COUNT(*) FROM variants v JOIN clinvar c ON {on};"))
            seeds.append(("how many likely benign ClinVar variants are here",
                          f"SELECT COUNT(*) FROM variants v JOIN clinvar c ON {on} "
                          "WHERE c.significance_class = 'likely_benign';"))
        numeric = sorted(k for k in view.numeric_info_keys if re.fullmatch(r"\w+", k))
        if numeric:
            seeds.append((f"how many variants have {numeric[0]} of at least 2",
                          f"SELECT COUNT(*) FROM variants WHERE (info->>'{numeric[0]}')::numeric >= 2;"))
            seeds.append((f"what is the average {numeric[0]}",
                          f"SELECT AVG((info->>'{numeric[0]}')::numeric) FROM variants;"))
    return seeds


def store_starter_examples(dataset_id: int, profile: dict, view: SchemaView) -> int:
    stored = 0
    for question, sql in starter_examples(dataset_id, profile, view):
        ok, _ = validate_sql(sql, schema=view)
        if ok:
            run_readonly(sql, max_rows=1, search_path=view.search_path)
            store_example(question, sql, verified=True, source="seed", dataset_id=dataset_id)
            stored += 1
    return stored


# ── lifecycle ────────────────────────────────────────────────────────────

def create_dataset(name: str, filename: str) -> int:
    with engine.begin() as conn:
        return conn.execute(text("INSERT INTO datasets (name, filename) VALUES (:n, :f) RETURNING id"),
                            {"n": sanitize(name, 60) or "dataset", "f": sanitize(filename, 120)}).scalar()


def upload_path(dataset_id: int) -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR / f"{int(dataset_id)}.upload"


def _set_status(dataset_id: int, **fields):
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "profile" else f"{k} = :{k}" for k in fields)
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE datasets SET {sets} WHERE id = :id"), {"id": dataset_id, **fields})


def ingest(dataset_id: int, path: Path):
    """Load one uploaded file. Never raises: failures are recorded on the dataset."""
    schema = schema_name(dataset_id)
    # the loader picks the reader by name: keep the gzip suffix it expects
    with path.open("rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    named = path.with_suffix(".vcf.gz" if gz else ".vcf")
    path.rename(named)
    try:
        _set_status(dataset_id, status="loading")
        header = read_header(str(named))
        if len(header.samples) > config.UPLOAD_MAX_SAMPLES:
            raise VcfError(f"{len(header.samples):,} samples exceeds the limit of "
                           f"{config.UPLOAD_MAX_SAMPLES:,} for this machine.")
        with engine.begin() as conn:
            create_tables(conn.connection.cursor(), schema)
        result = load_vcf(str(named), schema, header)
        if result.variants == 0:
            raise VcfError("No variants found in this file.")
        build = detect_build(header)
        profile = build_profile(schema, header, build, result.info_keys)
        view = build_view(dataset_id, profile)
        with engine.begin() as conn:
            conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA {schema} TO {config.READER_USER}")
            conn.exec_driver_sql(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {config.READER_USER}")
        store_starter_examples(dataset_id, profile, view)
        _set_status(dataset_id, status="ready", build=build, n_variants=result.variants,
                    n_samples=len(header.samples), n_genotypes=result.genotypes, profile=json.dumps(profile))
    except VcfError as e:
        _drop_schema(schema)
        _set_status(dataset_id, status="failed", error=str(e))
    except Exception as e:
        log.exception("Failed to ingest dataset %s", dataset_id)
        _drop_schema(schema)
        _set_status(dataset_id, status="failed", error=f"Could not load this file ({e.__class__.__name__}).")
    finally:
        named.unlink(missing_ok=True)   # the database copy is the only one kept


def submit_ingest(dataset_id: int, path: Path):
    _ingest_pool.submit(ingest, dataset_id, path)


def _drop_schema(schema: str):
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def list_datasets() -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("""SELECT id, name, filename, status, error, build, n_variants, n_samples,
                                           n_genotypes, created_at FROM datasets ORDER BY id DESC""")).mappings().all()
    return [dict(r) for r in rows]


def delete_dataset(dataset_id: int) -> bool:
    """Remove all traces: tables, examples, query log, any file still on disk."""
    _drop_schema(schema_name(dataset_id))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sql_examples WHERE dataset_id = :id"), {"id": dataset_id})
        conn.execute(text("DELETE FROM query_log WHERE dataset_id = :id"), {"id": dataset_id})
        removed = conn.execute(text("DELETE FROM datasets WHERE id = :id"), {"id": dataset_id}).rowcount
    with _views_lock:
        _views.pop(dataset_id, None)
    for leftover in UPLOAD_DIR.glob(f"{int(dataset_id)}.*"):
        leftover.unlink(missing_ok=True)
    return bool(removed)


def fail_interrupted():
    """After a crash or restart, loads in progress can't resume: mark them failed and clean up."""
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(text(
            "SELECT id FROM datasets WHERE status IN ('pending', 'loading')")).all()]
    for dataset_id in ids:
        _drop_schema(schema_name(dataset_id))
        _set_status(dataset_id, status="failed", error="Interrupted by a restart. Please upload the file again.")
        for leftover in UPLOAD_DIR.glob(f"{dataset_id}.*"):
            leftover.unlink(missing_ok=True)
