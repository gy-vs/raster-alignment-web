#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON:-python3}"
VENV="$ROOT/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  if "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    "$PYTHON_BIN" -m venv "$VENV"
  elif "$PYTHON_BIN" -m virtualenv --version >/dev/null 2>&1; then
    "$PYTHON_BIN" -m virtualenv "$VENV"
  elif command -v virtualenv >/dev/null 2>&1; then
    virtualenv "$VENV"
  else
    echo "Need python venv support (python3-venv) or virtualenv." >&2
    echo "The project targets Python 3.11 and rasterio 1.4.4." >&2
    exit 1
  fi
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/pip" install -r backend/requirements.txt

(
  cd frontend
  npm install
  npm run build
)

echo
echo "Setup complete. Start with: $ROOT/scripts/run.sh"
