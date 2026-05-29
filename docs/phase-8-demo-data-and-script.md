# Phase 8 — Demo Data and Walkthrough Script

Phase 8 makes the project reviewable through one repeatable demo command and a short human walkthrough.

## What changed

- Added `scripts/seed_demo.py`, a demo runner that:
  - deletes existing demo tables before seeding
  - posts inbound events through the public `POST /v1/events` API
  - runs all three reviewer scenarios in a fixed order
  - prints task and audit follow-up summaries
- Added `make demo` to run every scenario against the local API.
- Added `docs/demo-script.md` with the five-minute reviewer walkthrough.
- Updated README status and demo instructions.

## Design choice: destructive reset for demo repeatability

The demo command intentionally deletes rows from the application tables before it posts scenario events. This keeps the reviewer path deterministic and avoids idempotency making a second run look uneventful.

The reset is deliberately scoped to the demo script and documented as destructive. It deletes records in dependency order:

1. `audit_events`
2. `outreach_tasks`
3. `policy_decisions`
4. `inbound_events`
5. `accounts`
6. `customers`

The script does not drop tables or run migrations. Schema ownership remains with Alembic.

## Design choice: seed through APIs, not direct inserts

The script resets data directly, but it creates demo state only through the public ingestion API. That preserves the product story:

`POST /v1/events` -> idempotency -> snapshot upsert -> audit -> policy -> planner -> tasks

Directly inserting customers, accounts, policy decisions, and tasks would be faster, but it would bypass the exact workflow the project is meant to demonstrate. Speed is a poor substitute for evidence, sir.

## Demo scenarios

### 1. Happy path delinquent account

Posts one `account_delinquent` event for a customer with email, SMS, and call consent.

Expected result:

- three policy decisions
- three scheduled outreach tasks
- task list shows email, SMS, and call work
- audit trail explains event receipt, policy decisions, and task scheduling

### 2. Opted-out customer blocks future outreach

Posts two events for the same customer/account:

1. `opt_out_received`
2. `account_delinquent`

Expected result:

- first event records durable opt-out state
- second event evaluates outreach policy and blocks all channel attempts
- no outreach tasks are scheduled for the follow-up delinquency event
- audit trail shows the opt-out and policy block evidence

This two-step scenario is intentional. Opt-out is durable state; blocked outreach is proven when a later outreach-producing event is evaluated.

### 3. Payment received cancels scheduled outreach

Posts two events for the same customer/account:

1. `account_delinquent`
2. `payment_received`

Expected result:

- first event schedules outreach
- second event marks pending scheduled tasks as cancelled
- task list shows cancelled work
- audit trail shows cancellation evidence tied to the payment event

## Worker timing caveat

The script uses future `occurred_at` timestamps so scheduled tasks remain pending long enough for a reviewer to inspect them. This makes cancellation and planning behavior deterministic even when the Docker Compose worker is running.

The worker dispatch story remains available separately by creating due tasks or waiting until scheduled work is due. Phase 8 optimizes for a five-minute review path, not a real-time contact-center simulator. Mercifully.

## Intentionally deferred

- A browser dashboard.
- Real provider integrations.
- Production-safe reset tooling.
- Authentication around demo operations.
- A general-purpose CLI client.

The goal is a repeatable portfolio walkthrough, not another application surface area.
