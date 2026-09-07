#!/usr/bin/env bash
# Start the dashboard (scrapes first if the DB is still empty).
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || { echo "run: uv venv && uv pip install -e ."; exit 1; }

if [ ! -f data/hackathons.db ]; then
  echo "First run — scraping…"
  "$PY" -m scraper.run
fi

exec .venv/bin/uvicorn api.main:app --port 8765 --reload
