# Compliant Outreach Orchestrator

A FastAPI demo service for regulated-servicing outreach workflows.

The project accepts inbound customer/account events, preserves an auditable event trail, and includes a pure deterministic policy engine integrated with an outreach planner that schedules or cancels outbound call/SMS/email work.

The emphasis is not on a flashy chatbot. The emphasis is on the backend engineering concerns that matter when contacting real customers: idempotency, consent, opt-out handling, quiet hours, retry-safe workflows, and evidence that explains what the system did.

## Product narrative

This project is being built around engineering concerns common to regulated servicing platforms:

- deterministic compliance checks before outreach
- explicit audit trail for inbound event handling, policy decisions, scheduled tasks, blocked outreach, deferrals, and cancellations
- application-level idempotent processing to avoid duplicate customer contact on sequential retries
- PostgreSQL-backed operational state
- planner-integrated deterministic policy checks before outbound work is scheduled
- planned queue-worker architecture for outbound dispatch
- planned mock provider adapters to isolate orchestration from vendor APIs
- small, testable domain components rather than opaque automation

The core design principle is that compliance decisions must be reproducible. AI may eventually help draft messages or summarize audit trails, but the allow/block/defer decision itself remains deterministic and unit tested.

## Current implementation status

Implemented:

- Phase 1: database foundation
  - SQLAlchemy async models
  - Alembic migration setup
  - core tables for customers, accounts, inbound events, policy decisions, outreach tasks, and audit events
  - database constraints for idempotency-sensitive records

- Phase 2: audit foundation
  - FastAPI app structure
  - request correlation ID middleware
  - minimal inbound event ingestion
  - append-only audit helper
  - audit query endpoint
  - safe duplicate event handling

- Phase 3: deterministic policy engine
  - pure `PolicyInput` -> `PolicyResult` evaluation
  - opt-out, consent, quiet-hours, account-status, and frequency-cap rules
  - explicit allow/block/defer reasons
  - unit tests for individual rules and combined decisions

- Phase 4: outreach planner
  - event-to-outreach planning for supported servicing events
  - persisted policy decisions and scheduled outreach tasks
  - deferred scheduling for quiet-hours decisions
  - cancellation handling for payment, opt-out, hardship, and account pause events
  - application-level planner idempotency for repeat inbound-event processing

Planned next:

- Phase 5: full event ingestion with customer/account snapshot upsert
- Phase 6: worker dispatch through mock channel adapters
- Phase 7: operational APIs
- Phase 8: demo data and walkthrough script
- Phase 9: portfolio polish and production-extension notes

## Target system flow

The webhook, idempotent event storage, audit logging, policy engine, and outreach planner are implemented. Customer/account snapshot upsert and worker dispatch remain planned later-phase work.

```mermaid
flowchart TD
    A[Inbound servicing event] --> B[FastAPI webhook]
    B --> C[Correlation ID middleware]
    C --> D[Internal idempotency check]
    D --> E{Duplicate source event?}

    E -- yes --> F[Return existing event ID]
    E -- no --> G[Store inbound event]
    G --> H[Append event_received audit event]
    H --> I[Append event_accepted audit event]

    I --> J[Future: upsert customer/account snapshot]
    J --> K[Outreach planner]
    K --> L[Policy engine]
    L --> M{Decision per channel}

    M -- block --> N[Persist policy decision and audit block]
    M -- defer --> O[Schedule at defer_until unless frequency cap applies]
    M -- allow --> P[Create outreach task]

    O --> Q[Future: worker dispatch]
    P --> Q
    Q --> R[Future: mock call/SMS/email adapter]
    R --> S[Future: delivery result]
    S --> T[Future: append delivery audit trail]
```

## System design

```mermaid
flowchart LR
    subgraph Clients[External callers]
        Servicing[Servicing system]
        Demo[Demo scripts / curl]
    end

    subgraph API[FastAPI service]
        Health[Health endpoint]
        Events[Event ingestion API]
        AuditAPI[Audit API]
        Correlation[Correlation middleware]
    end

    subgraph Domain[Domain layer]
        Idempotency[Idempotency helper]
        AuditHelper[Audit helper]
        Policy[Deterministic policy engine]
        Planner[Outreach planner]
    end

    subgraph Data[PostgreSQL]
        Customers[(customers)]
        Accounts[(accounts)]
        Inbound[(inbound_events)]
        Decisions[(policy_decisions)]
        Tasks[(outreach_tasks)]
        Audit[(audit_events)]
    end

    subgraph Async[Async dispatch - planned]
        Redis[(Redis broker)]
        Worker[Worker process]
        Adapters[Mock channel adapters]
    end

    Servicing --> Events
    Demo --> Events
    Demo --> AuditAPI
    Events --> Correlation
    Events --> Idempotency
    Events --> AuditHelper
    Idempotency --> Inbound
    AuditHelper --> Audit
    AuditAPI --> Audit
    Planner --> Policy
    Planner --> Decisions
    Planner --> Tasks
    Planner --> Audit
    Tasks -. planned dispatch .-> Redis
    Redis -. planned .-> Worker
    Worker -. planned .-> Adapters
    Worker -. planned .-> Audit
    Events -. later phases .-> Customers
    Events -. later phases .-> Accounts
```

## Development principles

- Keep policy decisions deterministic and testable.
- Treat audit logs as product features, not debug leftovers.
- Keep application-level idempotency internal and derived from stable source event identity; add database locking/coordination before claiming production-grade concurrent processing.

## Design documentation

- [Phase 1 — Database foundation](docs/phase-1-database-foundation.md)
- [Phase 2 — Audit foundation](docs/phase-2-audit-foundation.md)
- [Phase 3 — Policy engine](docs/phase-3-policy-engine.md)
- [Phase 4 — Outreach planner](docs/phase-4-outreach-planner.md)

## Diagrams

Additional visual asset:

Event-driven call processing

![Event driven call processing](docs/diagrams/event-driven-call-processing-2026-05-28.png)

## Requirements

- Python 3.13
- uv
- Docker and Docker Compose

## Local development

Install dependencies:

```bash
uv sync
```

Create a local environment file when needed:

```bash
cp .env.example .env
```

Application configuration is centralized in `src/orchestrator/settings.py` and loaded from environment variables or `.env`. `DATABASE_URL` is used by the app. `ALEMBIC_DATABASE_URL` is optional and only needed when migrations should connect somewhere different from the app runtime URL.

Run tests:

```bash
make test
```

Run lint and tests:

```bash
make check
```

Apply database migrations:

```bash
make migrate
```

Create a new Alembic migration after model changes:

```bash
make revision m="describe change"
```

Run the API locally without Docker. Configuration is loaded from environment variables or `.env` through `src/orchestrator/settings.py`:

```bash
make run
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
make docker-up
```

Services:

- `api`: FastAPI service on port 8000
- `worker`: placeholder worker container for later queue dispatch phases
- `postgres`: PostgreSQL database on port 5432
- `redis`: Redis broker on port 6379

Stop the stack:

```bash
make docker-down
```

Remove database volume as well:

```bash
make docker-clean
```

## What this demo intentionally does not claim

This project is a portfolio/demo system, not a production compliance product.

It does not claim:

- actual TCPA compliance
- SOC 2, PCI, or CFPB compliance
- real telephony, SMS, or email provider integration
- legal advice or policy completeness

The accurate claim is narrower and stronger: this is a production-shaped backend demo showing TCPA-inspired controls, audit-ready design patterns, and a deterministic policy-evaluation foundation for future outreach orchestration.
