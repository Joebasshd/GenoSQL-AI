#!/usr/bin/env bash
# Phase 3 compute chain (sequential on purpose: runs must not compete for CPU).
# Crash-safe: every step resumes from its per-item checkpoint, so just re-run
#   bash eval/phase3_runs.sh >> eval/results/phase3_log.txt 2>&1
set -u
PY=.venv/Scripts/python.exe
FILTER='===|Execution|abstention|False|Wall|accuracy|copied|McNemar|Phase 1|->'
ev() { tag=$1; shift; echo "##### $tag $(date +%H:%M)"; $PY -u -m eval.run_eval --split test --resume --tag "$tag" "$@" 2>&1 | grep -E "$FILTER"; }

echo "##### memory_sim $(date +%H:%M)"
$PY -u -m eval.memory_sim --n 60 --model qwen2.5-coder:3b 2>&1 | grep -E "$FILTER"

ev v2_05b_full_test   --model qwen2.5-coder:0.5b
ev v2_llama32_3b_full_test --model llama3.2:3b
ev v2_phi4mini_full_test   --model phi4-mini

ev v2_3b_abl_nosemantic_test  --model qwen2.5-coder:3b --no-semantic
ev v2_3b_abl_norag_test       --model qwen2.5-coder:3b --no-rag
ev v2_3b_abl_norepair_test    --model qwen2.5-coder:3b --repairs 0
ev v2_3b_abl_norouting_test   --model qwen2.5-coder:3b --no-routing
ev v2_3b_abl_novaluecheck_test --model qwen2.5-coder:3b --no-value-check
ev v2_3b_abl_nogrounded_test  --model qwen2.5-coder:3b --no-grounded-abstain
echo "##### ALL DONE $(date +%H:%M)"
