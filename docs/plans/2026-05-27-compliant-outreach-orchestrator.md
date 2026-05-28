# Compliant Outreach Orchestrator Implementation Plan

Goal: Build a small, production-shaped FastAPI service that accepts customer/account events, applies deterministic compliance policy rules, schedules outbound call/SMS/email jobs, dispatches them through mock adapters, and stores an auditable append-only event log.

Why this fits the target product area: The project demonstrates the problem shape from the research report: regulated servicing, event-driven orchestration, multi-channel outreach, PostgreSQL state, queue workers, idempotency, retries, and compliance evidence. It is not a flashy chatbot. Mercifully.

Primary audience: A software engineer reviewing your portfolio or discussing your take-home-style project.

Implementation style: Python-first, strict TDD for domain logic and API behavior. Keep the system simple enough to finish, but serious enough to discuss tradeoffs.

Tech stack:
- Python 3.13
- FastAPI for HTTP APIs
- Pydantic v2 for request/response schemas
- SQLAlchemy 2.x async ORM for persistence
- Alembic for migrations
- PostgreSQL via Docker Compose
- Redis via Docker Compose for queue backend
- Dramatiq or RQ for background workers; recommendation: Dramatiq because retry middleware and worker processes are straightforward
- pytest + pytest-asyncio + httpx AsyncClient for tests
- Ruff for linting/formatting
- structlog or standard JSON logging for audit-friendly logs
- Optional: Sentry for error reporting, not required for the first demo
- Optional: Mailpit for local email preview, useful but not necessary

Non-goals:
- Real Twilio/Telnyx/SendGrid integration in the first version
- Real voice streaming, SIP, WebRTC, or LLM agent logic
- A complex frontend
- A dynamic policy DSL
- Authentication/authorization beyond a simple API key placeholder
- ClickHouse in the MVP

Recommended scope:
Build the core backend only, with a tiny read-only dashboard if time permits. The most impressive part should be correctness and auditability, not a decorative UI. A dashboard is nice; double-contacting a customer because your retry path is sloppy is less charming, sir.

Architecture:

Inbound event -> FastAPI webhook -> Idempotency check -> Store account/customer event -> Policy engine -> Outreach plan -> Scheduled jobs -> Worker dispatch -> Mock channel adapter -> Delivery result -> Append-only audit log -> Query/report APIs

Runtime containers:
- api: FastAPI application
- worker: Dramatiq/RQ worker process
- postgres: transactional database
- redis: queue broker
- optional mailpit: local SMTP/email UI

Suggested repository structure:

.
├── src/
│   └── orchestrator/
│       ├── __init__.py
│       ├── main.py                 # app factory and router wiring only
│       ├── settings.py             # Pydantic settings
│       ├── db.py                   # database engine/session setup
│       ├── models.py               # SQLAlchemy models and enums
│       ├── domain/                 # domain/application helpers, not HTTP handlers
│       │   ├── __init__.py
│       │   ├── audit_log.py        # append-only audit persistence helper
│       │   └── inbound_events.py   # inbound event identity/idempotency helpers
│       ├── policy/
│       │   ├── __init__.py
│       │   ├── engine.py
│       │   └── rules.py
│       ├── orchestration/
│       │   ├── __init__.py
│       │   ├── planner.py
│       │   └── scheduler.py
│       ├── channels/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── mock_call.py
│       │   ├── mock_sms.py
│       │   └── mock_email.py
│       ├── workers/
│       │   ├── __init__.py
│       │   └── dispatch.py
│       └── api/
│           ├── __init__.py
│           ├── correlation.py      # HTTP correlation-id middleware
│           ├── dependencies.py
│           ├── events.py           # event request/response schemas and routes
│           ├── tasks.py
│           ├── audit.py            # audit request/response schemas and routes
│           └── health.py
├── tests/
│   ├── unit/
│   │   ├── test_policy_engine.py
│   │   ├── test_planner.py
│   │   └── test_idempotency.py
│   ├── integration/
│   │   ├── test_event_ingestion.py
│   │   ├── test_worker_dispatch.py
│   │   └── test_audit_log.py
│   └── conftest.py
├── alembic/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── README.md
└── docs/
    ├── architecture.md
    ├── compliance-assumptions.md
    └── demo-script.md

Core domain concepts:

1. Customer
Fields:
- id
- external_id
- full_name
- timezone
- phone_number
- email
- sms_consent: bool
- call_consent: bool
- email_consent: bool
- opted_out: bool
- created_at
- updated_at

2. Account
Fields:
- id
- external_id
- customer_id
- status: current, delinquent, resolved, paused
- balance_cents
- days_past_due
- created_at
- updated_at

3. InboundEvent
Examples:
- account_delinquent
- payment_failed
- payment_received
- hardship_requested
- opt_out_received
- account_paused

Fields:
- id
- source
- external_id
- event_type
- customer_external_id
- account_external_id
- payload jsonb
- received_at
- idempotency_key
- processing_status

4. OutreachTask
Fields:
- id
- account_id
- customer_id
- channel: call, sms, email
- status: scheduled, blocked, dispatching, sent, failed, cancelled
- scheduled_at
- attempt_count
- max_attempts
- idempotency_key
- policy_decision_id
- last_error
- created_at
- updated_at

5. PolicyDecision
Fields:
- id
- account_id
- customer_id
- inbound_event_id
- decision: allow, block, defer
- channel
- reasons jsonb
- evaluated_at

6. AuditEvent
Append-only. No updates in normal application flow.
Fields:
- id
- entity_type
- entity_id
- event_type
- actor_type: system, api_client, worker, policy_engine
- actor_id
- correlation_id
- payload jsonb
- created_at

Policy rules for MVP:

Rule 1: Opt-out block
If customer.opted_out is true, block all outreach.

Rule 2: Channel consent
SMS requires sms_consent.
Call requires call_consent.
Email requires email_consent.

Rule 3: Quiet hours
No call or SMS outside 09:00-20:00 in customer local timezone. Email may be scheduled any time, but the MVP can still schedule email during business hours for simplicity.

Rule 4: Contact frequency cap
Do not schedule more than 3 outbound attempts per customer per rolling 24 hours.

Rule 5: Account paused/resolved block
If account.status is paused or resolved, block outreach.

Rule 6: Event-specific routing
- account_delinquent: schedule email now, SMS in 30 minutes, call next business day at 10:00 local time
- payment_failed: schedule email now, SMS in 15 minutes if consent exists
- payment_received: cancel scheduled outreach and append audit event
- hardship_requested: block automated outreach and append escalation audit event
- opt_out_received: mark customer opted_out and cancel scheduled outreach

This is deliberately deterministic. A regulated-servicing engineering team will care that you can reason about policy evidence. A non-deterministic LLM policy engine would be theatrical and, in this context, suspiciously enthusiastic about lawsuits.

API design:

1. Health
GET /healthz
Returns service status.

2. Ingest customer/account event
POST /v1/events
Headers:
- X-Correlation-ID: optional request tracing UUID
Body:
{
  "source": "core_banking_demo",
  "external_id": "evt_123",
  "event_type": "account_delinquent",
  "customer": {
    "external_id": "cus_123",
    "full_name": "Jane Doe",
    "timezone": "America/New_York",
    "phone_number": "+141****0100",
    "email": "jane@example.com",
    "sms_consent": true,
    "call_consent": true,
    "email_consent": true
  },
  "account": {
    "external_id": "acct_456",
    "status": "delinquent",
    "balance_cents": 12500,
    "days_past_due": 14
  },
  "occurred_at": "2026-05-27T12:00:00Z",
  "metadata": {
    "source": "core_banking_demo"
  }
}

Response:
{
  "event_id": "...",
  "status": "accepted",
  "created_tasks": 3,
  "blocked_tasks": 0,
  "correlation_id": "..."
}

3. List outreach tasks
GET /v1/tasks?status=scheduled&customer_external_id=cus_123

4. Get audit trail
GET /v1/audit?correlation_id=...

5. Simulate delivery callback
POST /v1/tasks/{task_id}/delivery-result
Body:
{
  "status": "sent",
  "provider_message_id": "mock_sms_123",
  "details": {
    "latency_ms": 83
  }
}

6. Cancel scheduled outreach for account
POST /v1/accounts/{account_external_id}/cancel-outreach
Body:
{
  "reason": "payment_received"
}

Database indexes worth adding:
- inbound_events.idempotency_key unique
- inbound_events.source, external_id unique
- outreach_tasks.idempotency_key unique
- outreach_tasks.status, scheduled_at composite index
- outreach_tasks.customer_id, created_at composite index
- audit_events.correlation_id index
- audit_events.entity_type, entity_id composite index
- customers.external_id unique
- accounts.external_id unique

MVP build phases:

Phase 0: Project hygiene
Objective: Make the repo easy to run and review.
Tasks:
- Move application code from main.py into src/orchestrator/main.py
- Configure pyproject for package layout
- Add ruff.toml if not already present
- Add .env.example
- Add Dockerfile
- Add docker-compose.yml with api, worker, postgres, redis
- Add README with one-command startup
Verification:
- uv run pytest passes
- docker compose up --build starts api, postgres, redis, worker
- GET /healthz returns 200

Phase 1: Database and migrations
Objective: Persist customers, accounts, inbound events, policy decisions, outreach tasks, and audit events.
Tasks:
- Add SQLAlchemy async database setup
- Add Alembic configuration
- Create initial migration
- Add model tests that verify required constraints, especially idempotency uniqueness
Verification:
- alembic upgrade head succeeds on a fresh Postgres container
- pytest tests/integration/test_database_constraints.py passes

Phase 2: Audit log foundation
Objective: Every important system action produces a durable audit event.
Tasks:
- Implement append_audit_event helper
- Add correlation_id propagation
- Add audit event tests
- Add GET /v1/audit endpoint
Verification:
- Ingesting an event creates audit entries for event_received and event_accepted
- Audit endpoint can filter by correlation_id

Phase 3: Policy engine
Objective: Deterministically allow, block, or defer outreach per channel.
Tasks:
- Implement PolicyInput and PolicyResult schemas
- Implement opt-out, consent, quiet-hours, account-status, and frequency-cap rules
- Unit test each rule independently
- Unit test combined decisions with multiple block reasons
Verification:
- pytest tests/unit/test_policy_engine.py passes

Phase 4: Outreach planner
Objective: Convert inbound events into proposed tasks and policy decisions.
Tasks:
- Implement event-specific routing rules
- Apply policy engine per proposed task
- Persist allowed tasks as scheduled
- Persist blocked attempts as policy decisions and audit events, not tasks unless you want a visible blocked task record
Verification:
- account_delinquent creates email, SMS, and call when all policies allow
- opt_out_received cancels scheduled tasks and blocks future outreach
- payment_received cancels scheduled tasks

Phase 5: Event ingestion API
Objective: Accept external customer/account events idempotently.
Tasks:
- Add POST /v1/events request/response schemas
- Derive inbound event idempotency internally from source + external_id
- Upsert customer and account
- Persist inbound event
- Run planner
- Return accepted response
- If the same source + external_id is received again, return the original accepted result without duplicating tasks or audit events
Verification:
- Duplicate POST /v1/events with same source + external_id creates one inbound event and one set of tasks
- Invalid event_type returns 422
- Missing source or external_id returns 422

Phase 6: Worker dispatch
Objective: Background worker sends due tasks through mock adapters.
Tasks:
- Implement query for due scheduled tasks
- Implement dispatch lock/status transition: scheduled -> dispatching -> sent/failed
- Implement mock SMS, email, and call adapters
- Implement retries with max_attempts and last_error
- Append audit events for dispatch_started, dispatch_succeeded, dispatch_failed
Verification:
- Worker sends due tasks and marks them sent
- Failed adapter result increments attempt_count and reschedules until max_attempts
- No duplicate send occurs when the same task is picked twice

Phase 7: Operational APIs
Objective: Give reviewers a clear way to inspect the orchestration story.
Tasks:
- GET /v1/tasks
- GET /v1/tasks/{task_id}
- POST /v1/tasks/{task_id}/delivery-result for manual callback simulation
- POST /v1/accounts/{account_external_id}/cancel-outreach
- Optional GET /dashboard read-only HTML summary
Verification:
- You can ingest an event, list scheduled tasks, run worker, and see audit log history end-to-end

Phase 8: Demo data and demo script
Objective: Make the project reviewable in under five minutes.
Tasks:
- Add scripts/seed_demo.py
- Add docs/demo-script.md
- Add sample curl commands
- Include three demo scenarios:
  1. Happy path delinquent account schedules three outreach tasks
  2. Opted-out customer blocks all outreach and logs policy reasons
  3. Payment received cancels scheduled outreach
Verification:
- A reviewer can run one command to seed data and follow the README demo without editing code

Phase 9: Polish and portfolio narrative
Objective: Make the project interview-ready.
Tasks:
- Add docs/architecture.md with Mermaid diagram
- Add docs/compliance-assumptions.md explaining TCPA-inspired consent, quiet hours, opt-out, auditability, and what is intentionally simplified
- Add README section: “Product narrative”
- Add README section: “Production extensions I would add next”
- Add basic CI command list
Verification:
- README answers: what it does, why it matters, how to run, how to test, how to demo, what tradeoffs were made

Testing strategy:

Unit tests:
- Policy rules
- Outreach planning
- Idempotency key generation/checks
- Quiet-hours time calculations
- Frequency cap calculations

Integration tests:
- Event ingestion creates records and tasks
- Idempotent event ingestion does not duplicate records
- Audit log entries are created in correct sequence
- Worker dispatch updates task state
- Cancellation changes pending task statuses

Recommended test commands:
- uv run pytest tests/unit -q
- uv run pytest tests/integration -q
- uv run pytest --cov=orchestrator --cov-report=term-missing
- uv run ruff check .
- uv run ruff format --check .
- uv run bandit -r src
- uv run pip-audit

Compliance and audit narrative:

What to say in README/interview:
- The system treats compliance as a first-class workflow, not a post-hoc log message.
- Every inbound event gets a correlation_id.
- Every policy decision stores explicit allow/block/defer reasons.
- Every outbound attempt is idempotent and auditable.
- Opt-out and payment-received events cancel future outreach.
- Quiet hours and consent rules are deterministic and testable.
- Mock adapters isolate orchestration correctness from vendor integration complexity.

What not to claim:
- Do not claim actual TCPA compliance.
- Do not claim SOC 2, PCI, or CFPB compliance.
- Say “TCPA-inspired controls” and “audit-ready design pattern.” Accuracy is preferable to confident nonsense, particularly when applying to a compliance company.

Optional additions after MVP:

1. Sentry
Useful if you want to show production sensibility.
Add only after the core workflow works.
Value:
- Captures worker/API exceptions
- Adds trace IDs to failures
- Demonstrates incident-awareness

2. OpenTelemetry + Prometheus/Grafana
Probably too much for the first version.
Good extension if you want observability depth.
Metrics:
- events_ingested_total
- outreach_tasks_scheduled_total
- outreach_tasks_blocked_total
- dispatch_success_total
- dispatch_failure_total
- queue_lag_seconds

3. ClickHouse
Not needed for MVP.
Excellent stretch extension because regulated servicing teams often need analytics scaling.
Possible use:
- Stream audit_events/outreach_results into ClickHouse
- Build aggregate dashboard: contact attempts per hour, blocked outreach by policy reason, delivery success rate

4. Real provider sandbox
Only after mock adapters are stable.
Options:
- Twilio trial for SMS/calls
- SendGrid/Mailgun for email
Risk:
- Real communication APIs introduce account setup friction and can distract from the orchestration project.

5. Read-only dashboard
Use FastAPI templates + HTMX or a tiny static page.
Show:
- Recent inbound events
- Scheduled tasks
- Blocked policy decisions
- Audit timeline
- Dispatch outcomes

Recommended MVP cut line:
If time is short, build these and stop:
- Docker Compose with Postgres + Redis
- FastAPI event ingestion
- SQLAlchemy models + Alembic migration
- Policy engine with tests
- Outreach planner with scheduled tasks
- Worker with mock dispatch
- Audit log query endpoint
- README + demo script

Do not add a frontend before the backend story is credible.

Suggested development order with TDD:

1. Write policy rule tests first.
2. Implement policy engine.
3. Write planner tests.
4. Implement planner.
5. Write database constraint tests.
6. Implement models/migrations.
7. Write API ingestion tests.
8. Implement POST /v1/events.
9. Write idempotency tests.
10. Implement idempotency behavior.
11. Write worker dispatch tests.
12. Implement worker/adapters.
13. Write audit endpoint tests.
14. Implement audit endpoint.
15. Write README demo and run it from a clean checkout.

Interview talking points this project will support:
- Why event-driven systems need idempotency.
- Why compliance decisions should be persisted, not just recomputed later.
- How to avoid double-contacting customers under retries.
- Why OLTP audit logs are useful but may later need OLAP/ClickHouse for dashboards.
- How mock channel adapters allow testing orchestration independently of Twilio/Telnyx/SendGrid.
- How you would evolve this into a production service on AWS: SQS instead of Redis, ECS/Kubernetes, RDS Postgres, CloudWatch/Sentry, secrets manager, and provider webhooks.

Definition of done:
- docker compose up --build starts the full system
- POST /v1/events ingests a demo account event
- Policy rules produce explicit decisions
- Scheduled outreach tasks are visible via API
- Worker dispatches due mock tasks
- Audit log shows the complete timeline from event ingestion to dispatch result
- Duplicate event submission does not duplicate outreach
- Opt-out and payment-received scenarios cancel/block outreach correctly
- Tests pass
- README contains a 5-minute demo path and a clear product narrative

Final recommendation:
Start with the deterministic compliance/orchestration core. Add observability and analytics only after that foundation works. The project should make you look like someone who understands regulated workflow systems, not someone who discovered Twilio yesterday and immediately gave it a credit card.