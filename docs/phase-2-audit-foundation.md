# Phase 2 — Audit foundation

## Purpose

Phase 2 turns the database foundation from Phase 1 into a small HTTP service that can accept inbound servicing events and prove what happened afterward.

The goal is not to finish the full outreach engine yet. The goal is to establish the request/audit spine that later phases can reuse when policy evaluation, scheduling, workers, and provider callbacks are added.

In a compliance-oriented outreach system, the important question is not only "did the API accept the event?" It is also "can we trace that request, show the actions taken, and avoid duplicating work when a caller retries?"

## What this phase builds

This phase introduces:

- FastAPI route modules under `src/orchestrator/api/`
- request correlation ID middleware
- minimal inbound event ingestion endpoint
- append-only audit event helper
- audit trail query endpoint
- idempotency handling for repeated inbound event requests
- API tests for correlation IDs, audit persistence, filtering, and duplicate retries

The main endpoints introduced are:

1. `POST /v1/events`
   - Accepts a minimal inbound event payload.
   - Requires `source` and `external_id` in the request body.
   - Reads or generates an `X-Correlation-ID`.
   - Derives the internal idempotency key from `source` and `external_id`.
   - Stores one `inbound_events` row for a new source event identity.
   - Appends audit events for `event_received` and `event_accepted`.
   - Returns the accepted event ID and correlation ID.

2. `GET /v1/audit`
   - Returns audit events.
   - Supports filtering by `correlation_id`.
   - Orders results by creation time and ID.

3. `GET /healthz`
   - Remains the simple service health endpoint.
   - Also participates in correlation ID propagation through middleware.

## Current request flow

For a new inbound event:

1. An API client calls `POST /v1/events`.
2. The correlation middleware reads `X-Correlation-ID` from the request header.
3. If the header is missing or invalid, the service generates a new UUID.
4. The middleware stores the correlation ID on `request.state.correlation_id`.
5. The route handler validates the body, including `source` and `external_id`.
6. The handler derives an internal idempotency key from the source event identity: `source` + `external_id`.
7. The handler checks whether an inbound event already exists for that source event identity.
8. If it is new, the handler creates an `InboundEvent` row.
9. The handler appends two audit events inside the same transaction:
   - `event_received`, attributed to `api_client`
   - `event_accepted`, attributed to `system`
10. The transaction commits.
11. The response includes the same correlation ID, and the middleware also writes it to the `X-Correlation-ID` response header.

For a duplicate inbound event retry:

1. The API client calls `POST /v1/events` again with the same `source` and `external_id`.
2. The service derives the same internal idempotency key.
3. The service finds the existing inbound event.
4. The service returns the existing event ID.
5. The service does not create another inbound event.
6. The service does not append another audit trail for the duplicate request.

This keeps retries safe. A caller can retry after a timeout without causing duplicate downstream work. In a real outreach system, this is the difference between a harmless retry and accidentally contacting the same borrower twice. One of those is merely boring; the other tends to attract compliance people with clipboards.

## What is a correlation ID?

A correlation ID is a request trace identifier.

It is not the business event ID. It is not the database row ID. It is not the idempotency key.

It answers a different question: "Which logs, audit rows, worker actions, and responses belong to the same incoming request or workflow?"

Example:

- A client sends `X-Correlation-ID: 8a5b...` when posting an event.
- The API stores audit rows using that same correlation ID.
- Later, a developer or reviewer can call `GET /v1/audit?correlation_id=8a5b...`.
- The response shows the audit events produced by that request.

The correlation ID is especially useful once later phases add background workers. One inbound request may eventually cause policy decisions, scheduled tasks, delivery attempts, provider callbacks, retries, and cancellations. The correlation ID gives us a thread to follow through that chain.

## Why correlation ID is a header, not a body field

The correlation ID is transport/request metadata, not domain data.

It belongs in a header because:

- It applies to the whole HTTP request, not only to one specific JSON schema.
- It should work for every endpoint, including `GET /healthz` and `GET /v1/audit`, which do not have request bodies.
- Middleware can process headers before the route handler runs.
- Logs, proxies, API gateways, load balancers, and tracing systems conventionally use headers for request tracing.
- It keeps the body focused on business data: event type, customer ID, account ID, and payload.

## Correlation ID vs idempotency key

These two identifiers solve different problems.

`X-Correlation-ID`:

- Used for tracing and debugging.
- May be generated by the caller or by this service.
- Can change between retries.
- Does not determine whether work is duplicated.
- Appears in response headers and audit events.

Internal idempotency key:

- Used to prevent duplicate processing.
- Generated by this service, not accepted as caller-controlled input.
- Derived from stable source event identity: `source` + `external_id`.
- Stored as a deterministic hash-prefixed key so long or separator-heavy upstream IDs do not create ambiguous keys.
- Should be stable across retries of the same logical event submission.
- Determines whether the service creates a new inbound event or returns the existing one.
- Is protected by database uniqueness from Phase 1.

A retry may have a new correlation ID but the same derived idempotency key. That is acceptable. The correlation ID tracks the retry request; the idempotency key links the retry to the same logical work item.

## Who calls these endpoints?

In the demo narrative, these endpoints are called by upstream servicing systems and operational users/tools.

### `POST /v1/events`

Likely callers:

- A loan servicing system
- A core banking system
- A CRM/customer account platform
- A webhook simulator in the demo
- A test script

The caller sends events such as:

- account became delinquent
- payment failed
- payment was received
- hardship was requested
- customer opted out
- account was paused

In this phase, the endpoint only accepts and audits the event. Later phases will use the same accepted event as the input for policy evaluation and outreach planning.

### `GET /v1/audit`

Likely callers:

- Internal dashboard
- Support or operations tooling
- Developer debugging tools
- Compliance review workflow
- Demo walkthrough script

This endpoint is intentionally read-oriented. It gives the project a visible way to explain what the system did.

### `GET /healthz`

Likely callers:

- Docker health checks
- Load balancers
- Monitoring systems
- Developer sanity checks

## Design decisions

### Treat audit as application data, not debug logging

Audit events are persisted in the database through the `audit_events` table.

Reasoning:

- Debug logs are useful for engineers but are not a reliable product-level audit trail.
- A compliance-oriented system should be able to query structured evidence.
- The audit model can later include policy decisions, blocked outreach, scheduled attempts, cancellations, dispatch results, and provider callbacks.

### Append audit events inside the same transaction

The `append_audit_event` helper flushes audit rows but does not commit.

Reasoning:

- The caller owns the transaction.
- The inbound event and its audit events should succeed or fail together.
- A helper that commits internally would make workflows harder to reason about and harder to test.

### Use middleware for correlation ID

The middleware reads `X-Correlation-ID`, validates it as a UUID, generates a replacement when missing or invalid, stores it on the request state, and writes it back to the response header.

Reasoning:

- Correlation is request metadata.
- It should apply consistently to every endpoint.
- Route handlers should not duplicate tracing boilerplate.

### Keep duplicate source events quiet in the audit trail

When `POST /v1/events` receives a duplicate `source` + `external_id`, it returns the existing event ID but does not append another audit event.

Reasoning:

- The duplicate request did not create new domain work.
- The audit trail should represent accepted domain actions, not inflate because of network retries.
- This keeps Phase 2 behavior simple and explainable.

A later production version may still log duplicate retry attempts separately in operational logs or in a lower-level request log. For this demo phase, the domain audit trail stays focused on accepted work.

## Narrative

Phase 2 makes the service demonstrably audit-aware.

A reviewer can now send an inbound event, receive a correlation ID, and query the audit trail for that request. The implementation remains small, but it shows the right instincts for regulated outreach: trace requests, preserve accepted events, avoid duplicate processing, and separate API wiring from domain helpers.
