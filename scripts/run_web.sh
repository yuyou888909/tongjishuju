#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

if ! python -c "import pandas, numpy" >/dev/null 2>&1; then
  pip install -r requirements.txt
fi

PORT="${1:-8765}"
PYTHONPATH=src python -m ashare_us_catalyst.web "$PORT"
