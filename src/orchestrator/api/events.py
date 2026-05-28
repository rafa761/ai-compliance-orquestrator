from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db import get_session
from orchestrator.domain.inbound_events import ingest_inbound_event

router = APIRouter(prefix="/v1/events", tags=["events"])

IdentityField = Annotated[str, Field(min_length=1, max_length=255)]


class InboundEventRequest(BaseModel):
    source: IdentityField
    external_id: IdentityField
    event_type: IdentityField
    customer_external_id: IdentityField
    account_external_id: IdentityField
    payload: dict[str, Any] = Field(default_factory=dict)


class InboundEventResponse(BaseModel):
    event_id: UUID
    status: str
    correlation_id: UUID


@router.post("", response_model=InboundEventResponse)
async def ingest_event(
    request: Request,
    body: InboundEventRequest,
    session: AsyncSession = Depends(get_session),
) -> InboundEventResponse:
    correlation_id: UUID = request.state.correlation_id
    inbound_event = await ingest_inbound_event(
        session,
        source=body.source,
        external_id=body.external_id,
        event_type=body.event_type,
        customer_external_id=body.customer_external_id,
        account_external_id=body.account_external_id,
        payload=body.payload,
        correlation_id=correlation_id,
    )
    return InboundEventResponse(
        event_id=inbound_event.id,
        status="accepted",
        correlation_id=correlation_id,
    )
