#!/usr/bin/env bash
# Register the research and docs MCP servers in Jan (mcp_config.json), active.
# Keeps every other Jan MCP entry as it is. Restart Jan afterwards.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_AI_HOME="${LOCAL_AI_HOME:-$HOME/.local-ai}"
PY="$LOCAL_AI_HOME/venv/bin/python"
JAN_DATA="${JAN_DATA:-$HOME/Library/Application Support/Jan/data}"
CFG="$JAN_DATA/mcp_config.json"
mkdir -p "$JAN_DATA"
[[ -s "$CFG" ]] || echo '{"mcpServers":{}}' >"$CFG"
cp "$CFG" "$CFG.bak"
tmp="$(mktemp "${TMPDIR:-/tmp}/jan-mcp.XXXXXX")"
jq --arg py "$PY" --arg repo "$REPO" '
  .mcpServers = (.mcpServers // {})
  | .mcpServers["local-research"] = {command: $py, args: [($repo + "/servers/research_server.py")], env: {}, active: true}
  | .mcpServers["local-docs"]     = {command: $py, args: [($repo + "/servers/docs_server.py")], env: {}, active: true}
' "$CFG" >"$tmp" && mv "$tmp" "$CFG"
echo "Jan MCP servers registered (local-research, local-docs). Quit and reopen Jan."
