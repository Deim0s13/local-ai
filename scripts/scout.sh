#!/usr/bin/env bash
# Monthly model scout. Results go to ~/.local-ai/evals/<YYYY-MM>/scorecard.md
#   scripts/scout.sh discover     list new MLX models that fit (no downloads)
#   scripts/scout.sh run          run this month's check now (stops oMLX while testing)
#   scripts/scout.sh run --force  run again even if this month is done
#   scripts/scout.sh last         show the latest scorecard
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${LOCAL_AI_HOME:-$HOME/.local-ai}"
if [[ "${1:-}" == "last" ]]; then
  f="$(ls -1d "$HOME_DIR"/evals/20*/scorecard.md 2>/dev/null | tail -1)"
  [[ -n "$f" ]] && cat "$f" || echo "No scorecard yet"; exit 0
fi
mkdir -p "$HOME_DIR/logs"
exec "$HOME_DIR/venv/bin/python" "$REPO/scout/scout.py" "$@" 2>&1 | tee -a "$HOME_DIR/logs/scout.log"
