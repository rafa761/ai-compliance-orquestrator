from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db import get_session
from orchestrator.models import AuditActorType, AuditEvent

router = APIRouter(prefix="/v1/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: str
    event_type: str
    actor_type: AuditActorType
    actor_id: str | None
    correlation_id: UUID
    payload: dict[str, Any]
    created_at: datetime


@router.get("", response_model=list[AuditEventResponse])
async def list_audit_events(
    correlation_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AuditEvent]:
    statement = select(AuditEvent)
    if correlation_id is not None:
        statement = statement.where(AuditEvent.correlation_id == correlation_id)
    statement = statement.order_by(AuditEvent.created_at, AuditEvent.id)
    return list(await session.scalars(statement))
