#!/usr/bin/env bash
# Build the Vercel payload: api/index.py + vercel.json + requirements.txt + api/bundle.tar.gz (code, index, mocks).
set -e
cd "$(dirname "$0")/.."
OUT=${1:-/workspace/vercel_payload}; mkdir -p "$OUT"
# Task materials (stubs, vehicle cards) are not in git (organizers 27.11): downloaded at deploy time from TASK_MATERIALS_URL
if [ -n "${TASK_MATERIALS_URL:-}" ]; then python3 scripts/fetch_task_materials.py; fi
test -f data/official/vehicles.json -a -f vendor/neurodvs_mock/mock_ml/app.py || { echo "no task materials: set TASK_MATERIALS_URL" >&2; exit 1; }
{ git ls-files src data/engine_docs.yaml data/demo_history.json data/demo_users.json data/vehicle_overlay.json data/i18n;
  find data/official vendor/neurodvs_mock/mock_ml vendor/neurodvs_mock/mock_toir vendor/neurodvs_mock/data -type f -not -path '*__pycache__*';
  ls data/index/*.jsonl.gz; } \
  | tar -czf "$OUT/bundle.tar.gz" -T -   # the manual indexes are not in git either (no redistribution right): taken from disk
base64 -w0 "$OUT/bundle.tar.gz" > "$OUT/bundle.b64"
ls -la "$OUT/bundle.tar.gz"
