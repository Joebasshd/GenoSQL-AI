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