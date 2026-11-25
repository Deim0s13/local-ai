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

PY="$HOME_DIR/venv/bin/python"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[[ -x "$PY" ]] && "$PY" -c "import mcp, sentence_transformers" 2>/dev/null && ok "MCP server env ready" || bad "MCP server env missing (run ./setup.sh)"
curl -fsS --max-time 3 "http://127.0.0.1:8888/search?q=test&format=json" >/dev/null 2>&1 && ok "SearXNG answering on 127.0.0.1:8888" || bad "SearXNG not answering (scripts/searxng.sh start)"
if [[ -x "$PY" ]]; then
  chunks="$("$PY" "$REPO/servers/docs_server.py" status 2>/dev/null | jq -r .chunks)"
  [[ "${chunks:-0}" -gt 0 ]] && ok "Docs index: $chunks passages" || bad "Docs index empty (scripts/rag.sh add <folder>)"
fi
jq -e '.mcpServers["local-research"].active and .mcpServers["local-docs"].active' "$HOME/Library/Application Support/Jan/data/mcp_config.json" >/dev/null 2>&1 && ok "Jan has the research and docs tools" || bad "Jan MCP servers not registered (scripts/jan-mcp.sh)"
launchctl print "gui/$(id -u)/com.local-ai.model-scout" >/dev/null 2>&1 && ok "Monthly model scout loaded (last scorecard: $(ls -1 "$HOME_DIR"/evals/20*/scorecard.md 2>/dev/null | tail -1 | xargs -n1 dirname 2>/dev/null | xargs -n1 basename 2>/dev/null || echo none yet))" || bad "Model scout job not loaded (run ./setup.sh)"

launchctl print "gui/$(id -u)/com.local-ai.morning-brief" >/dev/null 2>&1 && ok "Morning brief loaded (last: $(ls -1 "$HOME_DIR"/briefs/20*.md 2>/dev/null | tail -1 | xargs -n1 basename 2>/dev/null || echo none))" || bad "Morning brief job not loaded (run ./setup.sh)"

exit $fail
