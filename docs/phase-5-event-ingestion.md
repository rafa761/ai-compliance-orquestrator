# Phase 5 — Full event ingestion

## Purpose

Phase 5 turns `POST /v1/events` into the public ingestion workflow for servicing events.

The endpoint now accepts a source event identity plus nested customer and account snapshots. The service validates the request, performs idempotency checks, upserts operational snapshots, stores the inbound event, appends audit evidence, and calls the outreach planner in one transaction.

Compliance decisions remain deterministic. The ingestion API does not use an LLM for allow, block, defer, or cancellation decisions.

## What this phase builds

This phase introduces:

- nested `POST /v1/events` request schemas in `src/orchestrator/api/events.py`
- `src/orchestrator/domain/event_ingestion.py`
- customer snapshot upsert by `customer.external_id`
- account snapshot upsert by `account.external_id`
- account/customer ownership validation
- event-type and timezone validation before database writes
- duplicate `source` + `external_id` handling before snapshot mutation
- one transaction for new event ingestion, audit appends, planner execution, and final event status update
- a flat response with planner counts

## Request shape

```json
{
  "source": "core_banking_demo",
  "external_id": "evt_123",
  "event_type": "account_delinquent",
  "customer": {
    "external_id": "cus_123",
    "full_name": "Jane Doe",
    "timezone": "America/New_York",
    "phone_number": "+14155550100",
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
  "metadata": {"source": "core_banking_demo"}
}
```

Supported event types:

- `account_delinquent`
- `payment_failed`
- `payment_received`
- `hardship_requested`
- `opt_out_received`
- `account_paused`

Unsupported event types return `422` before database writes. Invalid customer timezones and naive `occurred_at` timestamps also return `422` before the planner can raise time-related errors.

## Response shape

```json
{
  "event_id": "00000000-0000-0000-0000-000000000000",
  "status": "accepted",
  "created_tasks": 3,
  "blocked_tasks": 0,
  "deferred_tasks": 1,
  "cancelled_tasks": 0,
  "policy_decisions": 3,
  "correlation_id": "11111111-1111-1111-1111-111111111111"
}
```

`created_tasks` counts persisted outreach tasks. `blocked_tasks`, `deferred_tasks`, and `policy_decisions` come from deterministic policy decisions. `cancelled_tasks` is populated for cancellation-style events.

## Transaction behavior

For a new event, ingestion performs the following steps:

1. Check for an existing inbound event with the same `source` and `external_id`.
2. Upsert the customer snapshot by `customer.external_id`.
3. Upsert the account snapshot by `account.external_id` and verify it belongs to the same customer.
4. Store the inbound event with source identity, customer/account external IDs, idempotency key, occurred timestamp, metadata, and full snapshots.
5. Append `event_received` and `event_accepted` audit rows.
6. Call the outreach planner.
7. Mark the inbound event as `processed`.
8. Commit once.

If any domain validation fails after request parsing, the transaction is rolled back and the API returns `422`.

## Duplicate behavior

The event identity is `source` + `external_id`.

When a duplicate source event arrives:

- no customer or account snapshot is mutated
- no inbound event is inserted
- no audit rows are appended
- the planner is not called
- the API returns the original `event_id`
- planner counts are reconstructed from existing policy, task, and audit rows
- the response uses the current request correlation ID

The same `external_id` from a different `source` is a distinct inbound event and can reuse the same customer/account rows.

## Snapshot behavior

Customer upsert updates mutable fields:

- `full_name`
- `timezone`
- `phone_number`
- `email`
- `sms_consent`
- `call_consent`
- `email_consent`

A previously opted-out customer is not reset by normal event snapshots. Opt-out remains sticky once set by opt-out processing.

Account upsert updates:

- `status`
- `balance_cents`
- `days_past_due`

If an existing account external ID belongs to another customer, ingestion rejects the request with `422` instead of silently reassigning ownership.

## Audit behavior

New accepted events append:

- `event_received`
- `event_accepted`

The planner then appends its own policy, scheduling, deferral, block, cancellation, or state-change audit rows using the same correlation ID.

Duplicate event retries do not append audit rows because nothing new happened.

## Test coverage

Phase 5 tests cover:

- full nested account delinquency ingestion
- mutable snapshot updates before planning
- duplicate retry behavior and reconstructed counts
- unsupported event type validation
- missing source/external ID validation
- invalid timezone and naive timestamp validation
- account ownership conflicts
- same external ID from different sources
- duplicate cancellation event count reconstruction
- sticky customer opt-out behavior
