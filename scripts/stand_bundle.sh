#!/usr/bin/env bash
# Archive for the deb stand: the committed tree (HEAD) + the manual indexes that are kept out of the public repo.
set -e
cd "$(dirname "$0")/.."
OUT=${1:-/workspace/app_update.tar.gz}
test -z "$(git status --porcelain --untracked-files=no)" || { echo "commit first: the stand gets HEAD" >&2; exit 1; }
TMP=$(mktemp -d); git archive HEAD | tar -x -C "$TMP"
mkdir -p "$TMP/data/index" && cp data/index/*.jsonl.gz "$TMP/data/index/"
tar -czf "$OUT" -C "$TMP" . && rm -rf "$TMP"
ls -la "$OUT"
