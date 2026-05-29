from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db import get_session
from orchestrator.domain.operational_outreach import (
    cancel_scheduled_account_outreach,
    find_account_by_external_id,
)

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


class CancelOutreachRequest(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class CancelOutreachResponse(BaseModel):
    account_external_id: str
    cancelled_tasks: int


@router.post(
    "/{account_external_id}/cancel-outreach", response_model=CancelOutreachResponse
)
async def cancel_account_outreach(
    account_external_id: str,
    body: CancelOutreachRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CancelOutreachResponse:
    account = await find_account_by_external_id(session, account_external_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account not found"
        )

    correlation_id: UUID = request.state.correlation_id
    cancelled_tasks = await cancel_scheduled_account_outreach(
        session,
        account=account,
        reason=body.reason,
        correlation_id=correlation_id,
    )
    return CancelOutreachResponse(
        account_external_id=account.external_id,
        cancelled_tasks=cancelled_tasks,
    )
