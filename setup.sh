#!/usr/bin/env bash
# Phase 1 setup: oMLX + OpenCode + Jan + profile + daily brew job.
# Safe to re-run. Writes nothing secret to this repo; personal data goes to ~/.local-ai/.
# Override model picks with: CODE_MODEL=<id> CHAT_MODEL=<id> ./setup.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
LOCAL_AI_HOME="${LOCAL_AI_HOME:-$HOME/.local-ai}"
OMLX="$HOME/.omlx/bin/omlx"
OMLX_SETTINGS="$HOME/.omlx/settings.json"
OMLX_URL="http://127.0.0.1:8000"
step() { printf '\n==> %s\n' "$1"; }

step "Checking prerequisites"
command -v brew >/dev/null || { echo "Homebrew is required"; exit 1; }
command -v jq   >/dev/null || brew install jq
[[ -x "$OMLX" ]] || { echo "oMLX not found at $OMLX. Install it first, then re-run."; exit 1; }
[[ -f "$OMLX_SETTINGS" ]] || { echo "oMLX settings not found at $OMLX_SETTINGS"; exit 1; }

step "Personal folders in $LOCAL_AI_HOME (outside the repo)"
mkdir -p "$LOCAL_AI_HOME"/{profile,memory,logs}
chmod 700 "$LOCAL_AI_HOME"
if [[ ! -f "$LOCAL_AI_HOME/profile/about-me.md" ]]; then
  cp "$REPO/templates/about-me.md" "$LOCAL_AI_HOME/profile/about-me.md"
  echo "Created profile: $LOCAL_AI_HOME/profile/about-me.md (fill it in)"
else
  echo "Profile already exists, left unchanged"
fi

step "oMLX API key available to new shells (read from oMLX settings, never copied)"
MARK="# local-ai: oMLX key"
if ! grep -qF "$MARK" "$HOME/.zshrc" 2>/dev/null; then
  {
    echo ""
    echo "$MARK"
    echo "export OMLX_API_KEY=\"\$(jq -r '.auth.api_key // empty' \"\$HOME/.omlx/settings.json\" 2>/dev/null)\""
  } >>"$HOME/.zshrc"
  echo "Added to ~/.zshrc"
else
  echo "Already in ~/.zshrc"
fi
OMLX_API_KEY="$(jq -r '.auth.api_key // empty' "$OMLX_SETTINGS")"
export OMLX_API_KEY

step "Starting oMLX"
models_json() { curl -fsS --max-time 3 -H "Authorization: Bearer $OMLX_API_KEY" "$OMLX_URL/v1/models"; }
if ! models_json >/dev/null 2>&1; then
  "$OMLX" start
  for _ in $(seq 1 60); do models_json >/dev/null 2>&1 && break; sleep 1; done
fi
MODELS="$(models_json)" || { echo "oMLX did not come up on $OMLX_URL"; exit 1; }
echo "oMLX models:"; jq -r '.data[].id' <<<"$MODELS" | sed 's/^/  /'

pick() { jq -r --arg re "$1" '[.data[].id | select(test($re; "i"))][0] // empty' <<<"$MODELS"; }
CODE_MODEL="${CODE_MODEL:-$(pick 'coder')}"
CHAT_MODEL="${CHAT_MODEL:-$(pick 'qwen3[.]6-35b-a3b')}"
CHAT_MODEL="${CHAT_MODEL:-$(pick '35b-a3b')}"
[[ -n "$CODE_MODEL" && -n "$CHAT_MODEL" ]] || { echo "Could not pick models; set CODE_MODEL and CHAT_MODEL"; exit 1; }
echo "Coding model: $CODE_MODEL"
echo "Chat model:   $CHAT_MODEL"

step "Python env for the MCP servers ($LOCAL_AI_HOME/venv)"
command -v uv >/dev/null || brew install uv
[[ -x "$LOCAL_AI_HOME/venv/bin/python" ]] || uv venv --python 3.12 "$LOCAL_AI_HOME/venv"
uv pip install --python "$LOCAL_AI_HOME/venv/bin/python" -q -r "$REPO/requirements.txt"
PY="$LOCAL_AI_HOME/venv/bin/python"
echo "Installed MCP server dependencies"

step "SearXNG web search (Podman, 127.0.0.1:8888)"
"$REPO/scripts/searxng.sh" start || echo "WARN SearXNG not running; web search will fail until it is (scripts/searxng.sh start)"

step "OpenCode"
command -v opencode >/dev/null || brew install opencode
mkdir -p "$HOME/.config/opencode"
CFG="$HOME/.config/opencode/opencode.json"
[[ -f "$CFG" ]] && cp "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)" && echo "Backed up existing config"
sed -e "s|__CODE_MODEL__|$CODE_MODEL|g" \
    -e "s|__CHAT_MODEL__|$CHAT_MODEL|g" \
    -e "s|__LOCAL_AI_HOME__|$LOCAL_AI_HOME|g" \
    -e "s|__PY__|$PY|g" \
    -e "s|__REPO__|$REPO|g" \
    "$REPO/config/opencode.template.json" >"$CFG"
jq empty "$CFG" && echo "Wrote $CFG"

step "Warming up the coding model (first load can take a minute or two)"
curl -fsS --max-time 600 -H "Authorization: Bearer $OMLX_API_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$CODE_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with OK\"}],\"max_tokens\":5}" \
  "$OMLX_URL/v1/chat/completions" >/dev/null && echo "Coding model loaded" || echo "WARN coding model did not answer; check oMLX logs"

step "Jan"
[[ -d /Applications/Jan.app ]] || brew install --cask jan
echo "Jan installed. Connect it to oMLX once by hand (see README, 'Connect Jan')."
"$REPO/scripts/jan-mcp.sh"

step "Keep Jan's assistant in sync with your profile"
[[ -f "$LOCAL_AI_HOME/settings.env" ]] || printf '# Name of the Jan assistant that gets your profile as its instructions\nJAN_ASSISTANT=""\n' >"$LOCAL_AI_HOME/settings.env"
"$REPO/scripts/sync-jan-profile.sh" || echo "WARN Jan sync did not run; see message above"
PLIST="$HOME/Library/LaunchAgents/com.local-ai.profile-sync.plist"
sed -e "s|__REPO__|$REPO|g" -e "s|__PROFILE__|$LOCAL_AI_HOME/profile/about-me.md|g" "$REPO/launchd/com.local-ai.profile-sync.plist" >"$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Loaded. Saving about-me.md now updates Jan automatically."

step "Daily brew update + upgrade --greedy at 09:30"
PLIST="$HOME/Library/LaunchAgents/com.local-ai.brew-daily.plist"
sed "s|__REPO__|$REPO|g" "$REPO/launchd/com.local-ai.brew-daily.plist" >"$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Loaded. Log: $LOCAL_AI_HOME/logs/brew.log"

step "Monthly model scout (runs once a month, overnight or when idle; asks first)"
PLIST="$HOME/Library/LaunchAgents/com.local-ai.model-scout.plist"
sed -e "s|__REPO__|$REPO|g" -e "s|__PY__|$PY|g" -e "s|__HOME__|$LOCAL_AI_HOME|g" "$REPO/launchd/com.local-ai.model-scout.plist" >"$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Loaded. Scorecards: $LOCAL_AI_HOME/evals/<month>/scorecard.md"

step "Done. Open a new terminal, then run: ./scripts/check.sh"
