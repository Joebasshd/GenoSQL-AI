# Benchmark v0 results (2026-10-14)

Hardware: i5-1135G7, 7.7 GB RAM, CPU only. Ollama, temperature 0, seed 42, num_ctx 8192.
Latency for `qwen2.5-coder_1.5b_r2` and the tail of `qwen2.5-coder_3b_norag_nosem_r0` is inflated (runs overlapped briefly); accuracy unaffected.

| run | EX relaxed % | EX strict % | abstain on unanswerable % | false abstain | failed SQL | silent wrong % | median s | tokens/q |
|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder_1.5b_norag_nosem_r0 | 15.4 | 15.4 | 0 | 0 | 14 | 59 | 6.9 | 422 |
| qwen2.5-coder_3b_norag_nosem_r0 | 15.4 | 15.4 | 17 | 3 | 3 | 72 | 10.4 | 412 |
| qwen2.5-coder_1.5b_r2 | 51.9 | 44.2 | 0 | 1 | 1 | 45 | 10.1 | 2,240 |
| qwen2.5-coder_3b_r2 | 61.5 | 53.8 | 100 | 8 | 0 | 21 | 14.0 | 1,874 |

| category | qwen2.5-coder_1.5b_norag_nosem_r0 | qwen2.5-coder_3b_norag_nosem_r0 | qwen2.5-coder_1.5b_r2 | qwen2.5-coder_3b_r2 |
|---|---|---|---|---|
| aggregation | 3/5 | 2/5 | 4/5 | 4/5 |
| clinvar | 1/9 | 0/9 | 5/9 | 4/9 |
| frequency | 1/5 | 1/5 | 4/5 | 5/5 |
| indel | 0/3 | 0/3 | 1/3 | 2/3 |
| join | 0/5 | 1/5 | 1/5 | 1/5 |
| json | 1/3 | 1/3 | 1/3 | 2/3 |
| lookup | 2/3 | 2/3 | 3/3 | 2/3 |
| multistep | 0/3 | 0/3 | 0/3 | 1/3 |
| population | 0/8 | 1/8 | 4/8 | 6/8 |
| region | 0/4 | 0/4 | 1/4 | 3/4 |
| sv | 0/4 | 0/4 | 3/4 | 2/4 |
| unanswerable | 0/6 | 1/6 | 0/6 | 6/6 |

Paired McNemar (relaxed): wins A only / wins B only / p
  qwen2.5-coder_1.5b_norag_nosem_r0 vs qwen2.5-coder_3b_norag_nosem_r0: 2 / 3 / p=1.000
  qwen2.5-coder_1.5b_norag_nosem_r0 vs qwen2.5-coder_1.5b_r2: 1 / 20 / p=0.000
  qwen2.5-coder_1.5b_norag_nosem_r0 vs qwen2.5-coder_3b_r2: 1 / 31 / p=0.000
  qwen2.5-coder_3b_norag_nosem_r0 vs qwen2.5-coder_1.5b_r2: 3 / 21 / p=0.000
  qwen2.5-coder_3b_norag_nosem_r0 vs qwen2.5-coder_3b_r2: 1 / 30 / p=0.000
  qwen2.5-coder_1.5b_r2 vs qwen2.5-coder_3b_r2: 7 / 18 / p=0.043
