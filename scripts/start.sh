#!/usr/bin/env bash
# Open pitchprob: freshness gate, API, dashboard, browser. One command.
#
# From Windows, double-click scripts/pitchprob.bat (or pin it to the
# taskbar); it calls this. Ctrl-C stops the dashboard and the API.
#
# The database lives in the cloud (Neon), so this starts a *viewer* over
# data that stayed current while the machine was off — there is nothing to
# catch up on, and nothing here writes to the tape.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/node/bin:$HOME/.local/bin:$PATH"

API_PORT=8000
WEB_PORT=3000

cleanup() {
  # Only kill what this script started; a pre-existing API keeps running.
  [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "== Czy dane są świeże? =="
# Never fatal: a red gate is information for the operator, not a reason to
# refuse to open the dashboard. Betting on stale data is the mistake; the
# scanner will independently verdict STALE if the anchor is old.
uv run pitchprob status || true
echo

if curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
  echo "== API już działa na :$API_PORT =="
else
  echo "== Startuję API na :$API_PORT =="
  uv run uvicorn pitchprob.api.main:app --host 0.0.0.0 --port "$API_PORT" \
    > /tmp/pitchprob-api.log 2>&1 &
  API_PID=$!
  for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  if ! curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
    echo "API nie wstało. Log: /tmp/pitchprob-api.log" >&2
    tail -20 /tmp/pitchprob-api.log >&2
    exit 1
  fi
fi

# explorer.exe is the WSL bridge to the Windows default browser; harmless
# (and silent) if this ever runs somewhere else.
( sleep 4; explorer.exe "http://localhost:$WEB_PORT" >/dev/null 2>&1 || true ) &

echo "== Dashboard: http://localhost:$WEB_PORT  (Ctrl-C kończy) =="
cd frontend
exec npm run dev -- --port "$WEB_PORT" -H 0.0.0.0
