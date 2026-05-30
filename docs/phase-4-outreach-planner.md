# Phase 4 — Outreach planner

## Purpose

Phase 4 connects inbound events, deterministic policy decisions, scheduled outreach tasks, cancellations, and audit evidence.

The planner is the application service that turns an already-stored servicing event into concrete next steps. It does not decide compliance rules itself. Instead, it proposes channel-specific outreach, calls the deterministic policy engine, persists the decision evidence, creates scheduled work only when allowed or explicitly deferred to a safe time, and records what happened in the audit trail.

## What this phase builds

This phase introduces:

- `src/orchestrator/orchestration/`
- `PlannerResult`
- `plan_outreach_for_event`
- outreach proposal routing for supported event types
- persisted `PolicyDecision` rows for evaluated channels
- persisted `OutreachTask` rows for scheduled work
- cancellation handling for resolution, opt-out, hardship, and account pause events
- idempotency guards for repeat planner calls
- tests for planning, blocking, deferral, cancellation, escalation, and unsupported events

## Phase boundary

The planner works over existing `Customer`, `Account`, and `InboundEvent` rows.

It does not expand `POST /v1/events` to accept nested customer/account snapshots yet. Full snapshot ingestion and upsert behavior remain Phase 5.

That boundary keeps this phase easy to explain: the planner assumes facts already exist in the database, then decides what work should be scheduled or cancelled. In Phase 4, the population of `policy_decisions` and `outreach_tasks` is verified through the planner service and its tests, not through the public ingestion endpoint.

## Event routing behavior

Supported policy-planning events:

- `account_delinquent`
  - propose email immediately
  - propose SMS at now plus 30 minutes
  - propose call on the next business day at 10:00 in the customer's local timezone

- `payment_failed`
  - propose email immediately
  - propose SMS at now plus 15 minutes

Supported cancellation or state-change events:

- `payment_received`
  - cancel scheduled outreach tasks for the account

- `opt_out_received`
  - mark the customer as opted out
  - cancel scheduled outreach tasks for the account/customer

- `hardship_requested`
  - cancel scheduled outreach tasks for the account
  - append a hardship escalation audit event

- `account_paused`
  - mark the account as paused
  - cancel scheduled outreach tasks for the account

## Policy decision persistence

For each proposed outreach channel, the planner builds a `PolicyInput` and calls the existing deterministic `evaluate_policy` function.

The planner persists one `PolicyDecision` row for each evaluated channel with:

- account
- customer
- inbound event
- channel
- decision outcome: `allow`, `block`, or `defer`
- reason strings returned by the policy engine

Decision behavior:

- `allow`
  - persist the policy decision
  - create a scheduled outreach task at the proposed time
  - append `policy_decision_recorded` and `outreach_task_scheduled` audit events

- `block`
  - persist the policy decision
  - do not create an outreach task
  - append `policy_decision_recorded` and `outreach_blocked` audit events

- `defer` with `defer_until`, except frequency-cap deferrals
  - persist the policy decision
  - create a scheduled outreach task at `defer_until`
  - append `policy_decision_recorded`, `outreach_deferred`, and `outreach_task_scheduled` audit events

- `defer` without `defer_until`, or with `frequency_cap_exceeded` in the decision reasons
  - persist the policy decision
  - do not create an outreach task
  - append `policy_decision_recorded` and `outreach_deferred` audit events

## Deferred scheduling behavior

The policy engine remains the source of truth for quiet-hours deferral.

Calls and SMS outside the allowed local contact window are deferred to the next valid 09:00 local time. Email is not quiet-hours restricted in this demo phase.

Frequency-cap deferral is handled by passing a rolling recent-attempt count into the policy engine. The `frequency_cap_exceeded` reason is authoritative for scheduling: when it is present, the planner records the defer decision but does not create an `OutreachTask`, even if another deferral reason such as quiet hours also supplies a concrete `defer_until`.

## Frequency cap calculation

The planner calculates `recent_outbound_attempt_count` from `OutreachTask` rows for the same customer created within the previous 24 hours.

Included statuses:

- `scheduled`
- `dispatching`
- `sent`
- `failed`

Excluded statuses:

- `cancelled`
- `blocked`

The planner also increments the effective count inside a single planning run whenever it schedules a task. That prevents one `account_delinquent` event from scheduling past the configured cap while processing email, SMS, and call proposals.

## Cancellation behavior

Cancellation events update existing scheduled tasks to `cancelled` and append `outreach_cancelled` audit events.

This phase cancels scheduled work only. Worker dispatch and in-flight provider behavior are still planned for later phases.

## Idempotency design

The planner is application-level repeat safe for sequential calls with the same inbound event. It is not a production-grade concurrent locking mechanism.

For policy-planning events, the simple guard is:

- if any `PolicyDecision` already exists for the inbound event, return an empty `PlannerResult` and do not write duplicate decisions, tasks, or audit events.

For state-change and cancellation events, the planner writes distinct processed markers on the inbound event audit entity, such as:

- `planner_payment_received_processed`
- `planner_opt_out_processed`
- `planner_hardship_processed`
- `planner_account_paused_processed`

These markers are separate from business audit events like `customer_opted_out` or `outreach_cancelled`, so retries can be detected even when no scheduled tasks existed to cancel on the first run.

Outreach tasks also use deterministic idempotency keys:

```text
outreach_task:{inbound_event_id}:{channel}
```

The key intentionally does not include `scheduled_at`, so a retry cannot create a duplicate task for the same inbound event and channel.

## Audit events added

This phase appends audit evidence for:

- `policy_decision_recorded`
- `outreach_task_scheduled`
- `outreach_blocked`
- `outreach_deferred`
- `outreach_cancelled`
- `customer_opted_out`
- `hardship_escalation_required`
- `account_paused`

Audit payloads include the inbound event context and channel or reason details where relevant.

## Phase boundary

At the Phase 4 boundary, the planner was still exercised as a domain component rather than through the full public event ingestion path. Later phases intentionally added the remaining runtime wiring:

- Phase 5 calls the planner from `POST /v1/events` and ingests nested customer/account snapshots from webhook payloads.
- Phase 6 dispatches scheduled tasks through the DB-backed worker and mock provider adapters.
- Phase 7 exposes task, policy decision, delivery-result, and cancellation operations through public APIs.

That sequencing kept the planner small, testable, and clear enough to discuss before adding runtime orchestration.

## Narrative

Phase 4 is where the service starts to look like an orchestrator instead of only an event receiver and a policy library.

An inbound event can now cause scheduled work, but only after deterministic policy evaluation. Every channel decision is persisted. Every scheduled task is linked back to a decision. Blocks and deferrals leave evidence. Resolution, opt-out, hardship, and pause events stop pending outreach.

The important product point is that the planner integrates compliance into the workflow rather than treating it as a later review step. The system does not merely send messages. It explains why it did or did not schedule each customer contact.
