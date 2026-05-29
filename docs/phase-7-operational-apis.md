# Phase 7 — Operational APIs

Phase 7 adds operator-facing API endpoints for inspecting outreach work, manually recording demo delivery results, and cancelling pending account outreach.

## What changed

- Added task inspection endpoints:
  - `GET /v1/tasks`
  - `GET /v1/tasks/{task_id}`
- Added manual delivery-result simulation:
  - `POST /v1/tasks/{task_id}/delivery-result`
- Added account-level cancellation for pending outreach:
  - `POST /v1/accounts/{account_external_id}/cancel-outreach`
- Kept route handlers and schemas in dedicated `orchestrator.api` modules.
- Kept state-change helpers under `orchestrator.domain.operational_outreach`.

## Task listing

`GET /v1/tasks` returns outreach tasks with customer/account context so a demo operator can move from an inbound event to the scheduled work it produced.

Supported filters:

- `status`
- `customer_external_id`
- `account_external_id`
- `channel`
- `limit`, default `50`, maximum `100`

The detail endpoint includes the associated policy-decision context when a task has one. That keeps the operational view tied to deterministic compliance evidence rather than a detached work queue row.

## Manual delivery-result simulation

`POST /v1/tasks/{task_id}/delivery-result` accepts:

```json
{
  "status": "sent",
  "provider_message_id": "provider-123",
  "details": {"provider_status": "accepted"}
}
```

Allowed request statuses are `sent` and `failed`.

The endpoint is intentionally narrow:

- `scheduled`, `dispatching`, and `failed` tasks can be manually marked `sent` or `failed`.
- `cancelled` tasks are rejected with `409 Conflict`.
- `sent` clears `last_error`.
- `failed` sets `last_error` from `details.error` when it is a string, otherwise to `manual delivery result failed`.
- No retry scheduling is performed here; retry behavior remains owned by the worker.

Each accepted manual result appends `delivery_result_recorded` with actor `api_client`, actor ID `operational-api`, the task correlation ID, provider message ID, details, previous status, and new status.

## Account cancellation

`POST /v1/accounts/{account_external_id}/cancel-outreach` accepts:

```json
{
  "reason": "customer requested agent review"
}
```

The endpoint:

- returns `404` when the account external ID is unknown
- requires a non-empty reason
- does not mutate `account.status`
- cancels only currently `scheduled` tasks for that account
- leaves `dispatching`, `sent`, `failed`, and already `cancelled` tasks as historical or inactive records

Each cancelled task receives an `outreach_cancelled` audit event with actor `api_client`, actor ID `operational-api`, the request correlation ID, reason, account external ID, channel, and `cancelled_by=operational_api`.

## Verification story

A complete demo flow is now possible through APIs:

1. Ingest a servicing event with `POST /v1/events`.
2. List resulting work with `GET /v1/tasks`.
3. Inspect one work item and its policy decision with `GET /v1/tasks/{task_id}`.
4. Either manually record a delivery result or cancel scheduled account outreach.
5. Query `GET /v1/audit` to see the end-to-end evidence trail.

## Intentionally deferred

Phase 7 does not add a dashboard, provider webhook ingestion, retry scheduling through the operational API, or real provider integrations. The dashboard remains a later optional layer on top of these JSON APIs.
