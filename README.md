# GenoSQL-AI

Natural-language querying of genomic variant data with small, locally-run open LLMs on PostgreSQL + pgvector — designed to run on a commodity laptop (no GPU, 8 GB RAM) at zero marginal cost per query.

```
question ─► retrieve verified examples (pgvector) ─► LLM (Ollama) ─► SQL guard (sqlglot AST)
                                                      ▲                      │
                                                      └── self-repair ◄──────┤
                                                                             ▼
                                         SELECT-only role, READ ONLY txn, statement timeout
```

**Data (chromosome 22, GRCh37):**
- 1000 Genomes Project phase 3 — 1.1 M variant alleles with global and continental (AFR/AMR/EAS/EUR/SAS) allele frequencies (`variants`)
- ClinVar (release 2026-10-11) — 99 k classified alleles with gene, significance, review stars, conditions, rsIDs (`clinvar`), joinable to `variants` on normalized (chrom, pos, ref, alt)

## Quick start

Prereqs: Docker, Python 3.11+, [Ollama](https://ollama.com).

```bash
cp .env.example .env                     # then set DB_PASS and READER_PASS
docker compose up -d db
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # bin/ on Linux/macOS
ollama pull qwen2.5-coder:7b

python -m scripts.setup_db               # schema + SELECT-only reader role
python -m scripts.load_vcf data/chr22.vcf.gz
python -m scripts.fetch_clinvar_slice    # chr22 only, via the tabix index (~4 MB, not the 200 MB file)
python -m scripts.load_clinvar
python -m scripts.seed_examples
uvicorn app.main:app --reload            # UI at http://localhost:8000
```

The chr22 VCF is `ALL.chr22.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz` from the 1000 Genomes FTP (`ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/`).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/query` | `{"question": "..."}` → SQL, results, status (`ok`, `abstained`, `invalid`, `db_error`, `llm_error`) |
| POST | `/feedback` | `{"query_id", "correct", "corrected_sql"?}` — the only way SQL enters RAG memory |
| GET | `/history` | recent queries from `query_log` |
| GET | `/health`, `/stats` | DB/LLM status, counts |

## Evaluation

```bash
python -m eval.run_eval --check-gold                         # validate the benchmark's gold SQL
python -m eval.run_eval                                      # full pipeline on the DEV split (default)
python -m eval.run_eval --split test                         # held-out TEST split: report only, never tune
python -m eval.run_eval --no-rag --no-semantic --repairs 0   # bare-LLM baseline
python -m eval.run_eval --model qwen2.5-coder:3b             # model-size comparison
python -m eval.summarize                                     # compare runs + McNemar tests
pytest
```

Metric is **execution accuracy**: gold and predicted SQL are both executed and their result sets compared (`strict`: same columns; `relaxed`: extra predicted columns allowed). Unanswerable questions are scored on correct abstention. The benchmark has a frozen, category-stratified dev/test split (`eval/splits.json`, ~40/60): iterate on dev, report test. See [docs/RESEARCH_STRATEGY.md](docs/RESEARCH_STRATEGY.md).

## Safety model

LLM-generated SQL is executed by `genosql_reader`, a login role with `SELECT` on `variants` and `clinvar` only, inside a `READ ONLY` transaction with a statement timeout and a streamed row cap. The sqlglot guard (single SELECT, known tables/columns, no `pg_*`/`set_config`/`dblink`-style functions) is defence in depth and produces precise errors for self-repair.
