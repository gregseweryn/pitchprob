#!/usr/bin/env bash
# Docker Desktop 4.68 on this machine crash-loops at startup when an unclean
# shutdown leaves orphaned AF_UNIX socket files behind: Windows returns error
# 1920 when the backend tries to remove them, the failing component (Inference
# manager / Secrets Engine) takes the backend down, and the error dialog's
# factory reset makes things worse. WSL's drvfs CAN delete these reparse
# points, so we sweep them before starting the engine. Safe whenever Docker
# is not running; harmless when there is nothing to sweep.
set -euo pipefail

WINUSER="${WINUSER:-grzeg}"
LOCALAPPDATA="/mnt/c/Users/${WINUSER}/AppData/Local"

rm -f \
  "${LOCALAPPDATA}/Docker/run/dockerInference" \
  "${LOCALAPPDATA}/Docker/run/userAnalyticsOtlpHttp.sock" \
  "${LOCALAPPDATA}/Docker/run/"*.sock \
  "${LOCALAPPDATA}/docker-secrets-engine/engine.sock" \
  2>/dev/null || true

echo "docker-preflight: stale socket sweep done"
