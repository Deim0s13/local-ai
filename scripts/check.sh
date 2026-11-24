#!/usr/bin/env bash
# Health check for the local AI setup. Read-only.
set -uo pipefail
HOME_DIR="${LOCAL_AI_HOME:-$HOME/.local-ai}"
OMLX_URL="http://127.0.0.1:8000"
fail=0
ok()   { printf 'OK    %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; fail=1; }

key="${OMLX_API_KEY:-}"
[[ -z "$key" && -f "$HOME/.omlx/settings.json" ]] && key="$(jq -r '.auth.api_key // empty' "$HOME/.omlx/settings.json")"

if models="$(curl -fsS --max-time 3 ${key:+-H "Authorization: Bearer $key"} "$OMLX_URL/v1/models" 2>/dev/null)"; then
  ok "oMLX is serving on $OMLX_URL"
  echo "      models:"; jq -r '.data[].id' <<<"$models" | sed 's/^/        /'
else
  bad "oMLX is not answering on $OMLX_URL (try: ~/.omlx/bin/omlx start)"
fi

command -v opencode >/dev/null && ok "OpenCode $(opencode --version 2>/dev/null)" || bad "OpenCode not installed"
[[ -f "$HOME/.config/opencode/opencode.json" ]] && ok "OpenCode config in place" || bad "OpenCode config missing (run ./setup.sh)"
[[ -d "/Applications/Jan.app" ]] && ok "Jan installed" || bad "Jan not installed"
[[ -f "$HOME_DIR/profile/about-me.md" ]] && ok "Profile at $HOME_DIR/profile/about-me.md" || bad "Profile missing"
launchctl print "gui/$(id -u)/com.local-ai.brew-daily" >/dev/null 2>&1 && ok "Daily brew job loaded" || bad "Daily brew job not loaded"
[[ -n "${OMLX_API_KEY:-}" ]] && ok "OMLX_API_KEY set in this shell" || bad "OMLX_API_KEY not set (open a new terminal after setup)"

exit $fail
