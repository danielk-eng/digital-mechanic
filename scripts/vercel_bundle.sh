#!/usr/bin/env bash
# Build the Vercel payload: api/index.py + vercel.json + requirements.txt + api/bundle.tar.gz (code, index, mocks).
set -e
cd "$(dirname "$0")/.."
OUT=${1:-/workspace/vercel_payload}; mkdir -p "$OUT"
git ls-files src data/index data/official data/engine_docs.yaml data/demo_history.json data/demo_users.json vendor/neurodvs_mock/mock_ml vendor/neurodvs_mock/mock_toir vendor/neurodvs_mock/data \
  | tar -czf "$OUT/bundle.tar.gz" -T -
base64 -w0 "$OUT/bundle.tar.gz" > "$OUT/bundle.b64"
ls -la "$OUT/bundle.tar.gz"
