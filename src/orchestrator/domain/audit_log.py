from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.models import AuditActorType, AuditEvent


async def append_audit_event(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor_type: AuditActorType,
    correlation_id: UUID,
    actor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append evidence inside the caller-owned transaction.

    The function flushes so callers can depend on the row ID immediately, but it
    deliberately does not commit. Audit rows should land atomically with the
    business state they explain.
    """

    audit_event = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload=payload or {},
        created_at=datetime.now(UTC),
    )
    session.add(audit_event)
    await session.flush()
    return audit_event
