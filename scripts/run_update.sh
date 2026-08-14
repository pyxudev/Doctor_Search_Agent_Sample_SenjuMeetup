#!/usr/bin/env bash
# Auto-update database when JSON files appear in data/json/incoming
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:---once}"   # --once | --watch
DIR="${2:-data/json/incoming}"

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

mkdir -p "$DIR" data/json/processed

echo "==> DB update batch (mode=$MODE dir=$DIR)"

if [[ "$MODE" == "--watch" ]]; then
  python -m src.batches.update_db --watch --dir "$DIR" --init-db
else
  python -m src.batches.update_db --once --dir "$DIR" --init-db
fi
