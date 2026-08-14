#!/usr/bin/env bash
# Start the interactive CLI agent (local or via docker compose)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env — copying from .env.example"
  cp .env.example .env
  echo "Edit .env and set XAI_API_KEY, then re-run."
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
set -a
# shellcheck disable=SC1091
source .env
set +a

# Ensure DB has data
if [[ ! -f data/healthcare.db ]]; then
  echo "==> Initializing DB and ingesting sample data..."
  python -m src.batches.ingest_json --source data/json/sample_doctors.json --init-db
fi

echo "==> Starting healthcare agent CLI"
exec python -m src.agent.cli "$@"
