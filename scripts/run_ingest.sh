#!/usr/bin/env bash
# JSON → Validation → Normalizer → DB → Index
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SOURCE="${1:-data/json/sample_doctors.json}"
INIT_FLAG="${2:-}"

if [[ ! -f "$SOURCE" ]]; then
  echo "ERROR: Source file not found: $SOURCE" >&2
  exit 1
fi

echo "==> Ingesting: $SOURCE"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if [[ -f .env ]]; then
    # Prefer running via compose when containers are available
    if docker compose ps --status running 2>/dev/null | grep -q healthcare; then
      docker compose run --rm --entrypoint python ingest -m src.batches.ingest_json \
        --source "/app/${SOURCE#./}" --init-db
      exit $?
    fi
  fi
fi

# Local Python fallback
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

ARGS=(--source "$SOURCE")
if [[ "$INIT_FLAG" == "--init-db" ]] || [[ "${INIT_DB:-0}" == "1" ]]; then
  ARGS+=(--init-db)
else
  # Always safe to init (idempotent)
  ARGS+=(--init-db)
fi

python -m src.batches.ingest_json "${ARGS[@]}"
