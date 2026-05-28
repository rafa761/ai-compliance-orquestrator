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
    """Audit row exposed for demo inspection and trace reconstruction.

    `payload` is contextual evidence for the recorded event, not a stable public
    business schema. Treat audit rows as append-only facts rather than mutable
    state projections.
    """

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
    """Return audit evidence in deterministic order for replay/debugging.

    Filtering by correlation_id shows the rows caused by one request or workflow,
    which is the main way to demonstrate why the service accepted, blocked,
    deferred, or cancelled outreach.
    """

    statement = select(AuditEvent)
    if correlation_id is not None:
        statement = statement.where(AuditEvent.correlation_id == correlation_id)
    statement = statement.order_by(AuditEvent.created_at, AuditEvent.id)
    return list(await session.scalars(statement))
