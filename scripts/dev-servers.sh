#!/usr/bin/env bash
# Start the API (background) and the dashboard dev server (foreground).
# Usage: bash scripts/dev-servers.sh   (Ctrl-C stops the dashboard; API keeps
# running — stop it with: pkill -f "uvicorn pitchprob")
set -euo pipefail
export PATH="$HOME/.local/node/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."

if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  nohup uv run uvicorn pitchprob.api.main:app --host 0.0.0.0 --port 8000 \
    >/tmp/pitchprob-api.log 2>&1 &
  echo "API starting on :8000 (log: /tmp/pitchprob-api.log)"
fi

cd frontend
exec npm run dev -- -H 0.0.0.0
