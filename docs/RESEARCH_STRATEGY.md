# GenoSQL-AI — Research Strategy (October 2026)

## 1. Where the project stands

The original proposals (Aug–Sep 2025) and the implementation plan (Mar 2026) describe a broad
"hybrid SQL + AI" platform: PostgreSQL + pgvector + Apache AGE graph + JSONB, Redis, MinIO,
NL→SQL with open LLMs and RAG, plus AI analytics (variant interpretation, patient stratification).

What was built (Mar 2026): 1000 Genomes chr22 in PostgreSQL, Ollama `qwen2.5-coder:7b`
text-to-SQL, a pgvector example memory, and a simple UI. The pipeline worked end to end.

What the October 2026 review found and fixed:

| Problem | Effect | Fix |
|---|---|---|
| Every valid-looking SQL was auto-saved as a few-shot example | Memory **poisoned itself**: all 7 seeds used a non-existent `qual` column; `chrom = 'chr22'` (0 rows) was saved as "correct" | Only human-verified SQL enters memory (`/feedback`); seeds rewritten |
| `quality` = 100 for all 1.1 M rows | "High quality variants" questions are meaningless | Schema notes tell the model; benchmark avoids them |
| Allele frequencies stored as JSON arrays | The most useful data (population AFs) was effectively unqueryable | Typed columns `af`, `afr_af`…`sas_af`, `ac`, `an`, `variant_type`, `sv_type`, `end_pos`; multi-allelic sites split per allele |
| Regex SQL guard, app ran as superuser | `pg_read_file()`, quoted/comma-joined tables bypassed the guard; valid aliases rejected | SELECT-only login role + READ ONLY txn + timeout; sqlglot AST guard |
| `init.sql` lacked `CREATE EXTENSION vector`; no named volume; port clash | Fresh deploys failed; data loss on `down` | Idempotent `schema.sql` + `setup_db.py`; named volume; port 5434 |
| vcfpy parsed 2,504 genotypes per line | Very slow ingestion | Site-only parser |
| INFO `VT` assumed to be in ALT order | 975 alleles mislabelled (e.g. `A>AT` as SNP) | Class derived from alleles (`classify`) + regression test |
| LLM SQL executed via psycopg2 with param parsing | Any SQL containing `%` (e.g. `LIKE 'rs%'`) failed | Raw named cursor, no parameter interpolation |
| This 1000G release has **no rsIDs** (ID = `.`) | rsID lookups — the most common real question — impossible | Model told to abstain; dbSNP annotation added to roadmap |
| No evaluation | No way to make claims | `eval/` harness with execution accuracy |

## 2. Positioning: what would make this genuinely useful (and publishable)

**What already exists — don't compete head-on:**
- *BiomedSQL* (NIH-CARD, arXiv 2505.20321, ICLR 2026): 68 k templated question/SQL pairs over a
  BigQuery biomedical knowledge base (GWAS, omics causal inference, drug approvals). Frontier models
  reach ~60 % execution accuracy vs 90 % for experts. Focus: scientific reasoning, frontier/cloud models.
- *On-prem open LLM frontier on BIRD* (arXiv 2606.29733): Qwen2.5-Coder / CodeLlama sizes × schema
  linking / self-correction / self-consistency, with McNemar tests — but on GPUs (vLLM), on BIRD.
- General: Spider/BIRD, EHRSQL; AWS Bedrock genomics demo (proprietary, cloud).

**The gap GenoSQL-AI can own:** *variant-level population-genetics data* + *CPU-only commodity
hardware at zero marginal cost* + *safety and trust mechanisms measured, not just described.*
The target user is a small lab / LMIC institution / clinician-scientist with a laptop and public data,
not a cloud budget. That is consistent with the original "cost-effective, open-source" thesis, but
now with a measurable claim.

### Proposed paper

> **"How far does a laptop get? Safe, verifiable natural-language querying of population-genomic
> variant data with small open LLMs."**

Contributions:

1. **GenoSQL-Bench** — an execution-graded benchmark over public variant data (1000 Genomes now;
   ClinVar + gene annotations next), with genomics-specific failure modes made explicit:
   chromosome naming, 1-based coordinates and interval overlap, multi-allelic sites, population-specific
   frequencies, SV classes, rarity thresholds, and **unanswerable questions** (genes/phenotypes/individuals
   not in the data). Small (target 150–250 items) but expert-reviewed, unlike templated corpora.
2. **Cost–accuracy frontier on a CPU-only 8 GB laptop** — quantized open models (≈0.5B–8B) via Ollama;
   report execution accuracy, abstention accuracy, latency, tokens, energy (optional), $/1k queries = 0
   marginal vs. a hosted frontier model as an accuracy ceiling.
3. **Ablation of the pipeline** — bare schema → + genomic semantic layer (column meanings, conventions)
   → + verified RAG examples → + self-repair → + abstention. Hypothesis (backed by BRACIS 2025 finding
   that schema linking matters more as models shrink): the *semantic layer* closes most of the gap for
   small models, cheaper than scaling the model.
4. **Memory poisoning experiment** — this project accidentally demonstrated it. Simulate a stream of
   user questions with (a) auto-store-every-valid-SQL vs (b) verified-only memory vs (c) no memory;
   plot accuracy over time. Self-reinforcing errors in "self-learning" RAG are under-studied and directly
   relevant to anyone deploying these systems.
5. **Safety layer as an evaluated artifact** — adversarial prompt set (prompt-injection in questions,
   attempts to read other tables / server files); report guard + DB-role block rates.

Statistics: paired comparisons per question → McNemar's test; bootstrap CIs on accuracy. Fixed seeds,
temperature 0, model digests recorded.

### Scope cuts (recommended)

Drop from the paper scope: Apache AGE graph, MinIO, Redis, patient stratification, AlphaMissense-style
prediction. They add infrastructure cost without serving the research questions, and each is a paper of
its own. (They can appear as "future work".) RQ1–RQ3 from Chapter One map onto contributions 1–3 directly.

## 3. First measurements on the target laptop (i5-1135G7, 7.7 GB RAM, no GPU)

- Ingestion: 1,103,547 records → 1,110,237 allele rows in 621 s (site-only parser).
- `qwen2.5-coder:7b` (5.1 GB resident) + Docker Desktop VM + Postgres left **73 MB free RAM**; the
  machine swapped. One question timed out at 600 s (cold load), the next took 191 s — though it
  *correctly abstained* on an rsID question.
- Implication: on 8 GB, 7B is only viable with Docker's VM memory capped (`%UserProfile%\.wslconfig`,
  `memory=2GB`) or the DB on another host. The interesting frontier for this hardware class is
  **0.5B–3B** models — which makes the "semantic layer vs model size" ablation the central experiment.

## 4. Roadmap

### Phase 1 — Data breadth (1–2 weeks)
- [ ] Gene annotations: GENCODE v19 (GRCh37) genes on chr22 → `genes(gene_name, gene_id, chrom, start_pos, end_pos, strand, biotype)`. Unlocks "variants in APOL1" — the most common real question.
- [ ] ClinVar GRCh37 VCF, chr22 subset → `clinvar(chrom, pos, ref, alt, clnsig, clndn, review_status, gene)`. Unlocks "pathogenic variants common in AFR but rare in EUR" (genuinely interesting: population-differentiated clinically-relevant variants).
- [ ] 1000G sample panel (2,504 samples → 26 populations, 5 super-pops, sex) → `samples`.
- [ ] Optional: sparse carrier genotypes for a gene subset (full chr22 genotypes ≈ 10⁸ rows — too big for the laptop target).
- [ ] Add each table to `app/nl_query/schema.py` and `READABLE_TABLES` in `scripts/setup_db.py`.

### Phase 2 — Benchmark (2–3 weeks)
- [ ] Grow `eval/benchmark.jsonl` to 150–250 items; ~20 % multi-table joins, ~10 % unanswerable.
- [ ] Two annotators write/review gold SQL independently; record agreement.
- [ ] Paraphrase set: each question in 2 phrasings (lay vs expert) → robustness.
- [ ] Freeze v1, publish on GitHub/Zenodo with DOI.

### Phase 3 — Experiments (2 weeks, mostly unattended compute)
- [ ] Models: qwen2.5-coder 0.5b/1.5b/3b/7b, llama3.2 3b, a recent 7–8B general model, one SQL-tuned model; one hosted frontier model as ceiling.
- [ ] Ablation grid (5 configs × ~7 models × ~200 items ≈ 7 k runs; CPU ~10–20 s each → 1–2 days).
- [ ] Memory-poisoning simulation.
- [ ] Adversarial safety set.

### Phase 4 — Usability (optional but strengthens the paper)
- [ ] 5–10 biologists: tasks with UI vs writing SQL; time-to-answer, correctness, SUS questionnaire. Needs ethics approval if at a university.

### Phase 5 — Deploy & write
- [ ] `docker compose --profile app up` single-command deploy; cheapest hosted option is a 4 vCPU / 8 GB VPS (~$20–40/month) with Ollama.
- [ ] Paper targets: *Bioinformatics* Application Note (short, tool-focused) or *NAR Genomics and Bioinformatics* / *BMC Bioinformatics* (full study); workshop alternative: a NeurIPS/ICLR data-centric or LLM-for-science workshop.

## 5. Threats to validity (address in the paper)
- Single chromosome, single cohort: answer distributions may not generalise → add ClinVar + second dataset (e.g. gnomAD chr22 sites subset).
- Benchmark authored by the system's authors → independent annotator, paraphrases, freeze before tuning prompts.
- Seed examples leaking into benchmark → seeds are pattern-level and checked for overlap; evaluation excludes self-retrieval.
- LLM nondeterminism → temperature 0, fixed seed, record model digest (`ollama show`).
