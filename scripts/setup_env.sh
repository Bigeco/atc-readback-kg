#!/usr/bin/env bash
set -euo pipefail

CONDA_ENV="${CONDA_ENV:-atc-kg}"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda create -y -n "$CONDA_ENV" python=3.11
conda activate "$CONDA_ENV"
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r "$(dirname "$0")/../requirements.txt"
echo "[setup] done. ffmpeg must be on PATH for audio decoding."
