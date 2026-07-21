#!/usr/bin/env bash
# Weekly data refresh for the 2026/27 season (docs/RUNBOOK-2026-27.md).
#
# Schedule twice a week via Windows Task Scheduler calling:
#   wsl.exe -d Ubuntu -- bash /home/greg/projects/trading/scripts/weekly-refresh.sh
# Wednesday + Saturday mornings: football-data.co.uk refreshes its season
# files on a Friday/Tuesday cadence (audit A2), so Wednesday catches the
# Tuesday snapshot (weekend results) and Saturday catches Friday's
# (midweek rounds) before the weekend's betting.
#
# This runs LOCALLY, not in GitHub Actions, on purpose: the matches/xG
# database lives on this machine, and unlike the odds tape — where a missed
# day loses prices forever — a missed week here loses nothing. The next run
# picks up exactly where the last one left off.
#
# Exit code is the freshness gate's: Task Scheduler's "last run result"
# column doubles as the season health light.
set -euo pipefail

cd /home/greg/projects/trading
export PATH="$HOME/.local/bin:$PATH"
export PITCHPROB_DATABASE_URL="${PITCHPROB_DATABASE_URL:-sqlite:///data/pitchprob.db}"

LOG=data/weekly-refresh.log
{
  echo "=== weekly refresh $(date -u +%Y-%m-%dT%H:%MZ) ==="

  # The cloud recorder commits tape snapshots daily; pull them first so
  # import-tape sees them. --rebase because local work may be ahead of
  # origin. A dirty tree makes this fail — that is a prompt to commit, not
  # a reason to skip the rest of the refresh.
  git pull --rebase origin main || echo "WARN: git pull failed (dirty tree?) — tape may be behind"

  # Current season only (--refresh busts the cache; its file grows every
  # round). Past seasons never change and stay cached.
  uv run pitchprob ingest --all --from-year 2026 --to-year 2026 --refresh
  uv run pitchprob xg --all --from-year 2026 --to-year 2026 --refresh

  uv run pitchprob import-tape
  uv run pitchprob pick settle

  # The gate last: its exit code is the script's exit code.
  uv run pitchprob status
} >> "$LOG" 2>&1
