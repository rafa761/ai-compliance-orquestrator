# Demo Script

This walkthrough shows the compliant outreach orchestrator story in under five minutes.

## Prerequisites

Install dependencies:

```bash
uv sync
```

Create local configuration if needed:

```bash
cp .env.example .env
```

Start the local stack:

```bash
make docker-up
```

Apply migrations:

```bash
make migrate
```

Check the API:

```bash
make health
```

Expected response:

```json
{"service":"compliant-outreach-orchestrator","status":"ok"}
```

## Run all demo scenarios

```bash
make demo
```

Warning: this command deletes existing rows from the demo database before posting scenario events. It is intended for local demo use only.

The command runs:

1. happy-path delinquent account
2. opt-out blocks future outreach
3. payment received cancels scheduled outreach

It prints the event responses, task summaries, and audit summaries for each scenario.

## Scenario 1: happy-path delinquent account

What happens:

- The script posts an `account_delinquent` event for Jane Doe.
- The API upserts the customer and account snapshot.
- The planner evaluates email, SMS, and call policy.
- Three outreach tasks are scheduled.

Useful inspection command:

```bash
curl 'http://localhost:8000/v1/tasks?account_external_id=acct_demo_jane_doe'
```

What to point out:

- policy decisions are persisted
- tasks are scheduled only after deterministic policy evaluation
- each task has customer/account context and a correlation ID

## Scenario 2: opt-out blocks future outreach

What happens:

- The script posts `opt_out_received` for Sam Taylor.
- The planner marks the customer opted out.
- The script then posts `account_delinquent` for the same customer/account.
- The policy engine blocks future outreach because durable opt-out state exists.

Useful inspection command:

```bash
curl 'http://localhost:8000/v1/tasks?account_external_id=acct_demo_sam_taylor'
```

Also copy the printed correlation ID from the second Sam event and inspect audit evidence:

```bash
curl 'http://localhost:8000/v1/audit?correlation_id=<printed-correlation-id>'
```

What to point out:

- opt-out is not just a request flag
- ordinary later snapshots cannot erase durable opt-out state
- blocked attempts produce policy evidence without creating outreach tasks

## Scenario 3: payment received cancels scheduled outreach

What happens:

- The script posts `account_delinquent` for Maria Santos.
- The planner schedules outreach.
- The script posts `payment_received` for the same account.
- Pending scheduled outreach is cancelled.

Useful inspection command:

```bash
curl 'http://localhost:8000/v1/tasks?account_external_id=acct_demo_maria_santos'
```

What to point out:

- cancellation is event-driven
- scheduled work becomes historical evidence instead of disappearing
- audit rows explain why outreach stopped

## Manual API controls to show if asked

List all tasks:

```bash
curl 'http://localhost:8000/v1/tasks'
```

Inspect one task:

```bash
curl 'http://localhost:8000/v1/tasks/<task-id>'
```

Record a manual delivery result:

```bash
curl -X POST 'http://localhost:8000/v1/tasks/<task-id>/delivery-result' \
  -H 'Content-Type: application/json' \
  -d '{"status":"sent","provider_message_id":"manual-demo-001","details":{"provider_status":"accepted"}}'
```

Cancel scheduled outreach for an account:

```bash
curl -X POST 'http://localhost:8000/v1/accounts/acct_demo_jane_doe/cancel-outreach' \
  -H 'Content-Type: application/json' \
  -d '{"reason":"manual reviewer demo"}'
```

## Interview narrative

A concise explanation:

This is a regulated-servicing outreach demo. External servicing events are accepted idempotently, customer/account snapshots are materialized, deterministic policy decides whether outreach may happen, scheduled work is stored, and every important action appends audit evidence. Mock channel adapters keep the demo focused on orchestration correctness rather than provider setup.

Good claims:

- deterministic policy decisions
- audit-ready workflow evidence
- idempotent event ingestion
- cancellation for opt-out and payment events
- mock dispatch boundary for provider-independent testing

Do not claim:

- actual TCPA compliance
- real SMS, call, or email delivery
- production authentication or authorization
- legal sufficiency
