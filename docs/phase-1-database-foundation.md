# Phase 1 — Database foundation

## Purpose

Phase 1 establishes the smallest useful persistence layer for the Compliant Outreach Orchestrator demo.

The goal is not to build a production-grade compliance platform. The goal is to show that the project understands servicing concerns: customer/account state, inbound account events, policy decisions, scheduled outreach, and an auditable trail of what happened and why.

## What this phase builds

This phase introduces:

- SQLAlchemy async database setup
- Pydantic Settings-based environment loading
- Alembic migrations
- PostgreSQL-compatible schema
- Core domain tables
- model tests for relationships, idempotency, and schema shape
- Docker Compose database support

The six domain tables are:

1. customers
   - Borrower/customer contact profile
   - Consent flags for SMS, calls, and email
   - Opt-out flag for outreach suppression

2. accounts
   - Account-level servicing state
   - Balance and delinquency indicators
   - Link back to the customer

3. inbound_events
   - Incoming account/customer events accepted by the API
   - Idempotency key to prevent duplicate processing
   - Raw payload preserved for traceability

4. policy_decisions
   - Result of compliance/policy evaluation
   - Decision outcome: allow, block, or defer
   - Reasons list explaining why the decision was made

5. outreach_tasks
   - Scheduled outbound work items
   - Channel: call, SMS, or email
   - Status and attempt metadata
   - Idempotency key to avoid duplicate contact attempts

6. audit_events
   - Structured event log for important domain actions
   - Correlation ID for following a request across the system
   - Actor type and payload for auditability

## Design decisions

### Keep idempotency uniqueness in the database

We do keep unique indexes/constraints for idempotency keys and external IDs.

Reasoning:

- Idempotency is central to compliant outreach: duplicate events must not produce duplicate customer contact.
- A database uniqueness guard is simple, easy to test, and directly tied to the product story.

### Preserve raw payloads

Inbound events and audit events include JSON payload fields.

Reasoning:

- The system should be able to explain what data arrived and what action was taken.
- Keeping payloads makes the audit trail more convincing without requiring a complex event store.
- For the demo, JSON columns are sufficient and pragmatic.

### Model audit as a first-class feature

Audit events are not treated as debug logs.

Reasoning:

- Compliance-oriented systems need traceability.
- The audit table gives us a place to show correlation IDs, actor attribution, and decision history.
- Later API endpoints can expose these events for debugging and demonstration.

## Narrative

I kept database constraints light because this is a demo, but retained uniqueness for idempotency because preventing duplicate contact is part of the core compliance.
