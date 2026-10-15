#!/usr/bin/env bash
# Sharded pipeline: ASR shards (by feed) -> M4 shards (by turn) -> merge + M5-M7 -> evaluation.
# A GPU id may repeat to run several processes on one GPU (Whisper needs ~5 GB, the 4B LLM ~20 GB).
#   ASR_GPUS="4 5 6 7" M4_GPUS="4 5 6 7" RUN_NAME=katl_all bash scripts/run_parallel.sh   # 4 GPUs
#   ASR_GPUS="4 4 4"   M4_GPUS="4"       RUN_NAME=katl_all bash scripts/run_parallel.sh   # 1 GPU
set -euo pipefail
source "$(dirname "$0")/_env.sh"

ASR_GPUS=(${ASR_GPUS:-${GPUS:-4 5 6 7}})
M4_GPUS=(${M4_GPUS:-${GPUS:-4 5 6 7}})
RUN_NAME="${RUN_NAME:-katl_all}"
AIRPORT="${AIRPORT:-katl}"
FACILITIES="${FACILITIES:-GND DEL TWR DEP APP}"
LLM_MODEL="${LLM_MODEL:-Qwen/Qwen3-4B-Instruct-2507}"
LOG_DIR="$PROJECT_ROOT/results/$RUN_NAME/logs"
mkdir -p "$LOG_DIR"

run_shards() {  # $1 = stage name, $2 = space-separated GPU list, rest = command ($SHARD_INDEX per process)
  local stage="$1"; local gpus=($2); shift 2
  local pids=()
  for i in "${!gpus[@]}"; do
    CUDA_VISIBLE_DEVICES="${gpus[$i]}" SHARD_INDEX="$i" bash -c "$*" > "$LOG_DIR/${stage}.shard$i.log" 2>&1 &
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid"; done
  echo "[$(date +%T)] $stage done"
}

echo "[$(date +%T)] ASR: ${#ASR_GPUS[@]} processes on GPUs ${ASR_GPUS[*]}"
run_shards asr "${ASR_GPUS[*]}" "$PYTHON_BIN $SRC_ROOT/run_asr.py --run-name $RUN_NAME --airports $AIRPORT \
  --facilities $FACILITIES --num-shards ${#ASR_GPUS[@]} --shard-index \$SHARD_INDEX"

echo "[$(date +%T)] M4: ${#M4_GPUS[@]} processes on GPUs ${M4_GPUS[*]}"
run_shards m4 "${M4_GPUS[*]}" "$PYTHON_BIN $SRC_ROOT/build_kg.py --run-name $RUN_NAME --airport $AIRPORT \
  --extractor llm --llm-model $LLM_MODEL --m4-shard \$SHARD_INDEX/${#M4_GPUS[@]}"
N=${#M4_GPUS[@]}

echo "[$(date +%T)] merge + M5-M7"
"$PYTHON_BIN" "$SRC_ROOT/build_kg.py" --run-name "$RUN_NAME" --airport "$AIRPORT" --extractor llm \
  --llm-model "$LLM_MODEL" --merge-shards "$N" > "$LOG_DIR/build.log" 2>&1
tail -3 "$LOG_DIR/build.log"

echo "[$(date +%T)] evaluation"
CUDA_VISIBLE_DEVICES="${M4_GPUS[0]}" RUN_NAME="$RUN_NAME" bash "$SCRIPT_DIR/run_eval.sh" > "$LOG_DIR/eval.log" 2>&1 || true
"$PYTHON_BIN" "$SRC_ROOT/evaluation/e4_rq4_diagnosis.py" template --run-name "$RUN_NAME" --n 20 >> "$LOG_DIR/eval.log" 2>&1
grep -E "^\[E" "$LOG_DIR/eval.log" || true
echo "[$(date +%T)] all done -> results/$RUN_NAME"
