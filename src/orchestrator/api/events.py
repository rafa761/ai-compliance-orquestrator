from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.audit import append_audit_event
from orchestrator.db import get_session
from orchestrator.models import AuditActorType, InboundEvent

router = APIRouter(prefix="/v1/events", tags=["events"])


class InboundEventRequest(BaseModel):
    external_id: str
    event_type: str
    customer_external_id: str
    account_external_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class InboundEventResponse(BaseModel):
    event_id: UUID
    status: str
    correlation_id: UUID


@router.post("", response_model=InboundEventResponse)
async def ingest_event(
    request: Request,
    body: InboundEventRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> InboundEventResponse:
    correlation_id: UUID = request.state.correlation_id
    existing_event = await session.scalar(
        select(InboundEvent).where(InboundEvent.idempotency_key == idempotency_key)
    )
    if existing_event is not None:
        return InboundEventResponse(
            event_id=existing_event.id,
            status="accepted",
            correlation_id=correlation_id,
        )

    inbound_event = InboundEvent(
        external_id=body.external_id,
        event_type=body.event_type,
        customer_external_id=body.customer_external_id,
        account_external_id=body.account_external_id,
        payload=body.payload,
        idempotency_key=idempotency_key,
    )
    session.add(inbound_event)
    await session.flush()

    audit_payload = {
        "event_type": body.event_type,
        "external_id": body.external_id,
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
    await session.commit()

    return InboundEventResponse(
        event_id=inbound_event.id,
        status="accepted",
        correlation_id=correlation_id,
    )
