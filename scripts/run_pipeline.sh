#!/usr/bin/env bash
# Full pipeline: STT (M1) -> KG (M2-M5).
#   RUN_NAME=katl_gnd AIRPORT=katl EXTRACTOR=llm CUDA_VISIBLE_DEVICES=3 bash scripts/run_pipeline.sh
set -euo pipefail
source "$(dirname "$0")/_env.sh"

RUN_NAME="${RUN_NAME:-katl_gnd}"
AIRPORT="${AIRPORT:-katl}"
EXTRACTOR="${EXTRACTOR:-llm}"
LLM_MODEL="${LLM_MODEL:-Qwen/Qwen3-4B-Instruct-2507}"
REGION="${REGION:-US}"
LANGUAGE="${LANGUAGE:-en}"

"$PYTHON_BIN" "$SRC_ROOT/run_asr.py" --run-name "$RUN_NAME" --airports "$AIRPORT" --language "$LANGUAGE"
"$PYTHON_BIN" "$SRC_ROOT/build_kg.py" --run-name "$RUN_NAME" --airport "$AIRPORT" --region "$REGION" \
  --extractor "$EXTRACTOR" --llm-model "$LLM_MODEL"
