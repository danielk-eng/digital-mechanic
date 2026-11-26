#!/usr/bin/env bash
# Start mock ML (8101), mock TOIR (8102) and the web app (8000). Official stubs replace the mocks.
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] && . .venv/bin/activate
export PYTHONPATH="$PWD/src"
[ -z "$LLM_API_KEY" ] && [ -f /workspace/.tokenrouter_key ] && export LLM_API_KEY="$(cat /workspace/.tokenrouter_key)"
uvicorn digital_mechanic.stubs.ml_stub:app --port 8101 --log-level warning &
uvicorn digital_mechanic.stubs.toir_stub:app --port 8102 --log-level warning &
uvicorn digital_mechanic.demo.app:app --host 0.0.0.0 --port 8000
