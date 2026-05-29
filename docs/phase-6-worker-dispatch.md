# Phase 6 — Worker dispatch

Phase 6 implements outbound dispatch for due outreach tasks through deterministic mock channel adapters.

## What changed

- `OutreachTask.correlation_id` is now required and is copied from the planner request correlation ID when tasks are created.
- A DB-backed async worker poller claims due scheduled tasks and sends them through channel adapters.
- Mock adapters exist for SMS, email, and call channels. They always succeed and return auditable provider IDs such as `mock_sms:{task_id}`.
- Dispatch lifecycle events are written to the append-only audit log:
  - `dispatch_started`
  - `dispatch_succeeded`
  - `dispatch_failed`
- Docker Compose now runs the worker with `python -m orchestrator.workers.dispatch --poll-interval 5`.

## Why a DB-backed poller

The project already stores outreach tasks in PostgreSQL with status, schedule time, attempts, and audit correlation. For this phase, a direct database poller keeps the implementation small and explainable without adding Dramatiq, RQ, or a separate broker contract.

That tradeoff fits the phase goal: prove the orchestration boundary, retry semantics, duplicate-send guard, and audit trail before introducing production queue infrastructure.

## Claim and duplicate-send guard

The worker selects due tasks with:

- `status == scheduled`
- `scheduled_at <= now`

Before calling an adapter, it claims each task with a conditional update:

- match the selected task ID
- require the status to still be `scheduled`
- set status to `dispatching`
- increment `attempt_count`

If another worker or prior run already changed the row, the update affects zero rows and the task is skipped. The provider send only happens after a successful claim.

The worker commits the claim and `dispatch_started` audit row before calling the adapter, so provider I/O is not held inside a long database transaction.

## Success semantics

On adapter success, the task is marked `sent`, `last_error` is cleared, and `dispatch_succeeded` is audited with:

- `provider_message_id`
- adapter `details`

The default mock adapters return predictable provider IDs:

- `mock_sms:{task_id}`
- `mock_email:{task_id}`
- `mock_call:{task_id}`

## Failure and retry semantics

Adapter exceptions and missing channel adapters are treated as dispatch failures.

On failure:

- `last_error` records the error message.
- If `attempt_count < max_attempts`, the task returns to `scheduled` and `scheduled_at` moves to `now + retry_delay`.
- If attempts are exhausted, the task is marked `failed`.
- `dispatch_failed` is audited with `will_retry`, `retry_scheduled_at`, and `final_status`.

## Intentionally deferred

Phase 6 does not add:

- stale `dispatching` recovery for workers that crash after claim and before final status
- real SMS, email, or call providers
- provider webhook/callback ingestion
- operational worker APIs or dashboards
- distributed locks or broker-backed queues

For a real provider integration, adapters should use `task.idempotency_key` or a provider-side idempotency token. A timeout after provider acceptance can otherwise be retried as a second send, which is precisely the sort of small inconvenience regulators enjoy documenting.

Those are better handled in later phases once the operational API and provider integration requirements are explicit.
