from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.models import (
    Account,
    AccountStatus,
    AuditEvent,
    Base,
    Customer,
    InboundEvent,
    OutreachChannel,
    OutreachTask,
    OutreachTaskStatus,
    PolicyDecision,
    PolicyDecisionOutcome,
)
from orchestrator.orchestration.planner import plan_outreach_for_event

NOW = datetime(2026, 5, 28, 14, 0, tzinfo=UTC)


def db_datetime(value: datetime) -> datetime:
    """SQLite test DB returns DateTime(timezone=True) values without tzinfo."""
    return value.replace(tzinfo=None)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db_session:
        yield db_session

    await engine.dispose()


async def seed_event(
    session: AsyncSession,
    event_type: str,
    *,
    customer: Customer | None = None,
    account: Account | None = None,
    event_id: str | None = None,
) -> InboundEvent:
    customer = customer or Customer(
        external_id=f"cust_{uuid4()}",
        full_name="Jane Doe",
        timezone="America/New_York",
        phone_number="+14155550100",
        email="jane@example.com",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    account = account or Account(
        external_id=f"acct_{uuid4()}",
        customer=customer,
        status=AccountStatus.DELINQUENT,
        balance_cents=12500,
        days_past_due=14,
    )
    external_id = event_id or f"event_{uuid4()}"
    event = InboundEvent(
        source="core_banking_demo",
        external_id=external_id,
        event_type=event_type,
        customer_external_id=customer.external_id,
        account_external_id=account.external_id,
        payload={},
        idempotency_key=f"inbound_event:core_banking_demo:{external_id}",
    )
    session.add_all([customer, account, event])
    await session.commit()
    return event


async def fetch_tasks(session: AsyncSession) -> list[OutreachTask]:
    return list((await session.scalars(select(OutreachTask))).all())


async def fetch_decisions(session: AsyncSession) -> list[PolicyDecision]:
    return list((await session.scalars(select(PolicyDecision))).all())


async def audit_types(session: AsyncSession) -> list[str]:
    return list((await session.scalars(select(AuditEvent.event_type))).all())


async def audit_type_counts(session: AsyncSession) -> Counter[str]:
    return Counter(await audit_types(session))


async def test_account_delinquent_creates_email_sms_and_call_when_policy_allows(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "account_delinquent")

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    tasks = await fetch_tasks(session)
    decisions = await fetch_decisions(session)
    by_channel = {task.channel: task for task in tasks}
    assert result.created_tasks == 3
    assert result.policy_decisions == 3
    assert len(tasks) == 3
    assert len(decisions) == 3
    assert {decision.decision for decision in decisions} == {
        PolicyDecisionOutcome.ALLOW
    }
    assert by_channel[OutreachChannel.EMAIL].scheduled_at == db_datetime(NOW)
    assert by_channel[OutreachChannel.SMS].scheduled_at == db_datetime(
        NOW + timedelta(minutes=30)
    )
    assert by_channel[OutreachChannel.CALL].scheduled_at == db_datetime(
        datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    counts = await audit_type_counts(session)
    assert counts["policy_decision_recorded"] == 3
    assert counts["outreach_task_scheduled"] == 3
    assert sum(counts.values()) == 6


async def test_payment_failed_creates_email_and_sms_when_policy_allows(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "payment_failed")

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    tasks = await fetch_tasks(session)
    assert result.created_tasks == 2
    assert {task.channel for task in tasks} == {
        OutreachChannel.EMAIL,
        OutreachChannel.SMS,
    }
    assert {task.scheduled_at for task in tasks} == {
        db_datetime(NOW),
        db_datetime(NOW + timedelta(minutes=15)),
    }


async def test_missing_sms_consent_records_block_decision_without_sms_task(
    session: AsyncSession,
) -> None:
    customer = Customer(
        external_id="cust_no_sms",
        full_name="Jane Doe",
        timezone="America/New_York",
        email="jane@example.com",
        sms_consent=False,
        call_consent=True,
        email_consent=True,
    )
    event = await seed_event(session, "payment_failed", customer=customer)

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    decisions = await fetch_decisions(session)
    tasks = await fetch_tasks(session)
    sms_decision = next(d for d in decisions if d.channel == OutreachChannel.SMS)
    assert result.created_tasks == 1
    assert result.blocked_attempts == 1
    assert sms_decision.decision == PolicyDecisionOutcome.BLOCK
    assert sms_decision.reasons == ["missing_sms_consent"]
    assert {task.channel for task in tasks} == {OutreachChannel.EMAIL}
    assert "outreach_blocked" in await audit_types(session)


async def test_quiet_hours_sms_and_call_create_deferred_tasks(
    session: AsyncSession,
) -> None:
    early_now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)  # 08:00 New York
    event = await seed_event(session, "account_delinquent")

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=early_now
    )
    await session.commit()

    decisions = await fetch_decisions(session)
    tasks = await fetch_tasks(session)
    deferred_channels = {
        decision.channel
        for decision in decisions
        if decision.decision == PolicyDecisionOutcome.DEFER
    }
    assert result.deferred_attempts == 1
    assert deferred_channels == {OutreachChannel.SMS}
    assert next(
        task for task in tasks if task.channel == OutreachChannel.SMS
    ).scheduled_at == db_datetime(
        datetime(2026, 5, 28, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    assert next(
        task for task in tasks if task.channel == OutreachChannel.CALL
    ).scheduled_at == db_datetime(
        datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    assert "outreach_deferred" in await audit_types(session)


async def test_frequency_cap_prevents_scheduling_beyond_cap_in_same_run(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "account_delinquent")

    result = await plan_outreach_for_event(
        session,
        inbound_event_id=event.id,
        correlation_id=uuid4(),
        now=NOW,
        frequency_cap=2,
    )
    await session.commit()

    tasks = await fetch_tasks(session)
    decisions = await fetch_decisions(session)
    assert result.created_tasks == 2
    assert result.deferred_attempts == 1
    assert len(tasks) == 2
    assert len(decisions) == 3
    assert any(decision.reasons == ["frequency_cap_exceeded"] for decision in decisions)


async def test_frequency_cap_prevents_quiet_hours_deferred_task_when_at_cap(
    session: AsyncSession,
) -> None:
    early_now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)  # 08:00 New York
    customer = Customer(
        external_id="cust_quiet_frequency_cap",
        full_name="Jane Doe",
        timezone="America/New_York",
        phone_number="+141****0100",
        email="jane@example.com",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    account = Account(
        external_id="acct_quiet_frequency_cap",
        customer=customer,
        status=AccountStatus.DELINQUENT,
    )
    session.add(
        OutreachTask(
            account=account,
            customer=customer,
            channel=OutreachChannel.EMAIL,
            correlation_id=uuid4(),
            status=OutreachTaskStatus.SENT,
            scheduled_at=early_now - timedelta(hours=1),
            idempotency_key="existing:quiet-frequency-cap",
            created_at=early_now - timedelta(hours=1),
        )
    )
    event = await seed_event(
        session, "payment_failed", customer=customer, account=account
    )

    result = await plan_outreach_for_event(
        session,
        inbound_event_id=event.id,
        correlation_id=uuid4(),
        now=early_now,
        frequency_cap=1,
    )
    await session.commit()

    tasks = await fetch_tasks(session)
    decisions = await fetch_decisions(session)
    sms_decision = next(
        decision for decision in decisions if decision.channel == OutreachChannel.SMS
    )
    counts = await audit_type_counts(session)
    assert result.created_tasks == 0
    assert result.deferred_attempts == 2
    assert len(tasks) == 1
    assert sms_decision.decision == PolicyDecisionOutcome.DEFER
    assert sms_decision.reasons == ["quiet_hours", "frequency_cap_exceeded"]
    assert counts["outreach_deferred"] == 2
    assert counts["outreach_task_scheduled"] == 0


async def test_opt_out_received_marks_customer_opted_out_and_cancels_scheduled_tasks(
    session: AsyncSession,
) -> None:
    customer = Customer(
        external_id="cust_opt_out",
        full_name="Jane Doe",
        timezone="America/New_York",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    account = Account(
        external_id="acct_opt_out",
        customer=customer,
        status=AccountStatus.DELINQUENT,
    )
    session.add(
        OutreachTask(
            account=account,
            customer=customer,
            channel=OutreachChannel.EMAIL,
            correlation_id=uuid4(),
            status=OutreachTaskStatus.SCHEDULED,
            scheduled_at=NOW,
            idempotency_key="existing:email",
        )
    )
    event = await seed_event(
        session, "opt_out_received", customer=customer, account=account
    )

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    assert customer.opted_out is True
    assert result.cancelled_tasks == 1
    assert (await fetch_tasks(session))[0].status == OutreachTaskStatus.CANCELLED
    counts = await audit_type_counts(session)
    assert counts["customer_opted_out"] == 1
    assert counts["outreach_cancelled"] == 1
    assert counts["planner_opt_out_processed"] == 1
    assert sum(counts.values()) == 3


async def test_payment_received_cancels_scheduled_tasks(session: AsyncSession) -> None:
    event = await seed_event(session, "payment_received")
    account = await session.scalar(
        select(Account).where(Account.external_id == event.account_external_id)
    )
    assert account is not None
    task = OutreachTask(
        account=account,
        customer=account.customer,
        channel=OutreachChannel.SMS,
        correlation_id=uuid4(),
        status=OutreachTaskStatus.SCHEDULED,
        scheduled_at=NOW,
        idempotency_key="existing:sms",
    )
    session.add(task)
    await session.commit()

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()
    retry = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    assert result.cancelled_tasks == 1
    assert retry.cancelled_tasks == 0
    assert task.status == OutreachTaskStatus.CANCELLED
    counts = await audit_type_counts(session)
    assert counts["outreach_cancelled"] == 1
    assert counts["planner_payment_received_processed"] == 1
    assert sum(counts.values()) == 2


async def test_payment_received_with_no_tasks_is_idempotent(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "payment_received")
    account = await session.scalar(
        select(Account).where(Account.external_id == event.account_external_id)
    )
    assert account is not None

    first = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    task = OutreachTask(
        account=account,
        customer=account.customer,
        channel=OutreachChannel.SMS,
        correlation_id=uuid4(),
        status=OutreachTaskStatus.SCHEDULED,
        scheduled_at=NOW,
        idempotency_key="created-after-payment:sms",
    )
    session.add(task)
    await session.commit()

    second = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    counts = await audit_type_counts(session)
    assert first.cancelled_tasks == 0
    assert second.cancelled_tasks == 0
    assert task.status == OutreachTaskStatus.SCHEDULED
    assert counts["planner_payment_received_processed"] == 1
    assert counts["outreach_cancelled"] == 0
    assert sum(counts.values()) == 1


async def test_hardship_requested_cancels_tasks_and_appends_escalation_audit(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "hardship_requested")
    account = await session.scalar(
        select(Account).where(Account.external_id == event.account_external_id)
    )
    assert account is not None
    session.add(
        OutreachTask(
            account=account,
            customer=account.customer,
            channel=OutreachChannel.CALL,
            correlation_id=uuid4(),
            status=OutreachTaskStatus.SCHEDULED,
            scheduled_at=NOW,
            idempotency_key="existing:call",
        )
    )
    await session.commit()

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    assert result.cancelled_tasks == 1
    counts = await audit_type_counts(session)
    assert counts["hardship_escalation_required"] == 1
    assert counts["outreach_cancelled"] == 1
    assert counts["planner_hardship_processed"] == 1
    assert sum(counts.values()) == 3


async def test_account_paused_marks_account_paused_and_cancels_tasks(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "account_paused")
    account = await session.scalar(
        select(Account).where(Account.external_id == event.account_external_id)
    )
    assert account is not None
    session.add(
        OutreachTask(
            account=account,
            customer=account.customer,
            channel=OutreachChannel.EMAIL,
            correlation_id=uuid4(),
            status=OutreachTaskStatus.SCHEDULED,
            scheduled_at=NOW,
            idempotency_key="existing:paused",
        )
    )
    await session.commit()

    result = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    assert account.status == AccountStatus.PAUSED
    assert result.cancelled_tasks == 1
    counts = await audit_type_counts(session)
    assert counts["account_paused"] == 1
    assert counts["outreach_cancelled"] == 1
    assert counts["planner_account_paused_processed"] == 1
    assert sum(counts.values()) == 3


async def test_persisted_frequency_cap_counts_only_recent_attempt_statuses(
    session: AsyncSession,
) -> None:
    customer = Customer(
        external_id="cust_frequency_cap",
        full_name="Jane Doe",
        timezone="America/New_York",
        phone_number="+141****0100",
        email="jane@example.com",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    account = Account(
        external_id="acct_frequency_cap",
        customer=customer,
        status=AccountStatus.DELINQUENT,
    )
    statuses = [
        OutreachTaskStatus.SCHEDULED,
        OutreachTaskStatus.DISPATCHING,
        OutreachTaskStatus.SENT,
        OutreachTaskStatus.FAILED,
        OutreachTaskStatus.CANCELLED,
        OutreachTaskStatus.BLOCKED,
    ]
    for status in statuses:
        session.add(
            OutreachTask(
                account=account,
                customer=customer,
                channel=OutreachChannel.EMAIL,
                correlation_id=uuid4(),
                status=status,
                scheduled_at=NOW,
                idempotency_key=f"recent:{status.value}",
                created_at=NOW - timedelta(hours=1),
            )
        )
    session.add(
        OutreachTask(
            account=account,
            customer=customer,
            channel=OutreachChannel.EMAIL,
            correlation_id=uuid4(),
            status=OutreachTaskStatus.SCHEDULED,
            scheduled_at=NOW,
            idempotency_key="old:scheduled",
            created_at=NOW - timedelta(hours=25),
        )
    )
    event = await seed_event(
        session, "payment_failed", customer=customer, account=account
    )

    result = await plan_outreach_for_event(
        session,
        inbound_event_id=event.id,
        correlation_id=uuid4(),
        now=NOW,
        frequency_cap=4,
    )
    await session.commit()

    decisions = await fetch_decisions(session)
    tasks = await fetch_tasks(session)
    assert result.created_tasks == 0
    assert result.deferred_attempts == 2
    assert len(tasks) == 7
    assert len(decisions) == 2
    assert all(
        decision.decision == PolicyDecisionOutcome.DEFER for decision in decisions
    )
    assert all(decision.reasons == ["frequency_cap_exceeded"] for decision in decisions)


async def test_planner_is_idempotent_for_same_inbound_event(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "account_delinquent")

    first = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()
    second = await plan_outreach_for_event(
        session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
    )
    await session.commit()

    assert first.created_tasks == 3
    assert second.created_tasks == 0
    assert await session.scalar(select(func.count()).select_from(OutreachTask)) == 3
    assert await session.scalar(select(func.count()).select_from(PolicyDecision)) == 3
    counts = await audit_type_counts(session)
    assert counts["policy_decision_recorded"] == 3
    assert counts["outreach_task_scheduled"] == 3
    assert sum(counts.values()) == 6


async def test_unsupported_event_type_raises_clear_value_error(
    session: AsyncSession,
) -> None:
    event = await seed_event(session, "unknown_event")

    with pytest.raises(
        ValueError, match="Unsupported inbound event type: unknown_event"
    ):
        await plan_outreach_for_event(
            session, inbound_event_id=event.id, correlation_id=uuid4(), now=NOW
        )
