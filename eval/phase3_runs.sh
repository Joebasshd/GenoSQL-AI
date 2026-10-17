#!/usr/bin/env bash
# Phase 3 compute chain (sequential on purpose: runs must not compete for CPU).
# Crash-safe: every step resumes from its per-item checkpoint, so just re-run
#   bash eval/phase3_runs.sh >> eval/results/phase3_log.txt 2>&1
# Trimmed for unreliable power: fastest / most useful runs first, on a fixed
# 60-item stratified test subset (eval.summarize --subset 60 compares on it).
set -u
PY=.venv/Scripts/python.exe
FILTER='===|Execution|abstention|False|Wall|accuracy|copied|McNemar|Phase 1|->|Resuming|resuming'
SUB=60
ev() { tag=$1; shift; echo "##### $tag $(date +%H:%M)"; $PY -u -m eval.run_eval --split test --subset $SUB --resume --tag "$tag" "$@" 2>&1 | grep -E "$FILTER"; }

ev v2_05b_full_sub60      --model qwen2.5-coder:0.5b

echo "##### memory_sim $(date +%H:%M)"
$PY -u -m eval.memory_sim --n 30 --model qwen2.5-coder:3b 2>&1 | grep -E "$FILTER"

ev v2_llama32_3b_full_sub60 --model llama3.2:3b
ev v2_phi4mini_full_sub60   --model phi4-mini

ev v2_3b_abl_nosemantic_sub60 --model qwen2.5-coder:3b --no-semantic
ev v2_3b_abl_norag_sub60      --model qwen2.5-coder:3b --no-rag
ev v2_3b_abl_norepair_sub60   --model qwen2.5-coder:3b --repairs 0
ev v2_3b_abl_norouting_sub60  --model qwen2.5-coder:3b --no-routing
ev v2_3b_abl_nogrounded_sub60 --model qwen2.5-coder:3b --no-grounded-abstain
echo "##### ALL DONE $(date +%H:%M)"
