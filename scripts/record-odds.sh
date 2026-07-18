#!/usr/bin/env bash
# One odds-tape snapshot (ADR 0012). Schedule daily via Windows Task
# Scheduler calling:
#   wsl.exe -d Ubuntu -- bash /home/greg/projects/trading/scripts/record-odds.sh
# Cost: ~15 API credits per run with all leagues and markets (500/month free
# tier => one run per day fits with headroom).
set -euo pipefail

cd /home/greg/projects/trading
export PATH="$HOME/.local/bin:$PATH"
export PITCHPROB_DATABASE_URL="${PITCHPROB_DATABASE_URL:-sqlite:///data/pitchprob.db}"

uv run pitchprob record-odds --league all --markets h2h,totals,spreads \
  >> data/record-odds.log 2>&1
