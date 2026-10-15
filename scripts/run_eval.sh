#!/usr/bin/env bash
# E1 (competency questions) + E2 (audit sheet, optional LLM judge) + E3 (error injection).
#   RUN_NAME=katl_gnd JUDGE=1 bash scripts/run_eval.sh
set -euo pipefail
source "$(dirname "$0")/_env.sh"

RUN_NAME="${RUN_NAME:-katl_gnd}"
PER_RELATION="${PER_RELATION:-12}"
JUDGE="${JUDGE:-0}"
JUDGE_MODEL="${JUDGE_MODEL:-Qwen/Qwen3.5-9B}"

"$PYTHON_BIN" "$SRC_ROOT/evaluation/e1_competency.py" --run-name "$RUN_NAME"
SHEET="$PROJECT_ROOT/results/$RUN_NAME/eval/e2_audit_sheet.csv"
if [[ ! -f "$SHEET" ]]; then
  "$PYTHON_BIN" "$SRC_ROOT/evaluation/e2_precision_audit.py" sample --run-name "$RUN_NAME" --per-relation "$PER_RELATION"
fi
if [[ "$JUDGE" == "1" ]]; then
  "$PYTHON_BIN" "$SRC_ROOT/evaluation/e2_precision_audit.py" judge --run-name "$RUN_NAME" --llm-model "$JUDGE_MODEL"
fi
"$PYTHON_BIN" "$SRC_ROOT/evaluation/e2_precision_audit.py" score --run-name "$RUN_NAME" || echo "[E2] no labels yet; fill $SHEET"
"$PYTHON_BIN" "$SRC_ROOT/evaluation/e3_error_injection.py" --run-name "$RUN_NAME"
