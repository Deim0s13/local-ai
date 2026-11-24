#!/usr/bin/env bash
# Daily Homebrew update and greedy upgrade. Run by launchd; logs to ~/.local-ai/logs/brew.log
# Fully non-interactive: brew never asks yes/no, and anything needing a password
# (some cask installers use sudo) fails fast and is logged instead of hanging.
set -uo pipefail
export NONINTERACTIVE=1
export HOMEBREW_NO_ENV_HINTS=1
export HOMEBREW_NO_AUTO_UPDATE=1      # we run brew update ourselves, once
export SUDO_ASKPASS=/usr/bin/false     # sudo can't prompt: fails immediately
LOG_DIR="${LOCAL_AI_HOME:-$HOME/.local-ai}/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/brew.log"
BREW="$(command -v brew || echo /opt/homebrew/bin/brew)"
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') brew daily ==="
  "$BREW" update
  "$BREW" upgrade --greedy
  rc=$?
  [[ $rc -ne 0 ]] && echo "!!! Some upgrades failed (often casks needing a password). Run 'brew upgrade --greedy' by hand to finish them."
  echo "=== exit $rc ==="
} </dev/null >>"$LOG" 2>&1
