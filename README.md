# Compliant Outreach Orchestrator

A small FastAPI demo project inspired by regulated servicing and collections product workflows.

The service will accept customer/account events, apply deterministic compliance policy rules, schedule outbound call/SMS/email tasks, dispatch them through mock channel adapters, and preserve an auditable event log.

## Product narrative

This project demonstrates engineering concerns common to regulated servicing platforms: collections workflows, multi-channel communication, policy controls, auditability, and event-driven backend systems:

- deterministic compliance checks before outreach
- explicit audit trail for decisions and actions
- idempotent processing to avoid duplicate customer contact
- PostgreSQL-backed operational state
- queue-worker architecture for outbound tasks
- mock provider adapters to isolate orchestration from vendor APIs

## Requirements

- Python 3.13
- uv
- Docker and Docker Compose

## Local development

Install dependencies:

```bash
uv sync
```

Run tests:

```bash
uv run pytest -q
```

Run the API locally without Docker:

```bash
uv run uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --reload
```

Check health:

```bash
curl http://localhost:8000/healthz
```

Expected response:

```json
{"service":"compliant-outreach-orchestrator","status":"ok"}
```

## Docker Compose

Start the local stack:

```bash
docker compose up --build
```

Services:

- `api`: FastAPI service on port 8000
- `worker`: placeholder worker container for later queue dispatch phases
- `postgres`: PostgreSQL database on port 5432
- `redis`: Redis broker on port 6379

Stop the stack:

```bash
docker compose down
```

Remove database volume as well:

```bash
docker compose down -v
```

## Planned architecture

```text
Inbound event
  -> FastAPI webhook
  -> idempotency check
  -> customer/account upsert
  -> policy engine
  -> outreach planner
  -> scheduled tasks
  -> worker dispatch
  -> mock call/SMS/email adapter
  -> delivery result
  -> append-only audit log
```

## Development principles

- Keep policy decisions deterministic and testable.
- Treat audit logs as product features, not debug leftovers.
