from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orchestrator.domain.audit_log import append_audit_event
from orchestrator.models import (
    Account,
    AuditActorType,
    OutreachChannel,
    OutreachTask,
    OutreachTaskStatus,
)

DeliveryStatus = Literal["sent", "failed"]
ALLOWED_MANUAL_DELIVERY_SOURCE_STATUSES = frozenset(
    {
        OutreachTaskStatus.SCHEDULED,
        OutreachTaskStatus.DISPATCHING,
        OutreachTaskStatus.FAILED,
    }
)


class TaskStateConflictError(RuntimeError):
    """Raised when a task changed state before an operational update claimed it."""


async def get_outreach_task(
    session: AsyncSession, task_id: UUID
) -> OutreachTask | None:
    return await session.scalar(
        select(OutreachTask)
        .where(OutreachTask.id == task_id)
        .options(
            selectinload(OutreachTask.account),
            selectinload(OutreachTask.customer),
            selectinload(OutreachTask.policy_decision),
        )
    )


async def record_manual_delivery_result(
    session: AsyncSession,
    *,
    task: OutreachTask,
    status: DeliveryStatus,
    provider_message_id: str | None,
    details: dict[str, Any],
) -> OutreachTask:
    previous_status = task.status
    if previous_status not in ALLOWED_MANUAL_DELIVERY_SOURCE_STATUSES:
        raise TaskStateConflictError(
            "Task status does not allow manual delivery result"
        )

    new_status = OutreachTaskStatus(status)
    if new_status is OutreachTaskStatus.SENT:
        last_error = None
    else:
        error = details.get("error")
        last_error = (
            error if isinstance(error, str) else "manual delivery result failed"
        )

    result = await session.execute(
        update(OutreachTask)
        .where(OutreachTask.id == task.id)
        .where(OutreachTask.status == previous_status)
        .values(status=new_status, last_error=last_error)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 0) != 1:
        await session.rollback()
        raise TaskStateConflictError(
            "Task status changed before delivery result update"
        )

    await append_audit_event(
        session,
        entity_type="outreach_task",
        entity_id=str(task.id),
        event_type="delivery_result_recorded",
        actor_type=AuditActorType.API_CLIENT,
        actor_id="operational-api",
        correlation_id=task.correlation_id,
        payload={
            "provider_message_id": provider_message_id,
            "details": details,
            "previous_status": previous_status.value,
            "new_status": new_status.value,
        },
    )
    task_id = task.id
    await session.commit()
    session.expire(task)
    updated_task = await get_outreach_task(session, task_id)
    if updated_task is None:  # pragma: no cover - task was just persisted.
        raise RuntimeError("task disappeared after manual delivery result")
    return updated_task


async def cancel_scheduled_account_outreach(
    session: AsyncSession,
    *,
    account: Account,
    reason: str,
    correlation_id: UUID,
) -> int:
    tasks = list(
        (
            await session.scalars(
                select(OutreachTask)
                .where(OutreachTask.account_id == account.id)
                .where(OutreachTask.status == OutreachTaskStatus.SCHEDULED)
                .options(selectinload(OutreachTask.account))
                .order_by(OutreachTask.created_at, OutreachTask.id)
            )
        ).all()
    )
    cancelled_count = 0
    for task in tasks:
        result = await session.execute(
            update(OutreachTask)
            .where(OutreachTask.id == task.id)
            .where(OutreachTask.status == OutreachTaskStatus.SCHEDULED)
            .values(status=OutreachTaskStatus.CANCELLED)
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            continue

        cancelled_count += 1
        await append_audit_event(
            session,
            entity_type="outreach_task",
            entity_id=str(task.id),
            event_type="outreach_cancelled",
            actor_type=AuditActorType.API_CLIENT,
            actor_id="operational-api",
            correlation_id=correlation_id,
            payload={
                "reason": reason,
                "account_external_id": account.external_id,
                "channel": task.channel.value,
                "cancelled_by": "operational_api",
            },
        )
    await session.commit()
    return cancelled_count


def task_statement():
    return select(OutreachTask).options(
        selectinload(OutreachTask.account),
        selectinload(OutreachTask.customer),
        selectinload(OutreachTask.policy_decision),
    )


async def find_account_by_external_id(
    session: AsyncSession, external_id: str
) -> Account | None:
    return await session.scalar(
        select(Account).where(Account.external_id == external_id)
    )


def apply_task_filters(
    statement,
    *,
    status: OutreachTaskStatus | None = None,
    customer_external_id: str | None = None,
    account_external_id: str | None = None,
    channel: OutreachChannel | None = None,
):
    if status is not None:
        statement = statement.where(OutreachTask.status == status)
    if channel is not None:
        statement = statement.where(OutreachTask.channel == channel)
    if customer_external_id is not None:
        statement = statement.join(OutreachTask.customer).where(
            OutreachTask.customer.has(external_id=customer_external_id)
        )
    if account_external_id is not None:
        statement = statement.join(OutreachTask.account).where(
            OutreachTask.account.has(external_id=account_external_id)
        )
    return statement
