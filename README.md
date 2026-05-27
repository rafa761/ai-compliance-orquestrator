# Compliant Outreach Orchestrator

A small FastAPI demo project inspired by Domu AI's regulated servicing and collections product surface.

The service will accept customer/account events, apply deterministic compliance policy rules, schedule outbound call/SMS/email tasks, dispatch them through mock channel adapters, and preserve an auditable event log.

This repository is intentionally backend-first. The goal is to demonstrate orchestration, compliance reasoning, idempotency, queue processing, PostgreSQL modeling, and operational clarity rather than a decorative UI.

## Current phase

Phase 0: project hygiene and runtime skeleton.

Implemented so far:

- FastAPI application package under `src/orchestrator`
- Health endpoint: `GET /healthz`
- pytest/httpx health check test
- Dockerfile for the API image
- Docker Compose services for API, worker placeholder, PostgreSQL, and Redis
- `.env.example` for local configuration

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

## How this maps to Domu AI

Domu's public materials emphasize regulated servicing, collections workflows, multi-channel communication, policy controls, auditability, and event-driven backend systems. This project is a small demonstration of those same engineering concerns:

- deterministic compliance checks before outreach
- explicit audit trail for decisions and actions
- idempotent processing to avoid duplicate customer contact
- PostgreSQL-backed operational state
- queue-worker architecture for outbound tasks
- mock provider adapters to isolate orchestration from vendor APIs

## Development principles

- Keep policy decisions deterministic and testable.
- Treat audit logs as product features, not debug leftovers.
- Prefer boring, reliable backend code over impressive but fragile demos.
- Add real provider integrations only after the orchestration core is correct.

## Next phase

Phase 1 will add database models and migrations for customers, accounts, inbound events, outreach tasks, policy decisions, and audit events.
