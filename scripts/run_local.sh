#!/usr/bin/env bash
# Local run without Docker: official NeuroDVS mocks (8001, 8002) + app (8000).
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] && . .venv/bin/activate
export PYTHONPATH="$PWD/src"
[ -f .env ] && set -a && . ./.env && set +a
[ -z "$LLM_API_KEY" ] && [ -z "$LLM_API_KEY_FILE" ] && [ -f /workspace/.tokenrouter_key ] && export LLM_API_KEY_FILE=/workspace/.tokenrouter_key
(cd vendor/neurodvs_mock && uvicorn mock_ml.app:app --port 8001 --log-level warning) &
(cd vendor/neurodvs_mock && uvicorn mock_toir.app:app --port 8002 --log-level warning) &
trap 'kill 0' EXIT
uvicorn digital_mechanic.web.app:app --host 0.0.0.0 --port "${PORT:-8000}"
