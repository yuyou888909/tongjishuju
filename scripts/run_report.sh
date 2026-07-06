#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

if ! python -c "import akshare" >/dev/null 2>&1; then
  pip install -r requirements.txt
fi

EXTRA_ARGS=("$@")
if [ -f ".env" ] && grep -q '^TELEGRAM_BOT_TOKEN=.' .env && grep -q '^TELEGRAM_CHAT_ID=.' .env; then
  EXTRA_ARGS+=(--send-telegram)
fi

if [ "${#EXTRA_ARGS[@]}" -gt 0 ]; then
  PYTHONPATH=src python -m ashare_us_catalyst.cli --top 5 "${EXTRA_ARGS[@]}"
else
  PYTHONPATH=src python -m ashare_us_catalyst.cli --top 5
fi
