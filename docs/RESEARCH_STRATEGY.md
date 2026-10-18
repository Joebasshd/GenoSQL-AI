# GenoSQL-AI — Research Strategy (October 2026)

## 1. Where the project stands

The original proposals (Aug–Sep 2025) and the implementation plan (Mar 2026) describe a broad
"hybrid SQL + AI" platform: PostgreSQL + pgvector + Apache AGE graph + JSONB, Redis, MinIO,
NL→SQL with open LLMs and RAG, plus AI analytics (variant interpretation, patient stratification).

What was built (Mar 2026): 1000 Genomes chr22 in PostgreSQL, Ollama `qwen2.5-coder:7b`
text-to-SQL, a pgvector example memory, and a simple UI. The pipeline worked end to end.

What the October 2026 review found and fixed:

| Problem | Effect | Fix |
| --- | --- | --- |
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

## 3. First measurements on the target laptop (i5-1135G7, 7.7 GB RAM, no GPU)

- Ingestion: 1,103,547 records → 1,110,237 allele rows in 621 s (site-only parser).
- `qwen2.5-coder:7b` (5.1 GB resident) + Docker Desktop VM + Postgres left **73 MB free RAM**; the
  machine swapped. One question timed out at 600 s (cold load), the next took 191 s — though it
  *correctly abstained* on an rsID question.
- Implication: on 8 GB, 7B is only viable with Docker's VM memory capped (`%UserProfile%\.wslconfig`,
  `memory=2GB`) or the DB on another host. The interesting frontier for this hardware class is
  **0.5B–3B** models — which makes the "semantic layer vs model size" ablation the central experiment.

### First benchmark results (v0, 58 items: 52 answerable + 6 unanswerable; 2026-10-14)

Full table: [eval/results/SUMMARY_v0.md](../eval/results/SUMMARY_v0.md). "Baseline" = table/column names only,
no RAG, no repair. "Full" = semantic layer + 3 verified examples + up to 2 repairs + abstention.

| config | EX (relaxed) | correct abstention | silent wrong answers* |
| --- | --- | --- | --- |
| 1.5B baseline | 15.4 % | 0/6 | 59 % |
| 3B baseline | 15.4 % | 1/6 | 72 % |
| 1.5B full | 51.9 % | 0/6 | 45 % |
| 3B full | **61.5 %** | **6/6** | **21 %** |

\*share of all items where the user receives a result table that is wrong (or any table for an unanswerable question).

- Pipeline effect dwarfs model size: baseline→full is +36 pts (1.5B) and +46 pts (3B), McNemar p < 0.001 for both;
  1.5B→3B at baseline: no difference (p = 1.0); 1.5B→3B full: p = 0.043.
- Baseline failures are the genomics conventions the semantic layer encodes: `chrom = 'chr22'`, joining
  `variants.id = clinvar.variation_id`, `variant_type = 'deletion'`, "rare" as a type. Most still *execute* —
  the dangerous failure mode.
- 1.5B never abstains; it invents tables/columns (`gnomad_variants`, `yri_af`), which the guard blocks (visible
  error instead of a wrong answer). 3B abstains perfectly on unanswerables but **over-abstains on 8 answerable
  items, mostly ClinVar joins** — the domain warnings ("no gene coordinates", "variants.rsid is NULL") are
  over-applied. Safety/coverage trade-off worth a section in the paper.
- Weakest categories for both: joins (1/5), multi-step (≤1/3).
- Caveats: latency medians for `1.5b_r2` and the tail of `3b_norag_nosem_r0` are inflated (the runs overlapped
  briefly); accuracy is unaffected (temperature 0). Prompts were *not* tuned on these items — before any prompt
  iteration, split the benchmark into dev/test so test results stay clean.
  v0 ran with the schema-wide column check; the table-aware guard (clear "column X exists only in table Y — join it"
  repair hints) was added afterwards and should help joins on the next run.

### Dev/test protocol (from 2026-10-14)

- Split frozen in `eval/splits.json`: 23 dev / 35 test (21 / 31 answerable), stratified by category, seeded.
  Existing assignments never move; new items (appended at the end of `ITEMS`) are assigned to keep each
  category near 40 % dev. Edited questions keep their split and print a warning.
- Iterate on prompts, schema notes and seed examples using **dev only** (`run_eval` defaults to dev).
  Run **test** once per frozen configuration, for reporting; no changes in response to individual test items.
- Seed examples must not paraphrase any benchmark item, dev or test.
- **Disclosure (for the paper):** before the split existed, the v0 failure lists for all 58 items were
  inspected; the two failure *patterns* observed (ClinVar-join over-abstention in 3B; invented tables/columns
  in 1.5B) are therefore known for test items too. Fixes must be motivated and checked on dev items, and the
  paper should report this. Growing the benchmark with fresh, unseen test items (Phase 2) removes the issue.
- v0 per split: 3B full 71.4 % dev vs 54.8 % test — dev happens to be easier; with n = 21/31 that gap is within
  chance, so judge dev improvements by paired comparisons on dev, not against the test number.

### Dev iteration on joins and abstention (benchmark v1, dev split, 2026-10-14)

All runs on the same v1 schema (Phase 1 tables) and the 30-item dev split (27 answerable + 3 unanswerable).
Each fix is a switch in `PipelineConfig` / `run_eval` so it can be ablated.

| dev config | 1.5B EX | 3B EX | 3B correct declines | 3B false declines | 3B silent wrong |
|---|---|---|---|---|---|
| semantic + RAG + repair, self-abstain (pre-fix) | 48.1 % | 66.7 % | 2/3 | 5 | 17 % |
| + routing rules + category-value check | 63.0 % | 59.3 % | 2/3 | 6 | 20 % |
| + hide always-NULL `variants.rsid`, challenge-once abstention | 59.3 % | 70.4 % | 2/3 | 5 | 13 % |
| **grounded abstention instead of self-abstain (final default)** | 55.6 % | **81.5 %** | 2/3 | **1** | 17 % |

What we learned (candidate paper findings):
1. **Offering small models a refusal option makes them refuse answerable questions.** 3B declined 5–6 answerable
   items with confidently false reasons ("no populations table" when `populations` exists; it even cited the note
   "rsIDs are in clinvar.rsid" as a reason to refuse). Asking it to re-check (challenge-once) did not help: it
   refused again. Removing the explicit refusal format (keeping only a last-resort mention in the routing rules)
   and declining when every attempt needs non-existent tables/columns cut false declines 5 → 1 and raised 3B to
   81.5 % (vs 66.7 %, p = 0.22; vs routing+value-check 59.3 %, 0/6 wins/losses, p = 0.031).
2. **Grounded abstention depends on how a model fails.** 1.5B hallucinates schema on unanswerable questions
   (`yri_af`, `individuals`, `gnomad_variants`), so grounding catches it (0/3 → 2/3 declines). 3B instead writes
   *valid* but wrong SQL for some unanswerables (e.g. global `af` for a Yoruba question), which grounding cannot
   catch — that is the residual silent-wrong risk.
3. **Don't show the model columns that carry no information.** `variants.rsid` (always NULL) was queried by both
   models and silently returned nothing; hiding it lets the guard answer "rsid exists only in clinvar — join it".
4. **Category-value checking** turns silent empty results (`variant_type = 'INS'`, `chrom = 'chr22'`) into
   repairable errors; its effect is mixed on its own at this sample size.
- Caveats: n = 27/3 on dev; several comparisons are not significant. The held-out test split is the arbiter.
  Dev iteration count: 5 configurations per model — report this in the paper.

### Held-out TEST results, benchmark v1 (run once after freezing the dev configuration)

Full tables: [eval/results/SUMMARY_v1.md](../eval/results/SUMMARY_v1.md). Test split: 39 answerable + 4 unanswerable.

| test | EX (relaxed) | correct declines | false declines | silent wrong |
|---|---|---|---|---|
| 1.5B bare | 7.7 % | 0/4 | 0 | 49 % |
| 1.5B full | **56.4 %** | 2/4 | 5 | 33 % |
| 3B bare | 15.4 % | 0/4 | 0 | 58 % |
| 3B full | **61.5 %** | 2/4 | 2 | 35 % |

- **Robust finding:** the pipeline (semantic layer + routing + verified RAG + repair + guard) lifts small CPU models
  from 8–15 % to 56–62 % on held-out questions (21/0 and 21/1 wins/losses, McNemar p < 0.001 for both).
- **Model size matters less than the pipeline:** 1.5B vs 3B full is not significantly different (4/6, p = 0.75).
- **Dev gains only partly transferred.** On the 35 test items shared with v0, v1-full vs the v0 pipeline:
  3B 54.8 % → 61.3 % (4/4, p = 1.0) and correct declines 4/4 → 2/4; 1.5B 48.4 % → 54.8 % (3/7, p = 0.34) and
  declines 0/4 → 2/4. (Confounded: the schema grew from 2 to 7 tables between v0 and v1.) 3B dev accuracy
  81.5 % vs test 61.5 % — the expected optimism from iterating on 27 items.
- **Abstention remains the open problem:** no configuration achieved high coverage *and* reliable declines at
  1.5–3B. Grounded abstention also misfires on fixable join mistakes (1.5B: 5 false declines on test). About a third
  of test answers are still silently wrong — the main argument for the verification UI (show SQL, ✓/✗) and for
  reporting silent-wrong rates in any NL→SQL paper.
- Next for the paper: grow the benchmark (Phase 2) so the test split is large enough to separate configurations
  (current n gives wide intervals), and add fresh test items never inspected during development.

### Held-out TEST results, benchmark v2 (148 items; configuration frozen since v1)

Full tables: [eval/results/SUMMARY_v2.md](../eval/results/SUMMARY_v2.md). Benchmark: [eval/BENCHMARK.md](../eval/BENCHMARK.md).

| test (134 answerable + 14 unanswerable) | bare | full | full, 105 never-inspected items |
|---|---|---|---|
| 1.5B | 8.2 % | 33.6 % | 24.2 % |
| 3B | 14.9 % | **53.0 %** | 49.5 % |

- **The pipeline effect replicates on 3× more, fresh items**: 3B 61/4 wins/losses vs bare, 1.5B 43/3 (p < 0.001).
- **The larger benchmark overturned a v1 conclusion.** On v1 (31 answerable test items) 1.5B ≈ 3B (p = 0.75);
  on v2 3B beats 1.5B 33/7 (p < 0.001). Mostly on the new items (49.5 % vs 24.2 %) and through abstention:
  grounded abstention fires 30 times on answerable items for 1.5B (it invents schema even when an answer
  exists) vs 10 for 3B. Small, development-adjacent test sets can mislead — the main argument for Phase 2.
- **Hand items reproduce v1 exactly** (3B 61.5 %, 1.5B 56.4 %), confirming the pipeline itself is unchanged;
  the drop to 53 % overall comes from harder, unseen questions.
- **Hardest skills**: carrier genotypes (1/10), multi-step (2/11), ClinVar×1000G joins (3/14), ClinVar (4/13).
  Easiest: frequency classes, genes via `variant_genes`, structural variants (≥ 6/7).
- **Phrasing robustness**: the same questions asked in plain biologist language drop 3B from 49.5 % to 36.8 %
  (20/9, p = 0.061) — the target users don't speak the schema; vocabulary mapping is clear future work.
- **Trust**: ~40 % of all test items still end with a wrong but valid-looking table ("silent wrong"); correct
  declines on unanswerables 6/14 for both models. Showing the SQL and the ✓/✗ loop are not optional.
- Caveats: annotators were LLMs (92.1 % agreement, adjudicated); human expert review of a sample is pending.

## 4. Roadmap

### Phase 1 — Data breadth ✅ (completed 2026-10-14)

- [x] ClinVar GRCh37 chr22 (release 2026-10-11) → `clinvar`; fetched via the tabix index (4 MB instead of 200 MB).
- [x] 1000G sample panel → `samples` (2,504) + `populations` (26 populations, 5 super-populations, names).
- [x] Genes: Ensembl GRCh37 release 87 chr22 GFF3 → `genes` (1,263) + precomputed `variant_genes` (803 k overlaps).
  Interval joins against 1.1 M variants timed out at 15 s; the materialised overlap table answers in < 0.5 s
  and gives small models a simple equality join.
- [x] Sparse carrier genotypes for the 13,774 alleles shared by 1000G and ClinVar → `genotypes` (2.9 M rows),
  read from the local chr22 VCF (no extra download). Integrity check: summed allele counts equal the VCF's AC
  for all 13,744 variants with carriers (the other 30 have AC = 0). Sanity check: APOL1 G1 (rs73885319)
  allele frequency 37.5 % in YRI, 49.5 % in ESN.
- [x] All tables in `app/nl_query/schema.py` / `READABLE_TABLES`; one-command `scripts/bootstrap.py`.
- Deferred: dbSNP rsIDs for all 1000G variants (requires the ~200 MB v5b chr22 VCF). ClinVar supplies rsIDs
  for its 63 k variants. Genotypes for all variants (~10⁸ rows) are out of scope for the laptop target.

### Phase 2 — Benchmark ✅ (v2 frozen 2026-10-15; datasheet: [eval/BENCHMARK.md](../eval/BENCHMARK.md))

- [x] 246 items (73 hand + 173 generated); 38 % multi-table/subquery, 9 % unanswerable; 98 dev / 148 test.
- [x] Two independent annotators (Gemini author + different Gemini reviewer, free tier) with execution-match
  agreement **92.1 %** (178 reviewed); 14 disputes adjudicated with written notes; 3 duplicates excluded.
- [x] Lay paraphrase for every generated item (173) → `run_eval --phrasing lay`.
- [x] Frozen in git (commit `4a146d5`) before any model was run on v2.
- [ ] Human expert review of a random sample (e.g. 40 items) — annotators were LLMs; report the error rate found.
- [ ] Add a LICENSE (suggest MIT for code, CC-BY-4.0 for the benchmark), tag `v2.0`, archive on Zenodo for a DOI.

### Phase 3 — Experiments ✅ (completed 2026-10-18; tables: [eval/results/SUMMARY_phase3.md](../eval/results/SUMMARY_phase3.md))

Run on a fixed 60-item stratified test subset where noted (power/compute constraints; 3B scores 53.7 % on it
vs 53.0 % on the full test). Every run is crash-safe and resumable (`eval/phase3_runs.sh`).

- [x] **Models** (full pipeline, subset): qwen2.5-coder 3B **53.7 %** > 1.5B 40.7 % > llama3.2 3B 31.5 % >
  phi4-mini 3.8B 27.8 % > qwen2.5-coder 0.5B 14.8 %. 3B beats llama3.2 3B (16/2, p = 0.001) and phi4-mini
  (18/4, p = 0.004): a code-specialised 3B beats general models of equal or larger size. Full-test scaling in
  one family: 0.5B 13.4 % → 1.5B 33.6 % → 3B 53.0 %. No SQL-specialised small model fits 8 GB (limitation).
- [x] **Ablation** (3B, subset; wins of full / of ablated):
  semantic layer is the largest single contribution (53.7 → 40.7 %, 12/3, p = 0.035); verified examples
  (→ 42.6 %, 11/5, p = 0.21) and routing rules (→ 46.3 %, 8/2, p = 0.11) help but n.s. on 54 items;
  **self-repair adds ~nothing** (→ 51.9 %, 1/0); grounded abstention changes no answer (0/0) — it only turns
  2 failed queries into explicit declines. Bare 13.0 % (26/2, p < 0.001).
- [x] **Memory poisoning** (3B, 30 pairs): unverified auto-saved memory reproduces **7/12** earlier errors on
  paraphrases (2/12 without memory); verified memory 0/12 at equal accuracy (56.7 %); expert-corrected memory
  86.7 % (vs verified 0/9, p = 0.004).
- [x] **Adversarial safety**: 33 direct + 15 NL attacks; every attack stopped by ≥ 1 layer, 0 leaks, 0 writes;
  found and fixed a guard bypass (`version()` normalised to `CURRENT_VERSION`); database alone leaks catalog
  metadata, guard alone cannot stop DoS — both layers are necessary.
- Not run (dropped for time): value-check-only ablation; ablations on other models; 7B.

### Phase 4 — Usability (optional but strengthens the paper)

- [ ] 5–10 biologists: tasks with UI vs writing SQL; time-to-answer, correctness, SUS questionnaire. Needs ethics approval if at a university.

### Phase 5 — Release & write (no hosting budget: local-first by design)

- [x] Distribution model: **fork / clone and run locally** (`scripts/bootstrap.py` + Docker + Ollama). No server,
  no API keys, $0 per query — this *is* the paper's thesis, so a hosted demo is not needed.
- [ ] Tag a release (`v1.0`) and archive it on Zenodo for a free DOI (GitHub → Zenodo integration), so the
  paper can cite an immutable version of code + benchmark + results.
- [ ] Optional free demo: a short screen recording or GIF in the README instead of a hosted instance.
- [ ] Paper targets: *Bioinformatics* Application Note (short, tool-focused) or *NAR Genomics and Bioinformatics* /
  *BMC Bioinformatics* (full study); workshop alternative: a NeurIPS/ICLR data-centric or LLM-for-science workshop.
  Check each venue's article processing charge — prefer venues with waivers or no APC (e.g. arXiv/bioRxiv
  preprint first, then a workshop or a journal offering APC waivers).

## 5. Threats to validity (address in the paper)

- Single chromosome, single cohort: answer distributions may not generalise → add ClinVar + second dataset (e.g. gnomAD chr22 sites subset).
- Benchmark authored by the system's authors → independent annotator, paraphrases, freeze before tuning prompts.
- Seed examples leaking into benchmark → seeds are pattern-level and checked for overlap; evaluation excludes self-retrieval.
- LLM nondeterminism → temperature 0, fixed seed, record model digest (`ollama show`).
