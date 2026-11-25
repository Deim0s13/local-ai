#!/usr/bin/env bash
# Morning brief. Files go to ~/.local-ai/briefs/ (outside the repo).
#   scripts/brief.sh run          build today's brief now (skips if it exists)
#   scripts/brief.sh run --force  rebuild today's brief
#   scripts/brief.sh open         open the latest brief in your browser
#   scripts/brief.sh last         print the latest brief
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${LOCAL_AI_HOME:-$HOME/.local-ai}"
case "${1:-}" in
  open) f="$(ls -1 "$HOME_DIR"/briefs/.web/20*.html 2>/dev/null | tail -1)"
        [[ -n "$f" ]] && open "$f" || echo "No brief yet"; exit 0 ;;
  last) f="$(ls -1 "$HOME_DIR"/briefs/20*.md 2>/dev/null | tail -1)"
        [[ -n "$f" ]] && cat "$f" || echo "No brief yet"; exit 0 ;;
esac
mkdir -p "$HOME_DIR/logs"
"$HOME_DIR/venv/bin/python" "$REPO/brief/brief.py" "$@" 2>&1 | tee -a "$HOME_DIR/logs/brief.log"
