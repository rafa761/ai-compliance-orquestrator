from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.channels.base import DeliveryResult
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
)
from orchestrator.orchestration.planner import plan_outreach_for_event
from orchestrator.workers.dispatch import dispatch_due_tasks


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


def db_datetime(value: datetime) -> datetime:
    """SQLite test DB returns DateTime(timezone=True) values without tzinfo."""

    return value.replace(tzinfo=None)


@dataclass
class RecordingAdapter:
    calls: list[UUID] = field(default_factory=list)
    fail: bool = False

    async def send(self, task: OutreachTask) -> DeliveryResult:
        self.calls.append(task.id)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return DeliveryResult(
            provider_message_id=f"test-provider:{task.id}",
            details={"test": True},
        )


async def _seed_account(session: AsyncSession) -> tuple[Customer, Account]:
    customer = Customer(
        external_id=f"cust_{uuid4()}",
        full_name="Jane Doe",
        timezone="America/New_York",
        phone_number="+14155550100",
        email="jane@example.com",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    account = Account(
        external_id=f"acct_{uuid4()}",
        customer=customer,
        status=AccountStatus.DELINQUENT,
        balance_cents=12500,
        days_past_due=14,
    )
    session.add_all([customer, account])
    await session.commit()
    return customer, account


async def _seed_task(
    session: AsyncSession,
    *,
    now: datetime,
    channel: OutreachChannel = OutreachChannel.SMS,
    status: OutreachTaskStatus = OutreachTaskStatus.SCHEDULED,
    scheduled_at: datetime | None = None,
    attempt_count: int = 0,
    max_attempts: int = 3,
    correlation_id: UUID | None = None,
) -> OutreachTask:
    customer, account = await _seed_account(session)
    task = OutreachTask(
        account=account,
        customer=customer,
        channel=channel,
        status=status,
        scheduled_at=scheduled_at or now,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        idempotency_key=f"task:{uuid4()}",
        correlation_id=correlation_id or uuid4(),
    )
    session.add(task)
    await session.commit()
    return task


async def _audit_events(session: AsyncSession, task: OutreachTask) -> list[AuditEvent]:
    return list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_type == "outreach_task")
                .where(AuditEvent.entity_id == str(task.id))
                .order_by(AuditEvent.created_at, AuditEvent.event_type)
            )
        ).all()
    )


async def test_sends_due_task_and_records_audit_with_task_correlation_id(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    correlation_id = uuid4()
    task = await _seed_task(session, now=now, correlation_id=correlation_id)
    adapter = RecordingAdapter()

    dispatched = await dispatch_due_tasks(
        session,
        now=now,
        adapters={OutreachChannel.SMS: adapter},
        worker_id="worker-a",
    )

    assert dispatched == 1
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.SENT
    assert task.attempt_count == 1
    assert task.last_error is None
    assert adapter.calls == [task.id]

    audit_events = await _audit_events(session, task)
    assert [event.event_type for event in audit_events] == [
        "dispatch_started",
        "dispatch_succeeded",
    ]
    assert {event.correlation_id for event in audit_events} == {correlation_id}
    assert {event.actor_id for event in audit_events} == {"worker-a"}
    assert audit_events[1].payload == {
        "provider_message_id": f"test-provider:{task.id}",
        "details": {"test": True},
    }


async def test_ignores_future_scheduled_task(session: AsyncSession) -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    task = await _seed_task(session, now=now, scheduled_at=now + timedelta(minutes=1))
    adapter = RecordingAdapter()

    dispatched = await dispatch_due_tasks(
        session, now=now, adapters={OutreachChannel.SMS: adapter}
    )

    assert dispatched == 0
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.SCHEDULED
    assert task.attempt_count == 0
    assert adapter.calls == []
    assert await _audit_events(session, task) == []


async def test_failing_adapter_reschedules_when_attempts_remain(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    retry_delay = timedelta(minutes=7)
    task = await _seed_task(session, now=now, attempt_count=1, max_attempts=3)
    adapter = RecordingAdapter(fail=True)

    dispatched = await dispatch_due_tasks(
        session,
        now=now,
        adapters={OutreachChannel.SMS: adapter},
        retry_delay=retry_delay,
    )

    assert dispatched == 1
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.SCHEDULED
    assert task.attempt_count == 2
    assert task.scheduled_at == db_datetime(now + retry_delay)
    assert task.last_error == "provider unavailable"
    assert adapter.calls == [task.id]

    audit_events = await _audit_events(session, task)
    assert [event.event_type for event in audit_events] == [
        "dispatch_failed",
        "dispatch_started",
    ] or [event.event_type for event in audit_events] == [
        "dispatch_started",
        "dispatch_failed",
    ]
    failed_event = next(
        event for event in audit_events if event.event_type == "dispatch_failed"
    )
    assert failed_event.payload == {
        "error": "provider unavailable",
        "will_retry": True,
        "retry_scheduled_at": (now + retry_delay).isoformat(),
        "final_status": OutreachTaskStatus.SCHEDULED.value,
    }


async def test_failing_adapter_marks_failed_at_max_attempts(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    task = await _seed_task(session, now=now, attempt_count=2, max_attempts=3)
    adapter = RecordingAdapter(fail=True)

    dispatched = await dispatch_due_tasks(
        session, now=now, adapters={OutreachChannel.SMS: adapter}
    )

    assert dispatched == 1
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.FAILED
    assert task.attempt_count == 3
    assert task.last_error == "provider unavailable"
    failed_event = next(
        event
        for event in await _audit_events(session, task)
        if event.event_type == "dispatch_failed"
    )
    assert failed_event.payload["will_retry"] is False
    assert failed_event.payload["retry_scheduled_at"] is None
    assert failed_event.payload["final_status"] == OutreachTaskStatus.FAILED.value


async def test_same_task_is_not_sent_twice(session: AsyncSession) -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    task = await _seed_task(session, now=now)
    adapter = RecordingAdapter()

    first_count = await dispatch_due_tasks(
        session, now=now, adapters={OutreachChannel.SMS: adapter}
    )
    second_count = await dispatch_due_tasks(
        session, now=now, adapters={OutreachChannel.SMS: adapter}
    )

    assert first_count == 1
    assert second_count == 0
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.SENT
    assert adapter.calls == [task.id]


async def test_cancelled_task_is_not_dispatched(session: AsyncSession) -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    task = await _seed_task(session, now=now, status=OutreachTaskStatus.CANCELLED)
    adapter = RecordingAdapter()

    dispatched = await dispatch_due_tasks(
        session, now=now, adapters={OutreachChannel.SMS: adapter}
    )

    assert dispatched == 0
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.CANCELLED
    assert adapter.calls == []


async def test_planner_created_tasks_carry_original_correlation_id(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 29, 14, 0, tzinfo=UTC)
    correlation_id = uuid4()
    customer = Customer(
        external_id="cust_planner_dispatch",
        full_name="Planner Customer",
        timezone="America/New_York",
        phone_number="+14155550100",
        email="planner@example.com",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    account = Account(
        external_id="acct_planner_dispatch",
        customer=customer,
        status=AccountStatus.DELINQUENT,
        balance_cents=4200,
        days_past_due=9,
    )
    inbound_event = InboundEvent(
        source="test",
        external_id="event_planner_dispatch",
        event_type="payment_failed",
        customer_external_id=customer.external_id,
        account_external_id=account.external_id,
        payload={},
        idempotency_key="inbound_event:test:event_planner_dispatch",
    )
    session.add_all([customer, account, inbound_event])
    await session.commit()

    result = await plan_outreach_for_event(
        session,
        inbound_event_id=inbound_event.id,
        correlation_id=correlation_id,
        now=now,
    )
    await session.commit()

    assert result.created_tasks > 0
    tasks = (await session.scalars(select(OutreachTask))).all()
    assert tasks
    assert {task.correlation_id for task in tasks} == {correlation_id}
