# Architecture Notes

This document explains the current demo architecture and the production evolution path without turning the README into a design dossier.

## Architecture goal

The system demonstrates a regulated-servicing outreach workflow:

1. Accept inbound customer/account events.
2. Materialize the current customer/account snapshot needed for policy evaluation.
3. Apply deterministic compliance policy per proposed outreach channel.
4. Persist policy decisions and scheduled work.
5. Dispatch due work through mock channel adapters.
6. Preserve audit evidence for every important state transition.

## Runtime components

### FastAPI service

The API process owns HTTP ingress and operational inspection.

Implemented API areas:

- `POST /v1/events` for servicing event ingestion.
- `GET /v1/tasks` and `GET /v1/tasks/{task_id}` for task inspection.
- `POST /v1/tasks/{task_id}/delivery-result` for manual delivery-result simulation.
- `POST /v1/accounts/{account_external_id}/cancel-outreach` for account-level scheduled outreach cancellation.
- `GET /v1/audit` for audit trail queries.
- `GET /healthz` for health checks.

Request correlation middleware assigns or propagates a correlation ID so a reviewer can connect event ingestion, policy decisions, scheduled work, cancellation, and dispatch audit rows.

### Domain and orchestration layer

Domain behavior is kept outside route handlers:

- Event ingestion performs validation, idempotency checks, snapshot upserts, inbound-event persistence, audit writes, planner execution, and processed-status updates in one transaction.
- The outreach planner maps supported event types to proposed channel actions or cancellation side effects.
- The policy engine evaluates deterministic allow/block/defer decisions for each proposed channel.
- Operational outreach helpers support manual result simulation and account-level cancellation without adding a second orchestration path.

The policy engine is deliberately deterministic. Compliance-sensitive decisions are not delegated to an LLM.

### PostgreSQL

PostgreSQL stores both operational state and audit evidence:

- `customers`
- `accounts`
- `inbound_events`
- `policy_decisions`
- `outreach_tasks`
- `audit_events`

The demo uses one database so a reviewer can inspect the full workflow without also reasoning about broker state, cache state, or analytics pipelines.

### Worker process

The worker polls PostgreSQL for due scheduled tasks, conditionally claims each task, calls a mock channel adapter, and records terminal or retry state.

The claim step is conditional so stale selections do not dispatch tasks that are no longer eligible. Worker terminal writes also respect task state so manual operational updates do not get silently overwritten.

### Mock channel adapters

Mock call, SMS, and email adapters return deterministic provider-style results. They make dispatch behavior testable without requiring external accounts, credentials, carrier behavior, or email deliverability setup.

## Current demo flow

![Current demo flow](diagrams/current-target-system-flow-2026-05-30.png)

Editable source: [current-target-system-flow-2026-05-30.excalidraw](diagrams/current-target-system-flow-2026-05-30.excalidraw)

The most important flow is:

`Inbound event -> idempotency -> snapshot upsert -> audit -> policy -> planner -> task state -> worker/manual result -> audit`

## Current demo architecture

![Current demo architecture](diagrams/current-system-design-2026-05-30.png)

Editable source: [current-system-design-2026-05-30.excalidraw](diagrams/current-system-design-2026-05-30.excalidraw)

The architecture is intentionally smaller than a production deployment. The point is to make the workflow explainable and runnable locally, not to display every infrastructure component a mature servicing platform might eventually need.

## Event ingestion flow

`POST /v1/events` receives a servicing event with nested customer and account snapshots.

The ingestion transaction:

1. Validates event type, source identity, timezone, and account status.
2. Derives idempotency from `source` + `external_id`.
3. Returns the original accepted result for duplicate events without replaying side effects.
4. Upserts the customer snapshot.
5. Upserts the account snapshot linked to the customer.
6. Stores the inbound event.
7. Appends event receipt/acceptance audit evidence.
8. Invokes the planner.
9. Marks the inbound event processed.

Duplicate replay does not overwrite snapshots, duplicate tasks, or append duplicate business audit rows.

## Policy and planning flow

The planner handles supported servicing events:

- `account_delinquent`
- `payment_failed`
- `payment_received`
- `hardship_requested`
- `opt_out_received`
- `account_paused`

For outreach-producing events, the planner proposes channel-specific attempts and asks the policy engine to evaluate each one.

Policy considers:

- customer opt-out state
- channel consent
- quiet hours for call and SMS
- account status
- rolling contact frequency cap

Allowed decisions create scheduled tasks. Deferred decisions create scheduled tasks for a later compliant time. Blocked decisions persist policy evidence without creating outbound work.

State-change events such as payment received, opt-out, hardship, and account pause cancel existing scheduled outreach when appropriate.

## Dispatch flow

The worker finds due `scheduled` tasks, then atomically claims each task before making a provider call.

Task state normally moves through:

`scheduled -> dispatching -> sent`

or, for retryable failures:

`scheduled -> dispatching -> scheduled`

or, after terminal failure:

`scheduled -> dispatching -> failed`

Each dispatch attempt appends audit evidence. The worker uses mock adapters, so provider behavior is deterministic and local.

## Audit model

Audit events are append-only in normal application flow. They are used for product evidence, not just debugging.

Audit rows capture events such as:

- inbound event received
- inbound event accepted
- policy decision recorded
- outreach task scheduled
- outreach blocked
- outreach deferred
- outreach cancelled
- dispatch started
- dispatch succeeded
- dispatch failed
- manual delivery result recorded

A reviewer can query by correlation ID to reconstruct one workflow from API ingress through policy, task state, and dispatch or cancellation.

## Why PostgreSQL-backed polling in the demo

A broker-backed worker would be a reasonable production choice, but it adds infrastructure and hidden state. For this demo, PostgreSQL polling is enough to show:

- durable scheduled work
- auditable task state transitions
- retry behavior
- conditional claims before side effects
- cancellation before dispatch

This keeps the local stack small: API, worker, and PostgreSQL.

## Production evolution

The next production-oriented steps would be:

- broker-backed dispatch with SQS, Celery, Dramatiq, or Redis when throughput and retry isolation justify it
- real SMS, email, and voice providers behind the existing channel boundary
- provider callback ingestion for delivery receipts and failures
- stale-dispatch recovery for worker crashes after claims
- authentication and authorization around operational APIs
- metrics for policy blocks, dispatch latency, retries, cancellations, and worker lag
- analytics export to a warehouse or ClickHouse-style store for compliance reporting dashboards

The production version should still keep compliance policy deterministic and auditable. More infrastructure should improve reliability and scale; it should not make the decision path opaque.
