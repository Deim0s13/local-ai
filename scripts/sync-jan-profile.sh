#!/usr/bin/env bash
# Copy ~/.local-ai/profile/about-me.md into a Jan assistant's instructions.
# about-me.md is the single source of truth; templates/tool-guidance.md is appended
# so the model knows to use its search_docs and web_search tools. Run by launchd whenever the file changes.
# Which assistant: JAN_ASSISTANT in ~/.local-ai/settings.env (its display name in Jan).
# If unset and there is exactly one assistant besides Jan's default, that one is used and remembered.
set -euo pipefail
LOCAL_AI_HOME="${LOCAL_AI_HOME:-$HOME/.local-ai}"
PROFILE="$LOCAL_AI_HOME/profile/about-me.md"
SETTINGS="$LOCAL_AI_HOME/settings.env"
JAN_DATA="${JAN_DATA:-$HOME/Library/Application Support/Jan/data}"
LOG="$LOCAL_AI_HOME/logs/profile-sync.log"
mkdir -p "$LOCAL_AI_HOME/logs"
JQ="$(command -v jq || echo /opt/homebrew/bin/jq)"
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

[[ -f "$PROFILE" ]] || { say "No profile at $PROFILE"; exit 1; }
[[ -d "$JAN_DATA/assistants" ]] || { say "No Jan assistants folder at $JAN_DATA/assistants (custom data folder? set JAN_DATA)"; exit 1; }
JAN_ASSISTANT=""
[[ -f "$SETTINGS" ]] && source "$SETTINGS"

files=()
while IFS= read -r -d '' f; do files+=("$f"); done < <(find "$JAN_DATA/assistants" -name assistant.json -print0)

target=""
if [[ -n "$JAN_ASSISTANT" ]]; then
  for f in "${files[@]}"; do
    [[ "$("$JQ" -r '.name // ""' "$f")" == "$JAN_ASSISTANT" ]] && { target="$f"; break; }
  done
  [[ -n "$target" ]] || { say "No Jan assistant named '$JAN_ASSISTANT'"; exit 1; }
else
  custom=()
  for f in "${files[@]}"; do [[ "$("$JQ" -r '.id // ""' "$f")" != "jan" ]] && custom+=("$f"); done
  if [[ ${#custom[@]} -eq 1 ]]; then
    target="${custom[0]}"
    name="$("$JQ" -r '.name' "$target")"
    printf '# Name of the Jan assistant that gets your profile as its instructions\nJAN_ASSISTANT="%s"\n' "$name" >"$SETTINGS"
    say "Using Jan assistant '$name' (saved to $SETTINGS)"
  else
    say "Set JAN_ASSISTANT in $SETTINGS to the Jan assistant's name (found ${#custom[@]} custom assistants)"
    exit 1
  fi
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONTENT="$(cat "$PROFILE"; [[ -f "$REPO/templates/tool-guidance.md" ]] && cat "$REPO/templates/tool-guidance.md")"
if [[ "$("$JQ" -r '.instructions // ""' "$target")" == "$CONTENT" ]]; then
  say "Jan assistant already up to date"; exit 0
fi
cp "$target" "$target.bak"
tmp="$(mktemp "${TMPDIR:-/tmp}/jan-assistant.XXXXXX")"
"$JQ" --arg p "$CONTENT" '.instructions = $p' "$target" >"$tmp" && mv "$tmp" "$target"
say "Updated Jan assistant instructions from $PROFILE"

if pgrep -x Jan >/dev/null 2>&1; then
  /usr/bin/osascript -e 'display notification "Profile synced. Quit and reopen Jan to load it." with title "local-ai"' >/dev/null 2>&1 || true
fi
