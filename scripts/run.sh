#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Run scripts/setup.sh first." >&2
  exit 1
fi

if [ ! -f samples/sample_a_utm.tif ] || [ ! -f samples/sample_b_wgs84.tif ]; then
  "$PY" scripts/make_samples.py
fi

if [ ! -d frontend/dist ]; then
  (cd frontend && npm install && npm run build)
fi

export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000
