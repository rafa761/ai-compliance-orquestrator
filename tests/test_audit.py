from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.db import get_session
from orchestrator.domain.audit_log import append_audit_event
from orchestrator.domain.inbound_events import inbound_event_idempotency_key
from orchestrator.main import create_app
from orchestrator.models import AuditActorType, AuditEvent, Base
from orchestrator.settings import Settings


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db_session:
        yield db_session

    await engine.dispose()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app(
        Settings(
            service_name="compliant-outreach-orchestrator",
            database_url="sqlite+aiosqlite:///:memory:",
        )
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client


async def test_append_audit_event_flushes_without_committing(
    session: AsyncSession,
) -> None:
    correlation_id = uuid4()

    audit_event = await append_audit_event(
        session,
        entity_type="inbound_event",
        entity_id="event_001",
        event_type="event_received",
        actor_type=AuditActorType.API_CLIENT,
        correlation_id=correlation_id,
        payload={"external_id": "event_001"},
    )

    assert audit_event.id is not None
    assert audit_event.created_at is not None
    assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1

    await session.rollback()

    assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 0


async def test_correlation_id_header_is_propagated(client: AsyncClient) -> None:
    correlation_id = uuid4()

    response = await client.get(
        "/healthz", headers={"X-Correlation-ID": str(correlation_id)}
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == str(correlation_id)


async def test_invalid_correlation_id_header_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert UUID(response.headers["X-Correlation-ID"])
    assert response.headers["X-Correlation-ID"] != "not-a-uuid"


async def test_ingest_event_creates_audit_events_and_filters_by_correlation_id(
    client: AsyncClient,
) -> None:
    correlation_id = uuid4()
    other_correlation_id = uuid4()
    payload = {
        "source": "core_banking_demo",
        "external_id": "event_001",
        "event_type": "account_delinquent",
        "customer_external_id": "cust_001",
        "account_external_id": "acct_001",
        "payload": {"source": "test"},
    }

    ingest_response = await client.post(
        "/v1/events",
        json=payload,
        headers={"X-Correlation-ID": str(correlation_id)},
    )

    assert ingest_response.status_code == 200
    ingest_body = ingest_response.json()
    assert ingest_body["status"] == "accepted"
    assert ingest_body["correlation_id"] == str(correlation_id)
    assert ingest_response.headers["X-Correlation-ID"] == str(correlation_id)

    matching_response = await client.get(
        "/v1/audit", params={"correlation_id": str(correlation_id)}
    )
    non_matching_response = await client.get(
        "/v1/audit", params={"correlation_id": str(other_correlation_id)}
    )

    assert matching_response.status_code == 200
    assert non_matching_response.status_code == 200
    audit_events = matching_response.json()
    assert len(audit_events) == 2
    assert {event["event_type"] for event in audit_events} == {
        "event_received",
        "event_accepted",
    }
    assert {event["correlation_id"] for event in audit_events} == {str(correlation_id)}
    assert {event["entity_id"] for event in audit_events} == {ingest_body["event_id"]}
    expected_idempotency_key = inbound_event_idempotency_key(
        source="core_banking_demo",
        external_id="event_001",
    )
    assert {event["payload"]["idempotency_key"] for event in audit_events} == {
        expected_idempotency_key
    }
    assert non_matching_response.json() == []


async def test_duplicate_source_event_identity_returns_existing_event_without_new_audit(
    client: AsyncClient,
) -> None:
    first_correlation_id = uuid4()
    second_correlation_id = uuid4()
    payload = {
        "source": "core_banking_demo",
        "external_id": "event_001",
        "event_type": "account_delinquent",
        "customer_external_id": "cust_001",
        "account_external_id": "acct_001",
        "payload": {"source": "test"},
    }

    first_response = await client.post(
        "/v1/events",
        json=payload,
        headers={"X-Correlation-ID": str(first_correlation_id)},
    )
    duplicate_response = await client.post(
        "/v1/events",
        json={**payload, "payload": {"source": "retry"}},
        headers={"X-Correlation-ID": str(second_correlation_id)},
    )

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert duplicate_response.json()["event_id"] == first_response.json()["event_id"]
    assert duplicate_response.json()["correlation_id"] == str(second_correlation_id)

    first_audit_response = await client.get(
        "/v1/audit", params={"correlation_id": str(first_correlation_id)}
    )
    duplicate_audit_response = await client.get(
        "/v1/audit", params={"correlation_id": str(second_correlation_id)}
    )

    assert len(first_audit_response.json()) == 2
    assert duplicate_audit_response.json() == []


async def test_idempotency_key_header_does_not_define_event_identity(
    client: AsyncClient,
) -> None:
    payload = {
        "source": "core_banking_demo",
        "external_id": "event_001",
        "event_type": "account_delinquent",
        "customer_external_id": "cust_001",
        "account_external_id": "acct_001",
        "payload": {},
    }

    first_response = await client.post(
        "/v1/events",
        json=payload,
        headers={"Idempotency-Key": "caller-key-1"},
    )
    duplicate_response = await client.post(
        "/v1/events",
        json=payload,
        headers={"Idempotency-Key": "caller-key-2"},
    )

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert duplicate_response.json()["event_id"] == first_response.json()["event_id"]


async def test_event_source_and_external_id_are_required(client: AsyncClient) -> None:
    payload = {
        "event_type": "account_delinquent",
        "customer_external_id": "cust_001",
        "account_external_id": "acct_001",
        "payload": {},
    }

    missing_source_response = await client.post(
        "/v1/events",
        json={**payload, "external_id": "event_001"},
    )
    missing_external_id_response = await client.post(
        "/v1/events",
        json={**payload, "source": "core_banking_demo"},
    )

    assert missing_source_response.status_code == 422
    assert missing_external_id_response.status_code == 422


async def test_same_external_id_from_different_sources_creates_distinct_events(
    client: AsyncClient,
) -> None:
    payload = {
        "external_id": "event_001",
        "event_type": "account_delinquent",
        "customer_external_id": "cust_001",
        "account_external_id": "acct_001",
        "payload": {},
    }

    first_response = await client.post(
        "/v1/events",
        json={**payload, "source": "core_banking_demo"},
    )
    second_response = await client.post(
        "/v1/events",
        json={**payload, "source": "crm_demo"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["event_id"] != first_response.json()["event_id"]
