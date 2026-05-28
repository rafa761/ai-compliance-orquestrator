from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.db import get_session
from orchestrator.main import create_app
from orchestrator.models import (
    Account,
    AccountStatus,
    AuditEvent,
    Base,
    Customer,
    InboundEvent,
    InboundEventStatus,
    OutreachChannel,
    OutreachTask,
    OutreachTaskStatus,
    PolicyDecision,
    PolicyDecisionOutcome,
)
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


def nested_payload(
    *,
    source: str = "core_banking_demo",
    external_id: str = "evt_123",
    event_type: str = "account_delinquent",
    customer_external_id: str = "cus_123",
    account_external_id: str = "acct_456",
    timezone: str = "America/New_York",
    sms_consent: bool = True,
    call_consent: bool = True,
    email_consent: bool = True,
    full_name: str = "Jane Doe",
    email: str = "jane@example.com",
    phone_number: str = "+14155550100",
    status: str = "delinquent",
    balance_cents: int = 12500,
    days_past_due: int = 14,
) -> dict[str, object]:
    return {
        "source": source,
        "external_id": external_id,
        "event_type": event_type,
        "customer": {
            "external_id": customer_external_id,
            "full_name": full_name,
            "timezone": timezone,
            "phone_number": phone_number,
            "email": email,
            "sms_consent": sms_consent,
            "call_consent": call_consent,
            "email_consent": email_consent,
        },
        "account": {
            "external_id": account_external_id,
            "status": status,
            "balance_cents": balance_cents,
            "days_past_due": days_past_due,
        },
        "occurred_at": "2026-05-27T12:00:00Z",
        "metadata": {"source": source},
    }


async def count_rows(session: AsyncSession, model: type[Base]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_nested_account_delinquent_ingestion_persists_snapshots_event_audit_and_plans(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    correlation_id = uuid4()

    response = await client.post(
        "/v1/events",
        json=nested_payload(),
        headers={"X-Correlation-ID": str(correlation_id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert UUID(body["event_id"])
    assert body == {
        "event_id": body["event_id"],
        "status": "accepted",
        "created_tasks": 3,
        "blocked_tasks": 0,
        "deferred_tasks": 1,
        "cancelled_tasks": 0,
        "policy_decisions": 3,
        "correlation_id": str(correlation_id),
    }

    customer = await session.scalar(
        select(Customer).where(Customer.external_id == "cus_123")
    )
    account = await session.scalar(
        select(Account).where(Account.external_id == "acct_456")
    )
    event = await session.get(InboundEvent, UUID(body["event_id"]))
    assert customer is not None
    assert customer.full_name == "Jane Doe"
    assert customer.timezone == "America/New_York"
    assert customer.phone_number == "+14155550100"
    assert customer.email == "jane@example.com"
    assert customer.sms_consent is True
    assert customer.call_consent is True
    assert customer.email_consent is True
    assert account is not None
    assert account.customer_id == customer.id
    assert account.status == AccountStatus.DELINQUENT
    assert account.balance_cents == 12500
    assert account.days_past_due == 14
    assert event is not None
    assert event.processing_status == InboundEventStatus.PROCESSED
    assert event.customer_external_id == "cus_123"
    assert event.account_external_id == "acct_456"
    assert event.payload["customer"]["full_name"] == "Jane Doe"
    assert event.payload["account"]["balance_cents"] == 12500
    assert event.payload["occurred_at"] == "2026-05-27T12:00:00+00:00"
    assert event.payload["metadata"] == {"source": "core_banking_demo"}

    assert await count_rows(session, OutreachTask) == 3
    assert await count_rows(session, PolicyDecision) == 3
    audit_types = (
        await session.scalars(
            select(AuditEvent.event_type).where(
                AuditEvent.correlation_id == correlation_id
            )
        )
    ).all()
    assert audit_types.count("event_received") == 1
    assert audit_types.count("event_accepted") == 1
    assert audit_types.count("policy_decision_recorded") == 3
    assert audit_types.count("outreach_task_scheduled") == 3


async def test_existing_snapshots_update_mutable_fields_before_planning(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    customer = Customer(
        external_id="cus_123",
        full_name="Old Name",
        timezone="America/Chicago",
        phone_number="+10000000000",
        email="old@example.com",
        sms_consent=True,
        call_consent=False,
        email_consent=False,
    )
    account = Account(
        external_id="acct_456",
        customer=customer,
        status=AccountStatus.CURRENT,
        balance_cents=500,
        days_past_due=0,
    )
    session.add_all([customer, account])
    await session.commit()

    response = await client.post(
        "/v1/events",
        json=nested_payload(
            event_type="payment_failed",
            sms_consent=False,
            call_consent=True,
            email_consent=True,
            full_name="Updated Name",
            email="updated@example.com",
            phone_number="+14155550199",
            balance_cents=9900,
            days_past_due=3,
        ),
    )

    assert response.status_code == 200
    assert response.json()["created_tasks"] == 1
    assert response.json()["blocked_tasks"] == 1
    assert response.json()["policy_decisions"] == 2
    await session.refresh(customer)
    await session.refresh(account)
    assert customer.full_name == "Updated Name"
    assert customer.email == "updated@example.com"
    assert customer.phone_number == "+14155550199"
    assert customer.sms_consent is False
    assert customer.call_consent is True
    assert customer.email_consent is True
    assert account.status == AccountStatus.DELINQUENT
    assert account.balance_cents == 9900
    assert account.days_past_due == 3
    decisions = (await session.scalars(select(PolicyDecision))).all()
    sms_decision = next(d for d in decisions if d.channel == OutreachChannel.SMS)
    assert sms_decision.decision == PolicyDecisionOutcome.BLOCK
    assert sms_decision.reasons == ["missing_sms_consent"]


async def test_duplicate_nested_event_returns_same_event_without_mutating_or_side_effects(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    first_response = await client.post("/v1/events", json=nested_payload())
    duplicate_response = await client.post(
        "/v1/events",
        json=nested_payload(
            full_name="Retry Body",
            balance_cents=999999,
            sms_consent=False,
        ),
        headers={"X-Correlation-ID": str(uuid4())},
    )

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert duplicate_response.json()["event_id"] == first_response.json()["event_id"]
    assert duplicate_response.json()["created_tasks"] == 3
    assert duplicate_response.json()["deferred_tasks"] == 1
    assert duplicate_response.json()["policy_decisions"] == 3

    customer = await session.scalar(
        select(Customer).where(Customer.external_id == "cus_123")
    )
    account = await session.scalar(
        select(Account).where(Account.external_id == "acct_456")
    )
    assert customer is not None
    assert customer.full_name == "Jane Doe"
    assert customer.sms_consent is True
    assert account is not None
    assert account.balance_cents == 12500
    assert await count_rows(session, InboundEvent) == 1
    assert await count_rows(session, OutreachTask) == 3
    assert await count_rows(session, PolicyDecision) == 3
    assert await count_rows(session, AuditEvent) == 9


async def test_unsupported_event_type_returns_422_without_db_writes(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    response = await client.post(
        "/v1/events", json=nested_payload(event_type="unsupported_event")
    )

    assert response.status_code == 422
    assert await count_rows(session, Customer) == 0
    assert await count_rows(session, Account) == 0
    assert await count_rows(session, InboundEvent) == 0
    assert await count_rows(session, AuditEvent) == 0


async def test_event_source_and_external_id_are_required(client: AsyncClient) -> None:
    payload = nested_payload()

    missing_source_response = await client.post(
        "/v1/events",
        json={key: value for key, value in payload.items() if key != "source"},
    )
    missing_external_id_response = await client.post(
        "/v1/events",
        json={key: value for key, value in payload.items() if key != "external_id"},
    )

    assert missing_source_response.status_code == 422
    assert missing_external_id_response.status_code == 422


async def test_invalid_timezone_returns_422_without_db_writes(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    response = await client.post(
        "/v1/events", json=nested_payload(timezone="Not/A_Timezone")
    )

    assert response.status_code == 422
    assert await count_rows(session, Customer) == 0
    assert await count_rows(session, Account) == 0
    assert await count_rows(session, InboundEvent) == 0
    assert await count_rows(session, AuditEvent) == 0


async def test_naive_occurred_at_returns_422_without_db_writes(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    payload = nested_payload()
    payload["occurred_at"] = "2026-05-27T12:00:00"

    response = await client.post("/v1/events", json=payload)

    assert response.status_code == 422
    assert await count_rows(session, Customer) == 0
    assert await count_rows(session, Account) == 0
    assert await count_rows(session, InboundEvent) == 0
    assert await count_rows(session, AuditEvent) == 0


async def test_existing_account_under_different_customer_returns_422_without_side_effects(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    existing_customer = Customer(
        external_id="cus_existing",
        full_name="Existing Customer",
        timezone="America/New_York",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    session.add(
        Account(
            external_id="acct_456",
            customer=existing_customer,
            status=AccountStatus.CURRENT,
            balance_cents=1,
            days_past_due=0,
        )
    )
    await session.commit()

    response = await client.post("/v1/events", json=nested_payload())

    assert response.status_code == 422
    assert await count_rows(session, InboundEvent) == 0
    assert await count_rows(session, OutreachTask) == 0
    assert await count_rows(session, PolicyDecision) == 0
    assert await count_rows(session, AuditEvent) == 0
    await session.refresh(existing_customer)
    assert existing_customer.full_name == "Existing Customer"
    assert await count_rows(session, Customer) == 1


async def test_same_external_id_different_sources_creates_distinct_events_with_same_snapshots(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    first_response = await client.post(
        "/v1/events",
        json=nested_payload(source="core_banking_demo", external_id="evt_same"),
    )
    second_response = await client.post(
        "/v1/events",
        json=nested_payload(source="crm_demo", external_id="evt_same"),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["event_id"] != first_response.json()["event_id"]
    assert await count_rows(session, InboundEvent) == 2
    assert await count_rows(session, Customer) == 1
    assert await count_rows(session, Account) == 1
    assert await count_rows(session, PolicyDecision) == 6


async def test_duplicate_cancellation_event_reconstructs_cancelled_count_without_side_effects(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    delinquency_response = await client.post(
        "/v1/events",
        json=nested_payload(external_id="evt_delinquent"),
    )
    payment_response = await client.post(
        "/v1/events",
        json=nested_payload(
            external_id="evt_payment_received",
            event_type="payment_received",
            status="current",
            balance_cents=0,
            days_past_due=0,
        ),
    )
    duplicate_payment_response = await client.post(
        "/v1/events",
        json=nested_payload(
            external_id="evt_payment_received",
            event_type="payment_received",
            full_name="Retry Body",
            status="current",
            balance_cents=0,
            days_past_due=0,
        ),
    )

    assert delinquency_response.status_code == 200
    assert payment_response.status_code == 200
    assert duplicate_payment_response.status_code == 200
    assert (
        duplicate_payment_response.json()["event_id"]
        == payment_response.json()["event_id"]
    )
    assert payment_response.json()["cancelled_tasks"] == 3
    assert duplicate_payment_response.json()["cancelled_tasks"] == 3
    assert await count_rows(session, InboundEvent) == 2
    assert await count_rows(session, OutreachTask) == 3
    assert await count_rows(session, PolicyDecision) == 3
    assert await count_rows(session, AuditEvent) == 15
    task_statuses = (await session.scalars(select(OutreachTask.status))).all()
    assert task_statuses == [OutreachTaskStatus.CANCELLED] * 3


async def test_existing_opted_out_customer_is_not_reset_by_normal_snapshot(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    customer = Customer(
        external_id="cus_123",
        full_name="Opted Out",
        timezone="America/New_York",
        opted_out=True,
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    session.add(
        Account(
            external_id="acct_456",
            customer=customer,
            status=AccountStatus.CURRENT,
        )
    )
    await session.commit()

    response = await client.post("/v1/events", json=nested_payload())

    assert response.status_code == 200
    assert response.json()["created_tasks"] == 0
    assert response.json()["blocked_tasks"] == 3
    await session.refresh(customer)
    assert customer.opted_out is True
