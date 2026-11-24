#!/usr/bin/env bash
# Local SearXNG in Podman, on 127.0.0.1:8888 only.
# Usage: scripts/searxng.sh start|stop|status
# Config + generated secret live in ~/.local-ai/searxng/, never in the repo.
set -euo pipefail
LOCAL_AI_HOME="${LOCAL_AI_HOME:-$HOME/.local-ai}"
CONF="$LOCAL_AI_HOME/searxng"
NAME=local-ai-searxng
IMAGE=docker.io/searxng/searxng:latest
PORT=8888

ensure_config() {
  mkdir -p "$CONF"
  if [[ ! -f "$CONF/settings.yml" ]]; then
    cat >"$CONF/settings.yml" <<YML
use_default_settings: true
server:
  secret_key: "$(openssl rand -hex 32)"
  limiter: false
  image_proxy: false
  bind_address: "0.0.0.0"
search:
  formats: [html, json]
YML
    chmod 600 "$CONF/settings.yml"
  fi
}

case "${1:-status}" in
  start)
    command -v podman >/dev/null || { echo "Podman is required"; exit 1; }
    podman info >/dev/null 2>&1 || podman machine start
    ensure_config
    if podman container exists "$NAME"; then
      podman start "$NAME" >/dev/null
    else
      podman run -d --name "$NAME" --restart=always \
        -p "127.0.0.1:$PORT:8080" \
        -v "$CONF:/etc/searxng" \
        "$IMAGE" >/dev/null
    fi
    for _ in $(seq 1 30); do
      curl -fsS --max-time 2 "http://127.0.0.1:$PORT/search?q=test&format=json" >/dev/null 2>&1 && { echo "SearXNG ready on http://127.0.0.1:$PORT"; exit 0; }
      sleep 1
    done
    echo "SearXNG did not answer; see: podman logs $NAME"; exit 1 ;;
  stop) podman stop "$NAME" ;;
  status)
    curl -fsS --max-time 3 "http://127.0.0.1:$PORT/search?q=test&format=json" >/dev/null 2>&1 \
      && echo "SearXNG ready on http://127.0.0.1:$PORT" || { echo "SearXNG not answering"; exit 1; } ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
