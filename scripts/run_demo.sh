#!/usr/bin/env bash
#   RUN_NAME=katl_gnd PORT=8800 bash scripts/run_demo.sh   ->  http://127.0.0.1:8800
set -euo pipefail
source "$(dirname "$0")/_env.sh"
"$PYTHON_BIN" "$SRC_ROOT/demo/server.py" --run-name "${RUN_NAME:-katl_gnd}" --host "${HOST:-127.0.0.1}" --port "${PORT:-8800}"
