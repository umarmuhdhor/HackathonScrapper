#!/usr/bin/env bash
# Daily job: scrape all sources, read eligibility rules, write the top-5 digest.
# Run by launchd (see install-daily.sh) or by hand.
set -uo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || { echo "venv missing — run: uv venv && uv pip install httpx fastapi 'uvicorn[standard]'"; exit 1; }

mkdir -p data/digests
LOG=data/digests/daily.log
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

if ! "$PY" -m scraper.digest --top 5 >> "$LOG" 2>&1; then
  echo "digest FAILED" >> "$LOG"
  osascript -e 'display notification "Cek data/digests/daily.log" with title "Hackathon digest gagal"' 2>/dev/null
  exit 1
fi

TODAY=$(date '+%Y-%m-%d')
DIGEST="data/digests/$TODAY.json"

# First line of the digest becomes the notification body.
if [ -f "$DIGEST" ]; then
  TOP=$("$PY" -c "
import json,sys
d=json.load(open('$DIGEST'))
t=d['top']
print(f\"{t[0]['title'][:60]} ({t[0]['score']}/100)\" if t else 'Tidak ada kandidat hari ini')
" 2>/dev/null)
  COUNT=$("$PY" -c "import json;print(len(json.load(open('$DIGEST'))['top']))" 2>/dev/null)
  osascript -e "display notification \"$TOP\" with title \"Top $COUNT hackathon minggu ini\" subtitle \"$TODAY\"" 2>/dev/null
fi

echo "digest ok — data/digests/$TODAY.md" >> "$LOG"
