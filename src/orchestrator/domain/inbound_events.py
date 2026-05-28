from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.domain.audit_log import append_audit_event
from orchestrator.models import AuditActorType, InboundEvent


def inbound_event_idempotency_key(*, source: str, external_id: str) -> str:
    """Derive a stable internal idempotency key from source event identity."""
    digest = sha256(f"{source}\x1f{external_id}".encode()).hexdigest()
    return f"inbound_event:{digest}"


async def ingest_inbound_event(
    session: AsyncSession,
    *,
    source: str,
    external_id: str,
    event_type: str,
    customer_external_id: str,
    account_external_id: str,
    payload: dict[str, Any],
    correlation_id: UUID,
) -> InboundEvent:
    """Persist an inbound event once, returning the existing row for duplicates."""
    existing_event = await _find_existing_event(
        session,
        source=source,
        external_id=external_id,
    )
    if existing_event is not None:
        return existing_event

    idempotency_key = inbound_event_idempotency_key(
        source=source,
        external_id=external_id,
    )
    inbound_event = InboundEvent(
        source=source,
        external_id=external_id,
        event_type=event_type,
        customer_external_id=customer_external_id,
        account_external_id=account_external_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    session.add(inbound_event)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing_event = await _find_existing_event(
            session,
            source=source,
            external_id=external_id,
        )
        if existing_event is not None:
            return existing_event
        raise

    audit_payload = {
        "source": source,
        "event_type": event_type,
        "external_id": external_id,
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
    return inbound_event


async def _find_existing_event(
    session: AsyncSession,
    *,
    source: str,
    external_id: str,
) -> InboundEvent | None:
    return await session.scalar(
        select(InboundEvent).where(
            InboundEvent.source == source,
            InboundEvent.external_id == external_id,
        )
    )
