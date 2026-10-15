# Benchmark v2 results (2026-10-15)

Benchmark: GenoSQL-Bench v2, 246 items, held-out **test split = 148** (134 answerable + 14 unanswerable); 105 of the test items are new LLM-annotated items never inspected against model outputs ([datasheet](../BENCHMARK.md)).
Pipeline configuration frozen since v1 (no tuning on v2). Hardware: i5-1135G7, 7.7 GB RAM, CPU only, under heavy memory pressure (latencies are conservative). Ollama, temperature 0, seed 42, num_ctx 8192.

"full" = semantic layer + routing rules + 3 verified examples + up to 2 repairs + category-value check + grounded abstention. "bare" = table/column names only.
Note: one 1.5B-bare item (JSO09) looped until the 600 s HTTP timeout (scored wrong); an output cap (LLM_MAX_TOKENS=512) was added afterwards — the largest real answer in these runs averaged 345 tokens/attempt.

## All test items

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| v2_15b_bare_test | 8.2 | 7.5 | 0 | 0 | 67 | 41 | 6.3 | 564 |
| v2_15b_full_test | 33.6 | 29.1 | 43 | 30 | 8 | 40 | 10.4 | 4,782 |
| v2_3b_bare_test | 14.9 | 13.4 | 0 | 0 | 47 | 49 | 9.3 | 569 |
| v2_3b_full_test | 53.0 | 45.5 | 43 | 10 | 4 | 39 | 25.9 | 3,939 |

| category | v2_15b_bare_test | v2_15b_full_test | v2_3b_bare_test | v2_3b_full_test |
|---|---|---|---|---|
| aggregation | 5/10 | 5/10 | 6/10 | 8/10 |
| carrier | 0/10 | 0/10 | 0/10 | 1/10 |
| clinvar | 0/13 | 4/13 | 2/13 | 4/13 |
| frequency | 1/9 | 6/9 | 3/9 | 8/9 |
| gene | 2/9 | 8/9 | 3/9 | 8/9 |
| indel | 0/6 | 4/6 | 0/6 | 4/6 |
| join | 0/14 | 1/14 | 0/14 | 3/14 |
| json | 0/7 | 0/7 | 0/7 | 4/7 |
| lookup | 0/7 | 3/7 | 1/7 | 4/7 |
| multistep | 0/11 | 0/11 | 0/11 | 2/11 |
| population | 0/13 | 4/13 | 0/13 | 8/13 |
| region | 2/10 | 3/10 | 2/10 | 7/10 |
| samples | 1/8 | 4/8 | 2/8 | 4/8 |
| sv | 0/7 | 3/7 | 1/7 | 6/7 |
| unanswerable | 0/14 | 6/14 | 0/14 | 6/14 |

Paired McNemar (relaxed): wins A only / wins B only / p
  v2_15b_bare_test vs v2_15b_full_test: 3 / 43 / p=0.000
  v2_15b_bare_test vs v2_3b_bare_test: 5 / 14 / p=0.064
  v2_15b_bare_test vs v2_3b_full_test: 1 / 67 / p=0.000
  v2_15b_full_test vs v2_3b_bare_test: 37 / 6 / p=0.000
  v2_15b_full_test vs v2_3b_full_test: 7 / 33 / p=0.000
  v2_3b_bare_test vs v2_3b_full_test: 4 / 61 / p=0.000

## Never-inspected generated test items only

Split: all, source: generated (105 items)

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| v2_15b_bare_test | 8.4 | 7.4 | 0 | 0 | 50 | 36 | 6.5 | 565 |
| v2_15b_full_test | 24.2 | 20.0 | 40 | 25 | 8 | 43 | 11.9 | 5,171 |
| v2_3b_bare_test | 14.7 | 13.7 | 0 | 0 | 37 | 45 | 9.8 | 579 |
| v2_3b_full_test | 49.5 | 40.0 | 40 | 8 | 4 | 40 | 28.4 | 4,149 |

| category | v2_15b_bare_test | v2_15b_full_test | v2_3b_bare_test | v2_3b_full_test |
|---|---|---|---|---|
| aggregation | 3/7 | 3/7 | 5/7 | 6/7 |
| carrier | 0/7 | 0/7 | 0/7 | 0/7 |
| clinvar | 0/8 | 3/8 | 2/8 | 2/8 |
| frequency | 1/6 | 3/6 | 1/6 | 5/6 |
| gene | 1/6 | 5/6 | 2/6 | 5/6 |
| indel | 0/4 | 2/4 | 0/4 | 2/4 |
| join | 0/11 | 0/11 | 0/11 | 2/11 |
| json | 0/5 | 0/5 | 0/5 | 3/5 |
| lookup | 0/5 | 1/5 | 0/5 | 3/5 |
| multistep | 0/9 | 0/9 | 0/9 | 2/9 |
| population | 0/8 | 0/8 | 0/8 | 4/8 |
| region | 2/8 | 2/8 | 2/8 | 6/8 |
| samples | 1/6 | 2/6 | 1/6 | 3/6 |
| sv | 0/5 | 2/5 | 1/5 | 4/5 |
| unanswerable | 0/10 | 4/10 | 0/10 | 4/10 |


## Hand-written test items only (inspected during v0/v1 development)

Split: all, source: hand (43 items)

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| v2_15b_bare_test | 7.7 | 7.7 | 0 | 0 | 17 | 51 | 5.7 | 561 |
| v2_15b_full_test | 56.4 | 51.3 | 50 | 5 | 0 | 33 | 8.4 | 3,834 |
| v2_3b_bare_test | 15.4 | 12.8 | 0 | 0 | 10 | 60 | 7.9 | 547 |
| v2_3b_full_test | 61.5 | 59.0 | 50 | 2 | 0 | 35 | 22.9 | 3,428 |


## Paraphrase robustness (3B full, 105 generated test items, expert vs lay phrasing)

| phrasing | EX on 95 answerable | declines on 10 unanswerable |
|---|---|---|
| expert (original) | 49.5 % | 4/10 |
| lay (biologist, no schema vocabulary) | 36.8 % | 5/10 |

Paired McNemar: expert-only 20 / lay-only 9 wins, p = 0.061.
