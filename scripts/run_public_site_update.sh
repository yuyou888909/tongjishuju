#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] Starting public site update"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

if ! python -c "import akshare" >/dev/null 2>&1; then
  pip install -r requirements.txt
fi

PYTHONPATH=src python scripts/build_static_site.py

if ! npx netlify status >/dev/null 2>&1; then
  echo "Netlify CLI is not authenticated or the site is not linked."
  echo "Run: npx netlify login && npx netlify link"
  exit 1
fi

npx netlify deploy \
  --prod \
  --dir=dist \
  --functions=netlify/functions \
  --message "Daily 9AM public report $(date '+%Y-%m-%d')"

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] Public site update finished"
