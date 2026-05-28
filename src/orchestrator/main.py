from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from orchestrator.audit import append_audit_event
from orchestrator.db import get_session
from orchestrator.models import AuditActorType, AuditEvent, InboundEvent
from orchestrator.settings import Settings, get_settings


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


def _correlation_id_from_header(value: str | None) -> UUID:
    if value is None:
        return uuid4()
    try:
        return UUID(value)
    except ValueError:
        return uuid4()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    app = FastAPI(
        title="Compliant Outreach Orchestrator",
        summary="Demo service for compliance-aware multi-channel outreach orchestration.",
        version="0.1.0",
    )
    app.state.settings = app_settings

    @app.middleware("http")
    async def correlation_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        correlation_id = _correlation_id_from_header(
            request.headers.get("X-Correlation-ID")
        )
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(correlation_id)
        return response

    @app.get("/healthz", tags=["health"])
    async def healthz(request: Request) -> dict[str, str]:
        current_settings: Settings = request.app.state.settings
        return {"service": current_settings.service_name, "status": "ok"}

    @app.post("/v1/events", response_model=InboundEventResponse, tags=["events"])
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

    @app.get("/v1/audit", response_model=list[AuditEventResponse], tags=["audit"])
    async def list_audit_events(
        correlation_id: UUID | None = None,
        session: AsyncSession = Depends(get_session),
    ) -> list[AuditEvent]:
        statement = select(AuditEvent)
        if correlation_id is not None:
            statement = statement.where(AuditEvent.correlation_id == correlation_id)
        statement = statement.order_by(AuditEvent.created_at, AuditEvent.id)
        return list(await session.scalars(statement))

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "orchestrator.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    run()
