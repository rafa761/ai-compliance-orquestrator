from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db import get_session
from orchestrator.domain.event_ingestion import (
    AccountSnapshot,
    CustomerSnapshot,
    EventIngestionValidationError,
    ingest_event_snapshot,
)
from orchestrator.models import AccountStatus

router = APIRouter(prefix="/v1/events", tags=["events"])

IdentityField = Annotated[str, Field(min_length=1, max_length=255)]
EventType = Literal[
    "account_delinquent",
    "payment_failed",
    "payment_received",
    "hardship_requested",
    "opt_out_received",
    "account_paused",
]


class CustomerSnapshotRequest(BaseModel):
    external_id: IdentityField
    full_name: Annotated[str, Field(min_length=1, max_length=255)]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    phone_number: Annotated[str | None, Field(max_length=64)] = None
    email: Annotated[str | None, Field(max_length=255)] = None
    sms_consent: bool = False
    call_consent: bool = False
    email_consent: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Invalid customer timezone") from exc
        return value


class AccountSnapshotRequest(BaseModel):
    external_id: IdentityField
    status: AccountStatus
    balance_cents: int = Field(default=0, ge=0)
    days_past_due: int = Field(default=0, ge=0)


class InboundEventRequest(BaseModel):
    source: IdentityField
    external_id: IdentityField
    event_type: EventType
    customer: CustomerSnapshotRequest
    account: AccountSnapshotRequest
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class InboundEventResponse(BaseModel):
    event_id: UUID
    status: str
    created_tasks: int
    blocked_tasks: int
    deferred_tasks: int
    cancelled_tasks: int
    policy_decisions: int
    correlation_id: UUID


@router.post("", response_model=InboundEventResponse)
async def ingest_event(
    request: Request,
    body: InboundEventRequest,
    session: AsyncSession = Depends(get_session),
) -> InboundEventResponse:
    correlation_id: UUID = request.state.correlation_id
    try:
        result = await ingest_event_snapshot(
            session,
            source=body.source,
            external_id=body.external_id,
            event_type=body.event_type,
            customer_snapshot=CustomerSnapshot(
                external_id=body.customer.external_id,
                full_name=body.customer.full_name,
                timezone=body.customer.timezone,
                phone_number=body.customer.phone_number,
                email=body.customer.email,
                sms_consent=body.customer.sms_consent,
                call_consent=body.customer.call_consent,
                email_consent=body.customer.email_consent,
            ),
            account_snapshot=AccountSnapshot(
                external_id=body.account.external_id,
                status=body.account.status,
                balance_cents=body.account.balance_cents,
                days_past_due=body.account.days_past_due,
            ),
            occurred_at=body.occurred_at,
            metadata=body.metadata,
            correlation_id=correlation_id,
        )
    except EventIngestionValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return InboundEventResponse(
        event_id=result.event_id,
        status="accepted",
        created_tasks=result.created_tasks,
        blocked_tasks=result.blocked_tasks,
        deferred_tasks=result.deferred_tasks,
        cancelled_tasks=result.cancelled_tasks,
        policy_decisions=result.policy_decisions,
        correlation_id=correlation_id,
    )
