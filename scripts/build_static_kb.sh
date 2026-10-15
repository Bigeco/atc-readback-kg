#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_env.sh"
"$PYTHON_BIN" "$SRC_ROOT/kb/build_static_kb.py" --icao "${@:-KATL}"
