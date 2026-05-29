from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db import get_session
from orchestrator.domain.operational_outreach import (
    ALLOWED_MANUAL_DELIVERY_SOURCE_STATUSES,
    TaskStateConflictError,
    apply_task_filters,
    get_outreach_task,
    record_manual_delivery_result,
    task_statement,
)
from orchestrator.models import OutreachChannel, OutreachTask, OutreachTaskStatus

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


class PolicyDecisionContextResponse(BaseModel):
    id: UUID
    decision: str
    channel: str | None
    reasons: list[str]


class OutreachTaskResponse(BaseModel):
    id: UUID
    account_external_id: str
    customer_external_id: str
    channel: OutreachChannel
    status: OutreachTaskStatus
    correlation_id: UUID
    scheduled_at: datetime
    attempt_count: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    policy_decision: PolicyDecisionContextResponse | None = None


class DeliveryResultRequest(BaseModel):
    status: Literal["sent", "failed"]
    provider_message_id: Annotated[str | None, Field(max_length=255)] = None
    details: dict[str, Any] = Field(default_factory=dict)


class DeliveryResultResponse(OutreachTaskResponse):
    pass


def task_response(task: OutreachTask) -> OutreachTaskResponse:
    policy_decision = None
    if task.policy_decision is not None:
        policy_decision = PolicyDecisionContextResponse(
            id=task.policy_decision.id,
            decision=task.policy_decision.decision.value,
            channel=task.policy_decision.channel.value
            if task.policy_decision.channel is not None
            else None,
            reasons=task.policy_decision.reasons,
        )
    return OutreachTaskResponse(
        id=task.id,
        account_external_id=task.account.external_id,
        customer_external_id=task.customer.external_id,
        channel=task.channel,
        status=task.status,
        correlation_id=task.correlation_id,
        scheduled_at=task.scheduled_at,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        last_error=task.last_error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        policy_decision=policy_decision,
    )


@router.get("", response_model=list[OutreachTaskResponse])
async def list_tasks(
    status_filter: Annotated[OutreachTaskStatus | None, Query(alias="status")] = None,
    customer_external_id: str | None = None,
    account_external_id: str | None = None,
    channel: OutreachChannel | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    session: AsyncSession = Depends(get_session),
) -> list[OutreachTaskResponse]:
    statement = apply_task_filters(
        task_statement(),
        status=status_filter,
        customer_external_id=customer_external_id,
        account_external_id=account_external_id,
        channel=channel,
    ).order_by(OutreachTask.created_at, OutreachTask.id)
    statement = statement.limit(limit)
    tasks = (await session.scalars(statement)).all()
    return [task_response(task) for task in tasks]


@router.get("/{task_id}", response_model=OutreachTaskResponse)
async def get_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> OutreachTaskResponse:
    task = await get_outreach_task(session, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return task_response(task)


@router.post("/{task_id}/delivery-result", response_model=DeliveryResultResponse)
async def record_delivery_result(
    task_id: UUID,
    body: DeliveryResultRequest,
    session: AsyncSession = Depends(get_session),
) -> DeliveryResultResponse:
    task = await get_outreach_task(session, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    if task.status is OutreachTaskStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelled tasks cannot be manually marked delivered",
        )
    if task.status not in ALLOWED_MANUAL_DELIVERY_SOURCE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task status does not allow manual delivery result",
        )

    try:
        updated_task = await record_manual_delivery_result(
            session,
            task=task,
            status=body.status,
            provider_message_id=body.provider_message_id,
            details=body.details,
        )
    except TaskStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task status changed before manual delivery result was recorded",
        ) from exc
    return DeliveryResultResponse(**task_response(updated_task).model_dump())
