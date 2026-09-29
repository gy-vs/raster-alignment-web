#!/usr/bin/env bash
# Run the complete app locally on one port (backend serves the built UI).
# Usage: ./run.sh [--rebuild]
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "Creating Python 3.11 virtualenv…"
  python3.11 -m venv .venv
fi
if ! .venv/bin/python -c "import pip" 2>/dev/null; then
  # Some distros ship a venv without bundled pip: bootstrap from a wheel.
  echo "Bootstrapping pip into the venv…"
  PIPWHL=$(ls -t /tmp/pip-*-py3-none-any.whl 2>/dev/null | head -1 || true)
  if [ -z "$PIPWHL" ]; then
    META=$(curl -sSf --retry 5 https://pypi.org/pypi/pip/json)
    URL=$(printf '%s' "$META" | .venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print(next(f['url'] for f in d['urls'] if f['filename'].endswith('-py3-none-any.whl')))")
    curl -sSf --retry 5 -o /tmp/pip_bootstrap.whl "$URL"
    PIPWHL=/tmp/pip_bootstrap.whl
  fi
  .venv/bin/python "$PIPWHL/pip" install "$PIPWHL"
fi
if ! .venv/bin/python -c "import rasterio" 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ "${1:-}" = "--rebuild" ] || [ ! -f backend/static/index.html ]; then
  echo "Building frontend…"
  (cd frontend && npm install && npm run build)
fi

# Regenerate the small numeric samples if missing.
if [ ! -f samples/sample_a.tif ] || [ ! -f samples/sample_b.tif ]; then
  echo "Generating sample rasters…"
  .venv/bin/python scripts/generate_samples.py
fi

echo "Serving on http://127.0.0.1:8000  (Ctrl-C to stop)"
exec .venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 8000
