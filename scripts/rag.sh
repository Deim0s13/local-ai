#!/usr/bin/env bash
# Manage your document index (stored in ~/.local-ai/rag/, outside the repo).
#   scripts/rag.sh add ~/Documents/notes   index a folder (re-run to refresh)
#   scripts/rag.sh sync                    refresh every indexed folder
#   scripts/rag.sh remove <folder>         drop a folder
#   scripts/rag.sh status                  what is indexed
#   scripts/rag.sh search <words>          quick test search
#   scripts/rag.sh find <part of a name>   check a file is indexed
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec "${LOCAL_AI_HOME:-$HOME/.local-ai}/venv/bin/python" "$REPO/servers/docs_server.py" "$@"
