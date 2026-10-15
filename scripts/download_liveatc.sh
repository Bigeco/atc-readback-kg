#!/usr/bin/env bash
# Download selected airport archives from the HF LiveATC dataset and keep only the wanted feeds.
#   bash scripts/download_liveatc.sh katl "Gnd|Del|Dep"
# LiveATC audio is for personal, non-commercial use; never redistribute it.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

AIRPORT="${1:?airport archive name, e.g. katl}"
FEED_REGEX="${2:-Gnd|Del|Dep|Twr|App}"
DEST="$PROJECT_ROOT/data/raw/liveatc"
URL="https://huggingface.co/datasets/nyuuzyou/liveatc/resolve/main/data/${AIRPORT}.tar.zst"

mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "[download] $URL"
curl -L --fail -o "$TMP/archive.tar.zst" "$URL"
zstd -dc "$TMP/archive.tar.zst" | tar -t | grep -E "($FEED_REGEX)" | grep -E '\.mp3$' > "$TMP/members.txt" || true
echo "[download] $(wc -l < "$TMP/members.txt") files match /$FEED_REGEX/"
zstd -dc "$TMP/archive.tar.zst" | tar -x -C "$DEST" -T "$TMP/members.txt"
find "$DEST/$AIRPORT" -name '*.mp3' -size 0 -delete
echo "[download] non-empty files: $(find "$DEST/$AIRPORT" -name '*.mp3' | wc -l) -> $DEST/$AIRPORT"
