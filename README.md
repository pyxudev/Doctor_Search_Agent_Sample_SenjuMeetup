# Health Care Call Center AI Agent

PoC CLI agent that matches patients to doctors from SMS/voice-style text, with JSON ingestion and auto DB update batches. Described in [`Description.md`](./Description.md).

## Quick start

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env — set XAI_API_KEY for LLM features (optional; offline rule-based mode works without it)

# 2. Start with Docker (ingest → db-updater → interactive agent)
./scripts/docker_start.sh

# Or manually:
docker compose up --build
# Recommended interactive agent (TTY):
docker compose run --rm agent
```

**Services**

| Service | Role |
|---------|------|
| `ingest` | One-shot: load `sample_doctors.json` into SQLite |
| `db-updater` | Watches `data/json/incoming` and auto-updates DB |
| `agent` | Interactive CLI agent |

```bash
# Background watcher only
docker compose up -d db-updater

# Agent session
docker compose run --rm agent
```

## Local run (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set XAI_API_KEY if desired

# Ingest sample doctors
./scripts/run_ingest.sh data/json/sample_doctors.json --init-db

# Interactive agent
./scripts/run_agent.sh
# or: python -m src.agent.cli

# One-shot message
python -m src.agent.cli -m "I need a kind female pediatrician in Tokyo tomorrow"
```

## Batch jobs

### 1. JSON ingestion

Pipeline: **JSON → Validation → Normalizer → DB → Indexes / FTS**

```bash
./scripts/run_ingest.sh data/json/sample_doctors.json --init-db
# or
python -m src.batches.ingest_json --source data/json/sample_doctors.json --init-db
```

### 2. Auto DB update when JSON changes

Drop new/updated JSON into `data/json/incoming/`. The watcher ingests them and moves files to `data/json/processed/`.

```bash
# One-shot process pending files
./scripts/run_update.sh --once

# Continuous watch (also started by docker compose as `db-updater`)
./scripts/run_update.sh --watch

# Demo: process the example update
cp data/json/examples/doctor_update.json data/json/incoming/doctors_$(date +%s).json
./scripts/run_update.sh --once
```

With Docker Compose, `db-updater` is always watching:

```bash
docker compose up -d db-updater
# copy a JSON file into data/json/incoming/ on the host
```

## Agent workflow (PoC)

```
Patient text
  → Input validation (confidence, medical aliases, fuzzy match)
  → Clarifying questions if needed
  → Hybrid doctor search (structured SQL + FTS semantic)
  → Re-ranking (region, gender, expertise, availability, rating)
  → Optional LLM re-rank / natural language reply (SpaceXAI)
  → Confirm reservation → contact_log + audit_log
```

Example session:

```
You: I need a kind female pediatrician in Tokyo available tomorrow
Agent: [lists matching doctors]
You: 1
Agent: Reservation confirmed! ...
You: csat 5
```

Commands: `quit` / `exit`, `escalate`, `csat 1-5`, option number to reserve.

## Configuration (`.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `XAI_API_KEY` | SpaceXAI / xAI API key | (empty → offline mode) |
| `LLM_MODEL` | Model name | `grok-4.5` |
| `LLM_BASE_URL` | API base | `https://api.x.ai/v1` |
| `RESPONSE_TIMEOUT_SEC` | LLM timeout | `120` |
| `RETRY_ATTEMPT` | Retries | `1` |
| `MAX_SEARCH_TIME_SEC` | Search budget | `60` |
| `CONFIDENCE_THRESHOLD` | Validation threshold | `0.95` |
| `LOG_LEVEL` | Logging | `info` |
| `DATABASE_URL` | SQLite URL | `sqlite:///data/healthcare.db` |
| `JSON_WATCH_DIR` | Incoming JSON dir | `data/json/incoming` |

## Database (SQLite PoC)

Tables: `doctor`, `hospital`, `keywords_alias`, `operator`, `contact_log`, `audit_log`, plus `doctor_fts` (FTS5).

Schema: [`src/db/schema.sql`](./src/db/schema.sql).

## Project layout

```
├── Description.md
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── requirements.txt
├── data/json/
│   ├── sample_doctors.json
│   ├── incoming/          # drop new JSON here
│   └── processed/         # archived after ingest
├── scripts/
│   ├── run_ingest.sh
│   ├── run_update.sh
│   └── run_agent.sh
└── src/
    ├── agent/             # CLI, planner, tools, validation, LLM
    ├── batches/           # ingest_json, update_db
    ├── db/                # schema, connection, repository
    └── config.py
```

## Notes

- **Twilio / STT / TTS** are stubbed for PoC; the CLI accepts text that would come from SMS or speech-to-text.
- **Offline mode**: without `XAI_API_KEY`, validation and search still work via rules + fuzzy alias matching + SQL/FTS.
- Production path in the design doc uses PostgreSQL + pgvector; this PoC uses SQLite as specified.
