from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.domain.audit_log import append_audit_event
from orchestrator.models import (
    Account,
    AccountStatus,
    AuditActorType,
    AuditEvent,
    Customer,
    InboundEvent,
    OutreachChannel,
    OutreachTask,
    OutreachTaskStatus,
    PolicyDecision,
    PolicyDecisionOutcome,
)
from orchestrator.policy.engine import PolicyInput, evaluate_policy

_RECENT_ATTEMPT_STATUSES = {
    OutreachTaskStatus.SCHEDULED,
    OutreachTaskStatus.DISPATCHING,
    OutreachTaskStatus.SENT,
    OutreachTaskStatus.FAILED,
}
_SCHEDULED_STATUSES = {OutreachTaskStatus.SCHEDULED}
_PROCESSED_MARKERS = {
    "payment_received": "planner_payment_received_processed",
    "opt_out_received": "planner_opt_out_processed",
    "hardship_requested": "planner_hardship_processed",
    "account_paused": "planner_account_paused_processed",
}


@dataclass(frozen=True)
class PlannerResult:
    inbound_event_id: UUID
    created_tasks: int = 0
    cancelled_tasks: int = 0
    policy_decisions: int = 0
    blocked_attempts: int = 0
    deferred_attempts: int = 0


@dataclass(frozen=True)
class ProposedOutreach:
    channel: OutreachChannel
    scheduled_at: datetime


async def plan_outreach_for_event(
    session: AsyncSession,
    *,
    inbound_event_id: UUID,
    correlation_id: UUID,
    now: datetime | None = None,
    frequency_cap: int = 3,
) -> PlannerResult:
    """Plan outreach for an existing inbound event inside a caller-owned transaction."""

    now = _aware_now(now)
    inbound_event = await session.get(InboundEvent, inbound_event_id)
    if inbound_event is None:
        raise ValueError(f"Inbound event not found: {inbound_event_id}")

    customer, account = await _load_customer_and_account(session, inbound_event)

    if inbound_event.event_type in {"account_delinquent", "payment_failed"}:
        return await _plan_policy_event(
            session,
            inbound_event=inbound_event,
            customer=customer,
            account=account,
            correlation_id=correlation_id,
            now=now,
            frequency_cap=frequency_cap,
        )

    if inbound_event.event_type == "payment_received":
        return await _handle_cancellation_event(
            session,
            inbound_event=inbound_event,
            customer=customer,
            account=account,
            correlation_id=correlation_id,
        )
    if inbound_event.event_type == "opt_out_received":
        if await _has_processed_marker(session, inbound_event):
            return PlannerResult(inbound_event_id=inbound_event.id)
        customer.opted_out = True
        await _audit(
            session,
            inbound_event,
            correlation_id,
            event_type="customer_opted_out",
            entity_type="customer",
            entity_id=str(customer.id),
            payload={"customer_external_id": customer.external_id},
        )
        cancelled = await _cancel_scheduled_tasks(
            session,
            inbound_event=inbound_event,
            customer=customer,
            account=account,
            correlation_id=correlation_id,
        )
        await _append_processed_marker(session, inbound_event, correlation_id)
        return PlannerResult(
            inbound_event_id=inbound_event.id, cancelled_tasks=cancelled
        )

    if inbound_event.event_type == "hardship_requested":
        if await _has_processed_marker(session, inbound_event):
            return PlannerResult(inbound_event_id=inbound_event.id)
        await _audit(
            session,
            inbound_event,
            correlation_id,
            event_type="hardship_escalation_required",
            entity_type="account",
            entity_id=str(account.id),
            payload={"account_external_id": account.external_id},
        )
        cancelled = await _cancel_scheduled_tasks(
            session,
            inbound_event=inbound_event,
            customer=customer,
            account=account,
            correlation_id=correlation_id,
        )
        await _append_processed_marker(session, inbound_event, correlation_id)
        return PlannerResult(
            inbound_event_id=inbound_event.id, cancelled_tasks=cancelled
        )

    if inbound_event.event_type == "account_paused":
        if await _has_processed_marker(session, inbound_event):
            return PlannerResult(inbound_event_id=inbound_event.id)
        account.status = AccountStatus.PAUSED
        await _audit(
            session,
            inbound_event,
            correlation_id,
            event_type="account_paused",
            entity_type="account",
            entity_id=str(account.id),
            payload={"account_external_id": account.external_id},
        )
        cancelled = await _cancel_scheduled_tasks(
            session,
            inbound_event=inbound_event,
            customer=customer,
            account=account,
            correlation_id=correlation_id,
        )
        await _append_processed_marker(session, inbound_event, correlation_id)
        return PlannerResult(
            inbound_event_id=inbound_event.id, cancelled_tasks=cancelled
        )

    raise ValueError(f"Unsupported inbound event type: {inbound_event.event_type}")


async def _plan_policy_event(
    session: AsyncSession,
    *,
    inbound_event: InboundEvent,
    customer: Customer,
    account: Account,
    correlation_id: UUID,
    now: datetime,
    frequency_cap: int,
) -> PlannerResult:
    existing_decision = await session.scalar(
        select(PolicyDecision.id).where(
            PolicyDecision.inbound_event_id == inbound_event.id
        )
    )
    if existing_decision is not None:
        return PlannerResult(inbound_event_id=inbound_event.id)

    recent_count = await _recent_attempt_count(session, customer=customer, now=now)
    created_tasks = 0
    policy_decisions = 0
    blocked_attempts = 0
    deferred_attempts = 0

    for proposal in _proposals_for_event(
        inbound_event.event_type, now, customer.timezone
    ):
        policy_result = evaluate_policy(
            PolicyInput(
                channel=proposal.channel,
                scheduled_at=proposal.scheduled_at,
                customer_timezone=customer.timezone,
                sms_consent=customer.sms_consent,
                call_consent=customer.call_consent,
                email_consent=customer.email_consent,
                opted_out=customer.opted_out,
                account_status=account.status,
                recent_outbound_attempt_count=recent_count,
                frequency_cap=frequency_cap,
            )
        )
        decision = PolicyDecision(
            account=account,
            customer=customer,
            inbound_event=inbound_event,
            decision=policy_result.decision,
            channel=policy_result.channel,
            reasons=policy_result.reasons,
        )
        session.add(decision)
        await session.flush()
        policy_decisions += 1
        await _audit(
            session,
            inbound_event,
            correlation_id,
            event_type="policy_decision_recorded",
            entity_type="policy_decision",
            entity_id=str(decision.id),
            payload={
                "decision": policy_result.decision.value,
                "channel": policy_result.channel.value,
                "reasons": policy_result.reasons,
            },
        )

        if policy_result.decision == PolicyDecisionOutcome.BLOCK:
            blocked_attempts += 1
            await _audit(
                session,
                inbound_event,
                correlation_id,
                event_type="outreach_blocked",
                entity_type="account",
                entity_id=str(account.id),
                payload={
                    "channel": policy_result.channel.value,
                    "reasons": policy_result.reasons,
                },
            )
            continue

        scheduled_at = proposal.scheduled_at
        if policy_result.decision == PolicyDecisionOutcome.DEFER:
            deferred_attempts += 1
            await _audit(
                session,
                inbound_event,
                correlation_id,
                event_type="outreach_deferred",
                entity_type="account",
                entity_id=str(account.id),
                payload={
                    "channel": policy_result.channel.value,
                    "reasons": policy_result.reasons,
                    "defer_until": policy_result.defer_until.isoformat()
                    if policy_result.defer_until
                    else None,
                },
            )
            if (
                "frequency_cap_exceeded" in policy_result.reasons
                or policy_result.defer_until is None
            ):
                continue
            scheduled_at = policy_result.defer_until

        session.add(
            OutreachTask(
                account=account,
                customer=customer,
                channel=policy_result.channel,
                status=OutreachTaskStatus.SCHEDULED,
                scheduled_at=scheduled_at,
                idempotency_key=f"outreach_task:{inbound_event.id}:{policy_result.channel.value}",
                policy_decision=decision,
            )
        )
        await session.flush()
        created_tasks += 1
        recent_count += 1
        await _audit(
            session,
            inbound_event,
            correlation_id,
            event_type="outreach_task_scheduled",
            entity_type="account",
            entity_id=str(account.id),
            payload={
                "channel": policy_result.channel.value,
                "scheduled_at": scheduled_at.isoformat(),
            },
        )

    return PlannerResult(
        inbound_event_id=inbound_event.id,
        created_tasks=created_tasks,
        policy_decisions=policy_decisions,
        blocked_attempts=blocked_attempts,
        deferred_attempts=deferred_attempts,
    )


async def _handle_cancellation_event(
    session: AsyncSession,
    *,
    inbound_event: InboundEvent,
    customer: Customer,
    account: Account,
    correlation_id: UUID,
) -> PlannerResult:
    if await _has_processed_marker(session, inbound_event):
        return PlannerResult(inbound_event_id=inbound_event.id)
    cancelled = await _cancel_scheduled_tasks(
        session,
        inbound_event=inbound_event,
        customer=customer,
        account=account,
        correlation_id=correlation_id,
    )
    await _append_processed_marker(session, inbound_event, correlation_id)
    return PlannerResult(inbound_event_id=inbound_event.id, cancelled_tasks=cancelled)


async def _cancel_scheduled_tasks(
    session: AsyncSession,
    *,
    inbound_event: InboundEvent,
    customer: Customer,
    account: Account,
    correlation_id: UUID,
) -> int:
    tasks = (
        await session.scalars(
            select(OutreachTask).where(
                OutreachTask.account_id == account.id,
                OutreachTask.customer_id == customer.id,
                OutreachTask.status.in_(_SCHEDULED_STATUSES),
            )
        )
    ).all()
    for task in tasks:
        task.status = OutreachTaskStatus.CANCELLED
        await _audit(
            session,
            inbound_event,
            correlation_id,
            event_type="outreach_cancelled",
            entity_type="outreach_task",
            entity_id=str(task.id),
            payload={
                "channel": task.channel.value,
                "account_external_id": account.external_id,
            },
        )
    await session.flush()
    return len(tasks)


async def _recent_attempt_count(
    session: AsyncSession, *, customer: Customer, now: datetime
) -> int:
    since = now - timedelta(hours=24)
    tasks = (
        await session.scalars(
            select(OutreachTask).where(
                OutreachTask.customer_id == customer.id,
                OutreachTask.status.in_(_RECENT_ATTEMPT_STATUSES),
                OutreachTask.created_at >= since,
            )
        )
    ).all()
    return len(tasks)


def _proposals_for_event(
    event_type: str, now: datetime, customer_timezone: str
) -> list[ProposedOutreach]:
    if event_type == "account_delinquent":
        return [
            ProposedOutreach(OutreachChannel.EMAIL, now),
            ProposedOutreach(OutreachChannel.SMS, now + timedelta(minutes=30)),
            ProposedOutreach(
                OutreachChannel.CALL,
                _next_business_day_at_10(now, customer_timezone),
            ),
        ]
    if event_type == "payment_failed":
        return [
            ProposedOutreach(OutreachChannel.EMAIL, now),
            ProposedOutreach(OutreachChannel.SMS, now + timedelta(minutes=15)),
        ]
    raise ValueError(f"Unsupported policy event type: {event_type}")


def _next_business_day_at_10(now: datetime, customer_timezone: str) -> datetime:
    local_now = now.astimezone(ZoneInfo(customer_timezone))
    candidate = local_now.date() + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return datetime.combine(
        candidate,
        time(hour=10),
        tzinfo=ZoneInfo(customer_timezone),
    )


async def _load_customer_and_account(
    session: AsyncSession, inbound_event: InboundEvent
) -> tuple[Customer, Account]:
    customer = await session.scalar(
        select(Customer).where(
            Customer.external_id == inbound_event.customer_external_id
        )
    )
    if customer is None:
        raise ValueError(
            f"Customer not found for inbound event: {inbound_event.customer_external_id}"
        )
    account = await session.scalar(
        select(Account).where(Account.external_id == inbound_event.account_external_id)
    )
    if account is None:
        raise ValueError(
            f"Account not found for inbound event: {inbound_event.account_external_id}"
        )
    if account.customer_id != customer.id:
        raise ValueError("Inbound event customer/account do not match")
    return customer, account


async def _has_processed_marker(
    session: AsyncSession, inbound_event: InboundEvent
) -> bool:
    event_type = _PROCESSED_MARKERS[inbound_event.event_type]
    marker = await session.scalar(
        select(AuditEvent.id).where(
            AuditEvent.entity_type == "inbound_event",
            AuditEvent.entity_id == str(inbound_event.id),
            AuditEvent.event_type == event_type,
        )
    )
    return marker is not None


async def _append_processed_marker(
    session: AsyncSession, inbound_event: InboundEvent, correlation_id: UUID
) -> None:
    event_type = _PROCESSED_MARKERS[inbound_event.event_type]
    await append_audit_event(
        session,
        entity_type="inbound_event",
        entity_id=str(inbound_event.id),
        event_type=event_type,
        actor_type=AuditActorType.SYSTEM,
        actor_id="outreach-planner",
        correlation_id=correlation_id,
        payload={
            "marker": event_type,
            "inbound_event_type": inbound_event.event_type,
        },
    )


async def _audit(
    session: AsyncSession,
    inbound_event: InboundEvent,
    correlation_id: UUID,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, object],
) -> None:
    await append_audit_event(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        actor_type=AuditActorType.SYSTEM,
        actor_id="outreach-planner",
        correlation_id=correlation_id,
        payload={
            **payload,
            "inbound_event_id": str(inbound_event.id),
            "inbound_event_type": inbound_event.event_type,
        },
    )


def _aware_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now
