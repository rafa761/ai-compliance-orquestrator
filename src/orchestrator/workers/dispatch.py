from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.channels.base import ChannelAdapter
from orchestrator.channels.mock_call import MockCallAdapter
from orchestrator.channels.mock_email import MockEmailAdapter
from orchestrator.channels.mock_sms import MockSmsAdapter
from orchestrator.db import async_session
from orchestrator.domain.audit_log import append_audit_event
from orchestrator.models import (
    AuditActorType,
    OutreachChannel,
    OutreachTask,
    OutreachTaskStatus,
)


def default_adapters() -> dict[OutreachChannel, ChannelAdapter]:
    return {
        OutreachChannel.SMS: MockSmsAdapter(),
        OutreachChannel.EMAIL: MockEmailAdapter(),
        OutreachChannel.CALL: MockCallAdapter(),
    }


async def dispatch_due_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 10,
    adapters: Mapping[OutreachChannel, ChannelAdapter] | None = None,
    retry_delay: timedelta = timedelta(minutes=5),
    worker_id: str = "dispatch-worker",
) -> int:
    """Claim and dispatch up to ``limit`` due scheduled outreach tasks.

    A task is claimed with a conditional scheduled -> dispatching update before
    calling its adapter. The claim and dispatch_started audit row are committed
    before provider I/O so the send does not happen inside a long transaction.
    """

    now = _aware_now(now)
    if adapters is None:
        adapters = default_adapters()
    attempted = 0

    task_ids = (
        await session.scalars(
            select(OutreachTask.id)
            .where(OutreachTask.status == OutreachTaskStatus.SCHEDULED)
            .where(OutreachTask.scheduled_at <= now)
            .order_by(OutreachTask.scheduled_at, OutreachTask.created_at)
            .limit(limit)
        )
    ).all()

    for task_id in task_ids:
        task = await session.get(OutreachTask, task_id)
        if task is None:
            continue

        claimed = await session.execute(
            update(OutreachTask)
            .where(OutreachTask.id == task_id)
            .where(OutreachTask.status == OutreachTaskStatus.SCHEDULED)
            .where(OutreachTask.scheduled_at <= now)
            .values(
                status=OutreachTaskStatus.DISPATCHING,
                attempt_count=OutreachTask.attempt_count + 1,
            )
        )
        if claimed.rowcount != 1:
            await session.rollback()
            continue

        await session.refresh(task)
        await append_audit_event(
            session,
            entity_type="outreach_task",
            entity_id=str(task.id),
            event_type="dispatch_started",
            actor_type=AuditActorType.WORKER,
            actor_id=worker_id,
            correlation_id=task.correlation_id,
            payload={
                "channel": task.channel.value,
                "attempt_count": task.attempt_count,
            },
        )
        await session.commit()
        attempted += 1

        try:
            adapter = adapters.get(task.channel)
            if adapter is None:
                raise RuntimeError(
                    f"no adapter configured for channel: {task.channel.value}"
                )
            result = await adapter.send(task)
        except Exception as exc:  # noqa: BLE001 - provider failures become task state.
            await _record_failure(
                session,
                task,
                error=str(exc),
                now=now,
                retry_delay=retry_delay,
                worker_id=worker_id,
            )
        else:
            await _record_success(
                session,
                task,
                provider_message_id=result.provider_message_id,
                details=result.details,
                worker_id=worker_id,
            )

    return attempted


async def _record_success(
    session: AsyncSession,
    task: OutreachTask,
    *,
    provider_message_id: str,
    details: dict[str, object],
    worker_id: str,
) -> None:
    task.status = OutreachTaskStatus.SENT
    task.last_error = None
    await append_audit_event(
        session,
        entity_type="outreach_task",
        entity_id=str(task.id),
        event_type="dispatch_succeeded",
        actor_type=AuditActorType.WORKER,
        actor_id=worker_id,
        correlation_id=task.correlation_id,
        payload={
            "provider_message_id": provider_message_id,
            "details": details,
        },
    )
    await session.commit()


async def _record_failure(
    session: AsyncSession,
    task: OutreachTask,
    *,
    error: str,
    now: datetime,
    retry_delay: timedelta,
    worker_id: str,
) -> None:
    await session.refresh(task)
    will_retry = task.attempt_count < task.max_attempts
    retry_scheduled_at = now + retry_delay if will_retry else None
    task.status = (
        OutreachTaskStatus.SCHEDULED if will_retry else OutreachTaskStatus.FAILED
    )
    task.scheduled_at = retry_scheduled_at or task.scheduled_at
    task.last_error = error
    await append_audit_event(
        session,
        entity_type="outreach_task",
        entity_id=str(task.id),
        event_type="dispatch_failed",
        actor_type=AuditActorType.WORKER,
        actor_id=worker_id,
        correlation_id=task.correlation_id,
        payload={
            "error": error,
            "will_retry": will_retry,
            "retry_scheduled_at": retry_scheduled_at.isoformat()
            if retry_scheduled_at
            else None,
            "final_status": task.status.value,
        },
    )
    await session.commit()


def _aware_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _run_loop(*, once: bool, poll_interval: float) -> None:
    while True:
        async with async_session() as session:
            await dispatch_due_tasks(session)
        if once:
            return
        await asyncio.sleep(poll_interval)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch due outreach tasks.")
    parser.add_argument(
        "--once", action="store_true", help="Run one polling pass and exit."
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds to sleep between polling passes.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run_loop(once=args.once, poll_interval=args.poll_interval))


if __name__ == "__main__":
    main()
