#!/usr/bin/env bash
# Export the demo as a static site and publish it to the gh-pages branch (GitHub Pages).
#   RUN_NAME=katl_gnd bash scripts/export_site.sh            # export only -> results/<run>/site
#   RUN_NAME=katl_gnd PUBLISH=1 bash scripts/export_site.sh  # export + push to gh-pages
# Audio is never exported (LiveATC terms).
set -euo pipefail
source "$(dirname "$0")/_env.sh"

RUN_NAME="${RUN_NAME:-katl_gnd}"
SITE_DIR="$PROJECT_ROOT/results/$RUN_NAME/site"
PAGES_BRANCH="${PAGES_BRANCH:-gh-pages}"

"$PYTHON_BIN" "$SRC_ROOT/demo/export_static.py" --run-name "$RUN_NAME" --out-dir "$SITE_DIR"
[[ "${PUBLISH:-0}" == "1" ]] || exit 0

WORKTREE="$(mktemp -d)"
trap 'git -C "$PROJECT_ROOT" worktree remove --force "$WORKTREE" 2>/dev/null || true' EXIT
if git -C "$PROJECT_ROOT" ls-remote --exit-code --heads origin "$PAGES_BRANCH" >/dev/null 2>&1; then
  git -C "$PROJECT_ROOT" fetch origin "$PAGES_BRANCH"
  git -C "$PROJECT_ROOT" worktree add "$WORKTREE" "origin/$PAGES_BRANCH" --detach
else
  git -C "$PROJECT_ROOT" worktree add --detach "$WORKTREE"
  git -C "$WORKTREE" checkout --orphan "$PAGES_BRANCH"
fi
git -C "$WORKTREE" rm -rq . 2>/dev/null || true
cp -r "$SITE_DIR"/. "$WORKTREE"/
git -C "$WORKTREE" add -A
git -C "$WORKTREE" commit -q -m "Publish static demo ($RUN_NAME)" || echo "[site] nothing to publish"
git -C "$WORKTREE" push -f origin "HEAD:$PAGES_BRANCH"
echo "[site] published to $PAGES_BRANCH"
