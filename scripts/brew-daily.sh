#!/usr/bin/env bash
# Daily Homebrew update and greedy upgrade. Run by launchd; logs to ~/.local-ai/logs/brew.log
set -uo pipefail
LOG_DIR="${LOCAL_AI_HOME:-$HOME/.local-ai}/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/brew.log"
BREW="$(command -v brew || echo /opt/homebrew/bin/brew)"
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') brew daily ==="
  "$BREW" update
  "$BREW" upgrade --greedy
  echo "=== exit $? ==="
} >>"$LOG" 2>&1
