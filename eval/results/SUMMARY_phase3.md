# Phase 3 results (2026-10-18)

Hardware: i5-1135G7, 7.7 GB RAM, CPU only. Ollama, temperature 0, seed 42. Pipeline configuration frozen since v1.
Model comparison and ablations use a **fixed 60-item category-stratified subset of the v2 test split** (`--subset 60`; 54 answerable + 6 unanswerable) because of compute and power constraints; 3B full scores 53.7 % on it vs 53.0 % on the full 148-item test, so the subset is representative. Runs labelled `_test` were run on the full test split and are restricted to the subset here.

## Model comparison (full pipeline)

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| v2_05b_full_sub60 | 14.8 | 13.0 | 17 | 14 | 5 | 52 | 14.7 | 4,882 |
| v2_15b_full_test | 40.7 | 29.6 | 17 | 16 | 2 | 32 | 10.0 | 4,669 |
| v2_llama32_3b_full_sub60 | 31.5 | 29.6 | 0 | 5 | 2 | 60 | 29.9 | 4,153 |
| v2_phi4mini_full_sub60 | 27.8 | 18.5 | 33 | 7 | 8 | 45 | 32.9 | 5,039 |
| v2_3b_full_test | 53.7 | 40.7 | 33 | 2 | 1 | 43 | 24.2 | 3,738 |

| category | v2_05b_full_sub60 | v2_15b_full_test | v2_llama32_3b_full_sub60 | v2_phi4mini_full_sub60 | v2_3b_full_test |
|---|---|---|---|---|---|
| aggregation | 2/4 | 3/4 | 3/4 | 2/4 | 4/4 |
| carrier | 0/4 | 0/4 | 0/4 | 0/4 | 0/4 |
| clinvar | 0/5 | 2/5 | 2/5 | 2/5 | 1/5 |
| frequency | 0/4 | 2/4 | 1/4 | 1/4 | 4/4 |
| gene | 2/4 | 4/4 | 4/4 | 3/4 | 4/4 |
| indel | 0/2 | 1/2 | 1/2 | 1/2 | 1/2 |
| join | 0/6 | 1/6 | 0/6 | 0/6 | 0/6 |
| json | 0/3 | 0/3 | 1/3 | 0/3 | 1/3 |
| lookup | 0/3 | 1/3 | 0/3 | 0/3 | 1/3 |
| multistep | 0/4 | 0/4 | 0/4 | 1/4 | 2/4 |
| population | 1/5 | 3/5 | 2/5 | 2/5 | 3/5 |
| region | 1/4 | 2/4 | 0/4 | 0/4 | 4/4 |
| samples | 1/3 | 2/3 | 1/3 | 1/3 | 2/3 |
| sv | 1/3 | 1/3 | 2/3 | 2/3 | 2/3 |
| unanswerable | 1/6 | 1/6 | 0/6 | 2/6 | 2/6 |

Paired McNemar (relaxed): wins A only / wins B only / p
  v2_05b_full_sub60 vs v2_15b_full_test: 2 / 16 / p=0.001
  v2_05b_full_sub60 vs v2_llama32_3b_full_sub60: 3 / 11 / p=0.057
  v2_05b_full_sub60 vs v2_phi4mini_full_sub60: 2 / 10 / p=0.039
  v2_05b_full_sub60 vs v2_3b_full_test: 0 / 22 / p=0.000
  v2_15b_full_test vs v2_llama32_3b_full_sub60: 9 / 3 / p=0.146
  v2_15b_full_test vs v2_phi4mini_full_sub60: 10 / 4 / p=0.180
  v2_15b_full_test vs v2_3b_full_test: 4 / 12 / p=0.077
  v2_llama32_3b_full_sub60 vs v2_phi4mini_full_sub60: 8 / 8 / p=1.000
  v2_llama32_3b_full_sub60 vs v2_3b_full_test: 2 / 16 / p=0.001
  v2_phi4mini_full_sub60 vs v2_3b_full_test: 4 / 18 / p=0.004

## Ablation (qwen2.5-coder:3b, remove one component)

`nosemantic` also removes the routing rules (they are part of the semantic layer). `bare` = no semantic layer, examples, repair, value check or grounded abstention.

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| v2_3b_full_test | 53.7 | 40.7 | 33 | 2 | 1 | 43 | 24.2 | 3,738 |
| v2_3b_abl_nosemantic_sub60 | 40.7 | 27.8 | 0 | 3 | 5 | 48 | 12.3 | 1,216 |
| v2_3b_abl_norag_sub60 | 42.6 | 37.0 | 33 | 9 | 2 | 40 | 11.6 | 4,118 |
| v2_3b_abl_norepair_sub60 | 51.9 | 38.9 | 33 | 8 | 4 | 30 | 16.3 | 2,713 |
| v2_3b_abl_norouting_sub60 | 46.3 | 33.3 | 0 | 4 | 6 | 42 | 31.2 | 4,033 |
| v2_3b_abl_nogrounded_sub60 | 53.7 | 40.7 | 33 | 0 | 3 | 43 | 42.3 | 3,837 |
| v2_3b_bare_test | 13.0 | 11.1 | 0 | 0 | 21 | 50 | 9.4 | 571 |

| category | v2_3b_full_test | v2_3b_abl_nosemantic_sub60 | v2_3b_abl_norag_sub60 | v2_3b_abl_norepair_sub60 | v2_3b_abl_norouting_sub60 | v2_3b_abl_nogrounded_sub60 | v2_3b_bare_test |
|---|---|---|---|---|---|---|---|
| aggregation | 4/4 | 4/4 | 3/4 | 4/4 | 3/4 | 4/4 | 3/4 |
| carrier | 0/4 | 0/4 | 0/4 | 0/4 | 0/4 | 0/4 | 0/4 |
| clinvar | 1/5 | 2/5 | 0/5 | 1/5 | 2/5 | 1/5 | 1/5 |
| frequency | 4/4 | 2/4 | 2/4 | 4/4 | 4/4 | 4/4 | 0/4 |
| gene | 4/4 | 2/4 | 4/4 | 4/4 | 3/4 | 4/4 | 1/4 |
| indel | 1/2 | 1/2 | 2/2 | 1/2 | 2/2 | 1/2 | 0/2 |
| join | 0/6 | 0/6 | 2/6 | 0/6 | 0/6 | 0/6 | 0/6 |
| json | 1/3 | 0/3 | 1/3 | 1/3 | 1/3 | 1/3 | 0/3 |
| lookup | 1/3 | 2/3 | 1/3 | 1/3 | 0/3 | 1/3 | 0/3 |
| multistep | 2/4 | 0/4 | 2/4 | 1/4 | 0/4 | 2/4 | 0/4 |
| population | 3/5 | 3/5 | 0/5 | 3/5 | 3/5 | 3/5 | 0/5 |
| region | 4/4 | 3/4 | 2/4 | 4/4 | 3/4 | 4/4 | 1/4 |
| samples | 2/3 | 0/3 | 2/3 | 2/3 | 2/3 | 2/3 | 0/3 |
| sv | 2/3 | 3/3 | 2/3 | 2/3 | 2/3 | 2/3 | 1/3 |
| unanswerable | 2/6 | 0/6 | 2/6 | 2/6 | 0/6 | 2/6 | 0/6 |

Paired McNemar (relaxed): wins A only / wins B only / p
  v2_3b_full_test vs v2_3b_abl_nosemantic_sub60: 12 / 3 / p=0.035
  v2_3b_full_test vs v2_3b_abl_norag_sub60: 11 / 5 / p=0.210
  v2_3b_full_test vs v2_3b_abl_norepair_sub60: 1 / 0 / p=1.000
  v2_3b_full_test vs v2_3b_abl_norouting_sub60: 8 / 2 / p=0.109
  v2_3b_full_test vs v2_3b_abl_nogrounded_sub60: 0 / 0 / p=1.000
  v2_3b_full_test vs v2_3b_bare_test: 26 / 2 / p=0.000
  v2_3b_abl_nosemantic_sub60 vs v2_3b_abl_norag_sub60: 11 / 14 / p=0.690
  v2_3b_abl_nosemantic_sub60 vs v2_3b_abl_norepair_sub60: 3 / 11 / p=0.057
  v2_3b_abl_nosemantic_sub60 vs v2_3b_abl_norouting_sub60: 6 / 9 / p=0.607
  v2_3b_abl_nosemantic_sub60 vs v2_3b_abl_nogrounded_sub60: 3 / 12 / p=0.035
  v2_3b_abl_nosemantic_sub60 vs v2_3b_bare_test: 15 / 0 / p=0.000
  v2_3b_abl_norag_sub60 vs v2_3b_abl_norepair_sub60: 6 / 11 / p=0.332
  v2_3b_abl_norag_sub60 vs v2_3b_abl_norouting_sub60: 10 / 10 / p=1.000
  v2_3b_abl_norag_sub60 vs v2_3b_abl_nogrounded_sub60: 5 / 11 / p=0.210
  v2_3b_abl_norag_sub60 vs v2_3b_bare_test: 19 / 1 / p=0.000
  v2_3b_abl_norepair_sub60 vs v2_3b_abl_norouting_sub60: 7 / 2 / p=0.180
  v2_3b_abl_norepair_sub60 vs v2_3b_abl_nogrounded_sub60: 0 / 1 / p=1.000
  v2_3b_abl_norepair_sub60 vs v2_3b_bare_test: 25 / 2 / p=0.000
  v2_3b_abl_norouting_sub60 vs v2_3b_abl_nogrounded_sub60: 2 / 8 / p=0.109
  v2_3b_abl_norouting_sub60 vs v2_3b_bare_test: 21 / 3 / p=0.000
  v2_3b_abl_nogrounded_sub60 vs v2_3b_bare_test: 26 / 2 / p=0.000

## Memory poisoning (qwen2.5-coder:3b, 30 question pairs)

Phase 1: 30 benchmark questions in expert phrasing, seed-only memory -> 56.7 % correct; 12 answers were
wrong but executed (a user would have seen a plausible table). Phase 2: the same questions in lay
phrasing, under four memory policies built from phase 1 (`eval/memory_sim.py`).

| memory policy | stored examples | lay accuracy | phase-1 errors reproduced |
|---|---|---|---|
| none (seeds only) | 0 | 46.7 % | 2 / 12 (independent repeats) |
| auto: store every answer that executed | 29 | 56.7 % | **7 / 12** |
| verified: store only correct answers (user ✓) | 17 | 56.7 % | **0 / 12** |
| corrected: store gold SQL (user fix) | 30 | **86.7 %** | 0 / 12 |

- Unverified "self-learning" memory buys the same accuracy as verified memory but **reproduces 7 of 12
  earlier errors** (vs 2 by chance without memory): wrong answers become few-shot "truth" for paraphrases.
- Verified memory removes error propagation entirely at no accuracy cost.
- Expert corrections are by far the most valuable feedback: verified -> corrected 0/9 wins, p = 0.004.

## Adversarial safety (`eval/results/safety.json`)

33 direct SQL attacks scored per layer + 15 natural-language attacks through the 3B pipeline, with canary
rows in protected tables: every direct attack stopped by at least one layer (guard 29/33, database alone
30/33); 0 canary leaks; 0 table row-count changes; DoS bounded by the 15 s statement timeout (max 16.9 s)
and the streamed row cap. NL attacks: 11 declined, 4 rewritten by the model into harmless reads.

## Scaling within one family (full 148-item test)

| model | EX | false declines | silent wrong |
|---|---|---|---|
| qwen2.5-coder:0.5b | 13.4 % | 41 | 43 % |
| qwen2.5-coder:1.5b | 33.6 % | 30 | 40 % |
| qwen2.5-coder:3b | 53.0 % | 10 | 39 % |

Reproducibility: two independent 0.5B runs (full test, then the 60-item subset) produced identical
correctness on all 60 shared items (McNemar 0/0).
