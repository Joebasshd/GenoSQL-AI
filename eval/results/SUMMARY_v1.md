# Benchmark v1 results (2026-10-14)

Schema: Phase 1 (variants, clinvar, samples, populations, genes, variant_genes, genotypes). Benchmark v1: 73 items, frozen split 30 dev / 43 test.
Hardware: i5-1135G7, 7.7 GB RAM, CPU only; Ollama, temperature 0, seed 42, num_ctx 8192. Runs executed sequentially.

"full" = semantic layer + routing rules + 3 verified examples + up to 2 repairs + category-value check + grounded abstention (final default).
"bare" = table/column names only, no examples, no repair, no value check, no grounded abstention.

## Held-out TEST split (run once, after dev iteration was frozen)

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder_1.5b_norag_nosem_r0_novc_nogabs_test | 7.7 | 7.7 | 0 | 0 | 18 | 49 | 7.1 | 561 |
| qwen2.5-coder_1.5b_r2_test | 56.4 | 51.3 | 50 | 5 | 0 | 33 | 11.3 | 3,834 |
| qwen2.5-coder_3b_norag_nosem_r0_novc_nogabs_test | 15.4 | 12.8 | 0 | 0 | 11 | 58 | 12.0 | 547 |
| qwen2.5-coder_3b_r2_test | 61.5 | 59.0 | 50 | 2 | 0 | 35 | 24.4 | 3,428 |

| category | qwen2.5-coder_1.5b_norag_nosem_r0_novc_nogabs_test | qwen2.5-coder_1.5b_r2_test | qwen2.5-coder_3b_norag_nosem_r0_novc_nogabs_test | qwen2.5-coder_3b_r2_test |
|---|---|---|---|---|
| aggregation | 2/3 | 2/3 | 1/3 | 2/3 |
| carrier | 0/3 | 0/3 | 0/3 | 1/3 |
| clinvar | 0/5 | 1/5 | 0/5 | 2/5 |
| frequency | 0/3 | 3/3 | 2/3 | 3/3 |
| gene | 1/3 | 3/3 | 1/3 | 3/3 |
| indel | 0/2 | 2/2 | 0/2 | 2/2 |
| join | 0/3 | 1/3 | 0/3 | 1/3 |
| json | 0/2 | 0/2 | 0/2 | 1/2 |
| lookup | 0/2 | 2/2 | 1/2 | 1/2 |
| multistep | 0/2 | 0/2 | 0/2 | 0/2 |
| population | 0/5 | 4/5 | 0/5 | 4/5 |
| region | 0/2 | 1/2 | 0/2 | 1/2 |
| samples | 0/2 | 2/2 | 1/2 | 1/2 |
| sv | 0/2 | 1/2 | 0/2 | 2/2 |
| unanswerable | 0/4 | 2/4 | 0/4 | 2/4 |

Paired McNemar (relaxed): wins A only / wins B only / p
  qwen2.5-coder_1.5b_norag_nosem_r0_novc_nogabs_test vs qwen2.5-coder_1.5b_r2_test: 0 / 21 / p=0.000
  qwen2.5-coder_1.5b_norag_nosem_r0_novc_nogabs_test vs qwen2.5-coder_3b_norag_nosem_r0_novc_nogabs_test: 2 / 5 / p=0.453
  qwen2.5-coder_1.5b_norag_nosem_r0_novc_nogabs_test vs qwen2.5-coder_3b_r2_test: 0 / 23 / p=0.000
  qwen2.5-coder_1.5b_r2_test vs qwen2.5-coder_3b_norag_nosem_r0_novc_nogabs_test: 18 / 0 / p=0.000
  qwen2.5-coder_1.5b_r2_test vs qwen2.5-coder_3b_r2_test: 4 / 6 / p=0.754
  qwen2.5-coder_3b_norag_nosem_r0_novc_nogabs_test vs qwen2.5-coder_3b_r2_test: 1 / 21 / p=0.000

## DEV split iteration (5 configurations per model)

Tags: `*_r2_norout_novc_dev` = pre-fix; `*_r2_dev` = + routing + value check (self-abstain); `*_v1fix2_dev` = + hidden rsid + challenge-once; `*_v1grounded2_dev` = grounded abstention (final). `3b_v1both` = self-abstain + challenge + grounded; `*_v1grounded_dev` = grounded before spontaneous-marker handling.

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder_1.5b_r2_norout_novc_dev | 48.1 | 40.7 | 0 | 0 | 2 | 40 | 12.6 | 3,456 |
| qwen2.5-coder_1.5b_r2_dev | 63.0 | 55.6 | 0 | 0 | 1 | 30 | 11.7 | 3,533 |
| qwen2_5-coder_1_5b_v1fix2_dev | 59.3 | 51.9 | 0 | 0 | 2 | 33 | 12.7 | 3,479 |
| qwen2_5-coder_1_5b_v1grounded2_dev | 55.6 | 48.1 | 67 | 1 | 1 | 37 | 9.8 | 3,441 |
| qwen2.5-coder_3b_r2_norout_novc_dev | 66.7 | 55.6 | 67 | 5 | 0 | 17 | 25.3 | 2,697 |
| qwen2.5-coder_3b_r2_dev | 59.3 | 51.9 | 67 | 6 | 0 | 20 | 27.3 | 2,812 |
| qwen2_5-coder_3b_v1fix2_dev | 70.4 | 63.0 | 67 | 5 | 0 | 13 | 23.3 | 3,543 |
| qwen2_5-coder_3b_v1both_dev | 70.4 | 63.0 | 67 | 5 | 0 | 13 | 25.2 | 3,731 |
| qwen2_5-coder_3b_v1grounded2_dev | 81.5 | 74.1 | 67 | 1 | 0 | 17 | 22.2 | 3,146 |

| category | qwen2.5-coder_1.5b_r2_norout_novc_dev | qwen2.5-coder_1.5b_r2_dev | qwen2_5-coder_1_5b_v1fix2_dev | qwen2_5-coder_1_5b_v1grounded2_dev | qwen2.5-coder_3b_r2_norout_novc_dev | qwen2.5-coder_3b_r2_dev | qwen2_5-coder_3b_v1fix2_dev | qwen2_5-coder_3b_v1both_dev | qwen2_5-coder_3b_v1grounded2_dev |
|---|---|---|---|---|---|---|---|---|---|
| aggregation | 2/2 | 2/2 | 2/2 | 2/2 | 1/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| carrier | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| clinvar | 2/4 | 4/4 | 4/4 | 3/4 | 2/4 | 1/4 | 1/4 | 1/4 | 3/4 |
| frequency | 1/2 | 1/2 | 1/2 | 1/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| gene | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| indel | 1/1 | 0/1 | 0/1 | 0/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| join | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 1/2 | 1/2 | 1/2 |
| json | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| lookup | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| multistep | 0/1 | 0/1 | 0/1 | 0/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| population | 0/3 | 2/3 | 2/3 | 2/3 | 3/3 | 1/3 | 2/3 | 2/3 | 3/3 |
| region | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| samples | 0/2 | 1/2 | 0/2 | 0/2 | 1/2 | 1/2 | 2/2 | 2/2 | 2/2 |
| sv | 1/2 | 1/2 | 1/2 | 1/2 | 1/2 | 1/2 | 1/2 | 1/2 | 1/2 |
| unanswerable | 0/3 | 0/3 | 0/3 | 2/3 | 2/3 | 2/3 | 2/3 | 2/3 | 2/3 |

