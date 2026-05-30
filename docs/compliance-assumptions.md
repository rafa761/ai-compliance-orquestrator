# Compliance Assumptions

This document explains the compliance-inspired assumptions used by the demo. It is not legal advice and does not claim actual TCPA, CFPB, SOC 2, PCI, or servicing-regulation compliance.

The goal is narrower: show how a backend outreach workflow can make compliance-sensitive decisions deterministic, testable, and auditable.

## Accurate claim

This project demonstrates TCPA-inspired controls and audit-ready workflow patterns for regulated servicing outreach.

That means:

- policy decisions are made before outreach is scheduled or dispatched
- decision reasons are persisted
- opt-out and cancellation events create durable state changes
- duplicate inbound events do not duplicate outreach
- audit evidence can explain why outreach was allowed, deferred, blocked, cancelled, sent, or failed

It does not mean the policy set is legally complete or production-ready.

## Consent assumptions

The demo models channel consent as explicit boolean fields on the customer snapshot:

- `email_consent`
- `sms_consent`
- `call_consent`

Policy behavior:

- SMS outreach requires SMS consent.
- Call outreach requires call consent.
- Email outreach requires email consent.
- Missing consent blocks the corresponding channel.

Simplification:

The demo assumes the upstream servicing system already owns consent collection and sends the latest customer snapshot. A production system would need stronger consent provenance, timestamps, source-of-consent metadata, revocation history, and potentially jurisdiction-specific rules.

## Opt-out assumptions

Opt-out is modeled as durable customer state.

When an `opt_out_received` event is processed:

- the customer is marked opted out
- scheduled outreach for the affected customer/account is cancelled
- later outreach-producing events are blocked even if a later source snapshot omits or contradicts the opt-out flag

This is intentional. Opt-out should not behave like an ordinary mutable profile field that can be accidentally erased by a stale upstream snapshot.

Simplification:

The demo uses a single global opt-out flag. A production system may need channel-specific opt-outs, source-specific suppression, campaign-level suppression, regulatory do-not-call lists, and time-scoped consent state.

## Quiet-hours assumptions

The demo applies quiet-hours policy to call and SMS channels.

Policy behavior:

- Call and SMS should not be sent outside the allowed local-time window.
- Customer timezone is required and validated during event ingestion.
- Outreach that falls outside the allowed window is deferred to a compliant future time rather than silently dropped.

Simplification:

The demo uses a simple policy window instead of a complete jurisdictional rules engine. Production policy may need state-specific rules, holidays, customer preference windows, daylight-saving edge cases, and provider-level enforcement safeguards.

## Contact-frequency assumptions

The demo enforces a rolling contact-frequency cap for customer outreach.

Policy behavior:

- The system counts recent outreach attempts for the customer.
- If the cap is reached, additional proposed outreach is blocked or deferred according to the policy result.
- Decisions persist explicit reasons so a reviewer can see that the frequency cap affected the outcome.

Simplification:

The demo uses a straightforward application-level frequency rule. A production implementation might distinguish successful contacts from attempts, separate channels, account for campaign priority, coordinate across products, and use an analytics store for high-volume historical counting.

## Account-state assumptions

The account snapshot includes status values such as delinquent, current, resolved, paused, and hardship-related events.

Policy behavior:

- Resolved or paused accounts block new automated outreach.
- Payment received cancels scheduled outreach for the account.
- Hardship requests block automated outreach and create escalation evidence.
- Account pause events cancel scheduled outreach where appropriate.

Simplification:

The demo models only the account states needed for the workflow. Production servicing systems usually have richer state machines, dispute handling, bankruptcy protections, promise-to-pay flows, and human-review queues.

## Auditability assumptions

The demo treats audit events as product state.

Audit evidence is appended for important workflow actions, including:

- event receipt and acceptance
- policy decisions
- task scheduling
- blocked outreach
- deferred outreach
- cancellations
- dispatch attempts and results
- manual delivery-result simulation

The audit log is queryable by correlation ID so one workflow can be reconstructed from ingress to outcome.

Simplification:

The demo keeps audit rows in PostgreSQL. A production system may export audit events to immutable storage, a warehouse, or a dedicated compliance reporting pipeline. It may also need retention policies, tamper-evidence controls, access controls, and formal audit schemas.

## Idempotency assumptions

Inbound event idempotency is derived from stable source identity:

`source + external_id`

Policy behavior:

- The first event submission performs the full ingestion and planning workflow.
- Duplicate submissions return the original accepted result.
- Duplicate submissions do not mutate customer/account snapshots.
- Duplicate submissions do not create duplicate tasks, policy decisions, or business audit events.

Simplification:

The demo focuses on application-level idempotency for sequential retries. A production system would also need distributed concurrency controls, provider idempotency tokens, message broker deduplication strategy, and replay tooling.

## Mock-provider assumptions

The demo uses mock call, SMS, and email adapters.

This is intentional because the project is about orchestration correctness, not vendor setup.

Mock providers let the workflow prove:

- due tasks are claimed before dispatch
- task state transitions are persisted
- retry behavior is auditable
- manual operational results do not get silently overwritten

Production providers would introduce provider-specific failure modes, callback signatures, delivery receipts, rate limits, credential management, and legal/commercial constraints.

## What is intentionally not claimed

This demo does not claim:

- actual TCPA compliance
- actual CFPB compliance
- SOC 2, PCI, or security certification
- legal sufficiency
- real customer communication
- production authorization around operational APIs
- complete jurisdiction-aware policy coverage

The more credible claim is that the design shows the right engineering instincts for regulated outreach: deterministic policy gates, durable suppression state, idempotent ingestion, auditable decisions, and clear production extension boundaries.
