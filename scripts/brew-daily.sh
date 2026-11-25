#!/usr/bin/env bash
# Daily Homebrew update and greedy upgrade, run by launchd at 09:30.
# Skips Saturday and Sunday; run `scripts/brew-daily.sh --force` to run anyway.
# - Answers "yes" to any confirmation prompt, never waits for input.
# - Apps whose installers need your password can't be done unattended:
#   they fail fast, are listed in the summary, and you get a notification.
# Output:
#   ~/.local-ai/logs/brew.log             full log, appended daily
#   ~/.local-ai/logs/brew-summary.md      today's short report
set -uo pipefail
if [[ "$(date +%u)" -ge 6 && "${1:-}" != "--force" ]]; then
  exit 0   # weekend: the work Mac is off duty
fi
export NONINTERACTIVE=1
export HOMEBREW_NO_ENV_HINTS=1
export HOMEBREW_NO_AUTO_UPDATE=1      # we run brew update ourselves, once
export SUDO_ASKPASS=/usr/bin/false     # sudo can't prompt: fails immediately

LOG_DIR="${LOCAL_AI_HOME:-$HOME/.local-ai}/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/brew.log"
SUMMARY="$LOG_DIR/brew-summary.md"
RUN_LOG="$(mktemp "${TMPDIR:-/tmp}/brew-daily.XXXXXX")"
trap 'rm -f "$RUN_LOG"' EXIT
BREW="$(command -v brew || echo /opt/homebrew/bin/brew)"
NOW="$(date '+%Y-%m-%d %H:%M')"

"$BREW" update >>"$RUN_LOG" 2>&1 </dev/null
before="$("$BREW" outdated --greedy --quiet 2>/dev/null </dev/null | sort)"

yes | "$BREW" upgrade --greedy >>"$RUN_LOG" 2>&1
rc=${PIPESTATUS[1]}

after="$("$BREW" outdated --greedy --quiet 2>/dev/null </dev/null | sort)"
upgraded="$(comm -23 <(echo "$before") <(echo "$after") | sed '/^$/d')"
pending="$(sed '/^$/d' <<<"$after")"
pw_hint="$(grep -iE 'password|a terminal is required|sudo' "$RUN_LOG" | head -5)"

{
  echo "# Brew daily: $NOW"
  echo
  echo "## Upgraded ($(grep -c . <<<"$upgraded" || true))"
  [[ -n "$upgraded" ]] && sed 's/^/- /' <<<"$upgraded" || echo "- nothing to upgrade"
  echo
  if [[ -n "$pending" ]]; then
    echo "## Needs you ($(grep -c . <<<"$pending"))"
    echo "These could not be upgraded unattended, usually because the installer needs your password."
    echo
    sed 's/^/- /' <<<"$pending"
    echo
    echo "Finish them in Terminal:"
    echo
    echo '```'
    echo "brew upgrade --greedy $(tr '\n' ' ' <<<"$pending")"
    echo '```'
    [[ -n "$pw_hint" ]] && { echo; echo "Log hints:"; echo; sed 's/^/    /' <<<"$pw_hint"; }
  else
    echo "## Needs you"
    echo "- nothing"
  fi
  echo
  echo "brew upgrade exit code: $rc. Full log: $LOG"
} >"$SUMMARY"

{ echo "=== $NOW brew daily (exit $rc) ==="; cat "$RUN_LOG"; echo; } >>"$LOG"

if [[ -n "$pending" ]]; then
  n="$(grep -c . <<<"$pending")"
  /usr/bin/osascript -e "display notification \"$n app(s) need your password to upgrade. See ~/.local-ai/logs/brew-summary.md\" with title \"Brew daily\"" >/dev/null 2>&1 || true
fi
exit 0
