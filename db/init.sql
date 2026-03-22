CREATE TABLE variants (
    id SERIAL PRIMARY KEY,
    chrom TEXT NOT NULL,
    pos INTEGER NOT NULL,
    variant_id TEXT,
    ref TEXT NOT NULL,
    alt TEXT NOT NULL,
    quality FLOAT,
    filter TEXT,
    info JSONB,
    UNIQUE (chrom, pos, ref, alt)
);

CREATE INDEX idx_variants_chrom_pos
ON variants(chrom, pos);

CREATE TABLE IF NOT EXISTS sql_examples (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    sql_query TEXT NOT NULL,
    embedding vector(384),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sql_examples_embedding
ON sql_examples USING hnsw (embedding vector_cosine_ops);