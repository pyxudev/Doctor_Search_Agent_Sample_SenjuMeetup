#!/usr/bin/env bash
# Setup + docker compose: ingest sample data, start DB watcher, run agent CLI
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Creating .env from .env.example ..."
  cp .env.example .env
  echo "Edit .env and set XAI_API_KEY (optional for offline mode), then re-run if needed."
fi

echo "==> Building images..."
docker compose build

echo "==> Running JSON ingestion..."
docker compose run --rm ingest

echo "==> Starting db-updater (background)..."
docker compose up -d db-updater

echo "==> Starting interactive agent (Ctrl+C to exit)..."
docker compose run --rm agent
