from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.domain.audit_log import append_audit_event
from orchestrator.domain.inbound_events import inbound_event_idempotency_key
from orchestrator.models import (
    Account,
    AccountStatus,
    AuditActorType,
    AuditEvent,
    Customer,
    InboundEvent,
    InboundEventStatus,
    OutreachTask,
    PolicyDecision,
    PolicyDecisionOutcome,
)
from orchestrator.orchestration.planner import PlannerResult, plan_outreach_for_event


@dataclass(frozen=True)
class CustomerSnapshot:
    external_id: str
    full_name: str
    timezone: str
    phone_number: str | None
    email: str | None
    sms_consent: bool
    call_consent: bool
    email_consent: bool


@dataclass(frozen=True)
class AccountSnapshot:
    external_id: str
    status: AccountStatus
    balance_cents: int
    days_past_due: int


@dataclass(frozen=True)
class EventIngestionResult:
    event_id: UUID
    created_tasks: int
    blocked_tasks: int
    deferred_tasks: int
    cancelled_tasks: int
    policy_decisions: int


class EventIngestionValidationError(ValueError):
    """Raised when an event snapshot is invalid for ingestion."""


async def ingest_event_snapshot(
    session: AsyncSession,
    *,
    source: str,
    external_id: str,
    event_type: str,
    customer_snapshot: CustomerSnapshot,
    account_snapshot: AccountSnapshot,
    occurred_at: datetime,
    metadata: dict[str, Any],
    correlation_id: UUID,
) -> EventIngestionResult:
    existing_event = await _find_existing_event(
        session, source=source, external_id=external_id
    )
    if existing_event is not None:
        return await _result_from_existing_event(session, existing_event)

    customer = await _upsert_customer(session, customer_snapshot)
    account = await _upsert_account(session, account_snapshot, customer)
    idempotency_key = inbound_event_idempotency_key(
        source=source, external_id=external_id
    )
    inbound_event = InboundEvent(
        source=source,
        external_id=external_id,
        event_type=event_type,
        customer_external_id=customer.external_id,
        account_external_id=account.external_id,
        idempotency_key=idempotency_key,
        payload={
            "source": source,
            "external_id": external_id,
            "event_type": event_type,
            "customer": _customer_payload(customer_snapshot),
            "account": _account_payload(account_snapshot),
            "occurred_at": occurred_at.isoformat(),
            "metadata": metadata,
        },
    )
    session.add(inbound_event)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing_event = await _find_existing_event(
            session, source=source, external_id=external_id
        )
        if existing_event is not None:
            return await _result_from_existing_event(session, existing_event)
        raise

    audit_payload = {
        "source": source,
        "event_type": event_type,
        "external_id": external_id,
        "idempotency_key": idempotency_key,
    }
    await append_audit_event(
        session,
        entity_type="inbound_event",
        entity_id=str(inbound_event.id),
        event_type="event_received",
        actor_type=AuditActorType.API_CLIENT,
        correlation_id=correlation_id,
        payload=audit_payload,
    )
    await append_audit_event(
        session,
        entity_type="inbound_event",
        entity_id=str(inbound_event.id),
        event_type="event_accepted",
        actor_type=AuditActorType.SYSTEM,
        correlation_id=correlation_id,
        payload=audit_payload,
    )

    planner_result = await plan_outreach_for_event(
        session,
        inbound_event_id=inbound_event.id,
        correlation_id=correlation_id,
        now=occurred_at,
    )
    inbound_event.processing_status = InboundEventStatus.PROCESSED
    await session.flush()
    await session.commit()
    return _result_from_planner(inbound_event.id, planner_result)


async def _find_existing_event(
    session: AsyncSession, *, source: str, external_id: str
) -> InboundEvent | None:
    return await session.scalar(
        select(InboundEvent).where(
            InboundEvent.source == source,
            InboundEvent.external_id == external_id,
        )
    )


async def _upsert_customer(
    session: AsyncSession, snapshot: CustomerSnapshot
) -> Customer:
    customer = await session.scalar(
        select(Customer).where(Customer.external_id == snapshot.external_id)
    )
    if customer is None:
        customer = Customer(
            external_id=snapshot.external_id, full_name=snapshot.full_name
        )
        session.add(customer)
    customer.full_name = snapshot.full_name
    customer.timezone = snapshot.timezone
    customer.phone_number = snapshot.phone_number
    customer.email = snapshot.email
    customer.sms_consent = snapshot.sms_consent
    customer.call_consent = snapshot.call_consent
    customer.email_consent = snapshot.email_consent
    await session.flush()
    return customer


async def _upsert_account(
    session: AsyncSession, snapshot: AccountSnapshot, customer: Customer
) -> Account:
    account = await session.scalar(
        select(Account).where(Account.external_id == snapshot.external_id)
    )
    if account is None:
        account = Account(
            external_id=snapshot.external_id,
            customer=customer,
            status=snapshot.status,
        )
        session.add(account)
    elif account.customer_id != customer.id:
        raise EventIngestionValidationError(
            "Account external_id already belongs to another customer"
        )

    account.status = snapshot.status
    account.balance_cents = snapshot.balance_cents
    account.days_past_due = snapshot.days_past_due
    await session.flush()
    return account


async def _result_from_existing_event(
    session: AsyncSession, event: InboundEvent
) -> EventIngestionResult:
    policy_decisions = await session.scalar(
        select(func.count())
        .select_from(PolicyDecision)
        .where(PolicyDecision.inbound_event_id == event.id)
    )
    blocked_tasks = await session.scalar(
        select(func.count())
        .select_from(PolicyDecision)
        .where(
            PolicyDecision.inbound_event_id == event.id,
            PolicyDecision.decision == PolicyDecisionOutcome.BLOCK,
        )
    )
    deferred_tasks = await session.scalar(
        select(func.count())
        .select_from(PolicyDecision)
        .where(
            PolicyDecision.inbound_event_id == event.id,
            PolicyDecision.decision == PolicyDecisionOutcome.DEFER,
        )
    )
    created_tasks = await session.scalar(
        select(func.count())
        .select_from(OutreachTask)
        .join(PolicyDecision, OutreachTask.policy_decision_id == PolicyDecision.id)
        .where(PolicyDecision.inbound_event_id == event.id)
    )
    cancelled_tasks = await session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.entity_type == "outreach_task",
            AuditEvent.event_type == "outreach_cancelled",
            AuditEvent.payload["inbound_event_id"].as_string() == str(event.id),
        )
    )
    return EventIngestionResult(
        event_id=event.id,
        created_tasks=created_tasks or 0,
        blocked_tasks=blocked_tasks or 0,
        deferred_tasks=deferred_tasks or 0,
        cancelled_tasks=cancelled_tasks or 0,
        policy_decisions=policy_decisions or 0,
    )


def _result_from_planner(
    event_id: UUID, planner_result: PlannerResult
) -> EventIngestionResult:
    return EventIngestionResult(
        event_id=event_id,
        created_tasks=planner_result.created_tasks,
        blocked_tasks=planner_result.blocked_attempts,
        deferred_tasks=planner_result.deferred_attempts,
        cancelled_tasks=planner_result.cancelled_tasks,
        policy_decisions=planner_result.policy_decisions,
    )


def _customer_payload(snapshot: CustomerSnapshot) -> dict[str, Any]:
    return {
        "external_id": snapshot.external_id,
        "full_name": snapshot.full_name,
        "timezone": snapshot.timezone,
        "phone_number": snapshot.phone_number,
        "email": snapshot.email,
        "sms_consent": snapshot.sms_consent,
        "call_consent": snapshot.call_consent,
        "email_consent": snapshot.email_consent,
    }


def _account_payload(snapshot: AccountSnapshot) -> dict[str, Any]:
    return {
        "external_id": snapshot.external_id,
        "status": snapshot.status.value,
        "balance_cents": snapshot.balance_cents,
        "days_past_due": snapshot.days_past_due,
    }
