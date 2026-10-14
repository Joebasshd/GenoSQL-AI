-- GenoSQL-AI schema. Idempotent: applied by `python -m scripts.setup_db`.

CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────────────────────────
-- variants: one row per (site, ALT allele). Multi-allelic VCF records
-- are split so that per-allele INFO fields (AC, AF, *_AF) line up.
-- Frequently queried INFO fields are promoted to typed columns; the
-- remainder stays in `info` (JSONB).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS variants (
    id               BIGSERIAL PRIMARY KEY,
    chrom            TEXT    NOT NULL,          -- no 'chr' prefix (GRCh37 / hs37d5)
    pos              INTEGER NOT NULL,          -- 1-based
    end_pos          INTEGER,                   -- INFO END for SVs, else pos + len(ref) - 1
    rsid             TEXT,                      -- VCF ID column; NULL when '.'
    ref              TEXT    NOT NULL,
    alt              TEXT    NOT NULL,          -- single allele
    variant_type     TEXT,                      -- per allele: SNP | INDEL | MNP | SV (not INFO VT; see vcf_parser.classify)
    sv_type          TEXT,                      -- INFO SVTYPE for structural variants
    is_multiallelic  BOOLEAN NOT NULL DEFAULT FALSE,
    quality          REAL,
    filter           TEXT,
    ac               INTEGER,                   -- alt allele count
    an               INTEGER,                   -- total called alleles
    af               REAL,                      -- global alt allele frequency
    afr_af           REAL,
    amr_af           REAL,
    eas_af           REAL,
    eur_af           REAL,
    sas_af           REAL,
    dp               INTEGER,                   -- total read depth
    info             JSONB,                     -- remaining INFO fields
    UNIQUE (chrom, pos, ref, alt)
);

CREATE INDEX IF NOT EXISTS idx_variants_chrom_pos ON variants (chrom, pos);
CREATE INDEX IF NOT EXISTS idx_variants_rsid ON variants (rsid) WHERE rsid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_variants_af ON variants (af);
CREATE INDEX IF NOT EXISTS idx_variants_type ON variants (variant_type);

-- ─────────────────────────────────────────────────────────────
-- clinvar: ClinVar GRCh37 VCF, one row per (site, ALT allele), alleles
-- normalized exactly like `variants` so the two join on
-- (chrom, pos, ref, alt).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clinvar (
    id                     BIGSERIAL PRIMARY KEY,
    chrom                  TEXT    NOT NULL,
    pos                    INTEGER NOT NULL,
    ref                    TEXT    NOT NULL,
    alt                    TEXT    NOT NULL,
    variation_id           INTEGER NOT NULL,   -- ClinVar VariationID (VCF ID)
    allele_id              INTEGER,            -- ALLELEID
    rsid                   TEXT,               -- 'rs' || RS
    gene                   TEXT,               -- first symbol in GENEINFO
    clinical_significance  TEXT,               -- CLNSIG, underscores → spaces
    significance_class     TEXT,               -- pathogenic | likely_pathogenic | uncertain | likely_benign | benign | conflicting | other
    is_pathogenic          BOOLEAN NOT NULL,   -- Pathogenic / Likely pathogenic / Pathogenic/Likely pathogenic
    review_status          TEXT,               -- CLNREVSTAT, underscores → spaces
    review_stars           SMALLINT,           -- 0..4 ClinVar gold stars
    conditions             TEXT,               -- CLNDN, '|'-separated, underscores → spaces
    variant_class          TEXT,               -- CLNVC: single nucleotide variant, Deletion, ...
    consequences           TEXT,               -- MC names, ','-separated: missense_variant, ...
    origin                 INTEGER             -- ORIGIN bit flags (1 germline, 2 somatic, ...)
);

-- Hash the alleles: some ClinVar deletions are kilobases long, beyond the
-- B-tree row size limit for a plain (ref, alt) key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_clinvar_allele
ON clinvar (chrom, pos, md5(ref), md5(alt), variation_id);
CREATE INDEX IF NOT EXISTS idx_clinvar_locus ON clinvar (chrom, pos);
CREATE INDEX IF NOT EXISTS idx_clinvar_gene ON clinvar (gene);
CREATE INDEX IF NOT EXISTS idx_clinvar_rsid ON clinvar (rsid);

-- ─────────────────────────────────────────────────────────────
-- sql_examples: RAG memory of (question → SQL) pairs.
-- Only `verified` rows are retrieved as few-shot examples; unverified
-- model output is never fed back into the prompt (avoids self-poisoning).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sql_examples (
    id          SERIAL PRIMARY KEY,
    question    TEXT NOT NULL,
    sql_query   TEXT NOT NULL,
    embedding   vector(384),
    verified    BOOLEAN NOT NULL DEFAULT FALSE,
    source      TEXT NOT NULL DEFAULT 'user',   -- 'seed' | 'user'
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (question, sql_query)
);

ALTER TABLE sql_examples ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sql_examples ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'user';

CREATE INDEX IF NOT EXISTS idx_sql_examples_embedding
ON sql_examples USING hnsw (embedding vector_cosine_ops);

-- ─────────────────────────────────────────────────────────────
-- query_log: every NL query, for auditing and for usage studies.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_log (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    question        TEXT NOT NULL,
    generated_sql   TEXT,
    status          TEXT NOT NULL,              -- ok | invalid | db_error | abstained | llm_error
    error           TEXT,
    attempts        INTEGER,
    examples_used   INTEGER,
    row_count       INTEGER,
    model           TEXT,
    latency_ms      INTEGER,
    prompt_tokens   INTEGER,
    output_tokens   INTEGER,
    feedback        BOOLEAN                     -- user marked result correct / incorrect
);
