from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.models import (
    Account,
    AccountStatus,
    AuditActorType,
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


@pytest.fixture
async def session() -> AsyncSession:
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


async def test_customer_external_id_is_unique(session: AsyncSession) -> None:
    session.add_all(
        [
            Customer(
                external_id="cust_001",
                full_name="Jane Doe",
                timezone="America/New_York",
                phone_number="+14155550100",
                email="jane@example.com",
            ),
            Customer(
                external_id="cust_001",
                full_name="Jane Duplicate",
                timezone="America/New_York",
                phone_number="+14155550101",
                email="jane.duplicate@example.com",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_inbound_event_external_id_can_repeat_across_sources(
    session: AsyncSession,
) -> None:
    session.add_all(
        [
            InboundEvent(
                source="core_banking_demo",
                external_id="event_001",
                event_type="account_delinquent",
                customer_external_id="cust_001",
                account_external_id="acct_001",
                payload={},
                idempotency_key="inbound_event:core_banking_demo:event_001",
            ),
            InboundEvent(
                source="crm_demo",
                external_id="event_001",
                event_type="account_delinquent",
                customer_external_id="cust_001",
                account_external_id="acct_001",
                payload={},
                idempotency_key="inbound_event:crm_demo:event_001",
            ),
        ]
    )

    await session.commit()

    assert (
        await session.scalar(
            select(InboundEvent).where(InboundEvent.source == "crm_demo")
        )
    ) is not None


async def test_inbound_event_idempotency_key_is_unique(session: AsyncSession) -> None:
    session.add_all(
        [
            InboundEvent(
                source="core_banking_demo",
                external_id="event_001",
                event_type="account_delinquent",
                customer_external_id="cust_001",
                account_external_id="acct_001",
                payload={"source": "test"},
                idempotency_key="idem_001",
            ),
            InboundEvent(
                source="core_banking_demo",
                external_id="event_002",
                event_type="account_delinquent",
                customer_external_id="cust_001",
                account_external_id="acct_001",
                payload={"source": "test"},
                idempotency_key="idem_001",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_outreach_task_idempotency_key_is_unique(session: AsyncSession) -> None:
    customer = Customer(
        external_id="cust_001",
        full_name="Jane Doe",
        timezone="America/New_York",
        email="jane@example.com",
    )
    account = Account(
        external_id="acct_001",
        customer=customer,
        status=AccountStatus.DELINQUENT,
        balance_cents=12500,
        days_past_due=14,
    )
    decision = PolicyDecision(
        account=account,
        customer=customer,
        decision=PolicyDecisionOutcome.ALLOW,
        channel=OutreachChannel.EMAIL,
        reasons=["email_consent_present"],
    )
    scheduled_at = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)

    session.add_all(
        [
            OutreachTask(
                account=account,
                customer=customer,
                channel=OutreachChannel.EMAIL,
                status=OutreachTaskStatus.SCHEDULED,
                scheduled_at=scheduled_at,
                idempotency_key="task_001",
                policy_decision=decision,
            ),
            OutreachTask(
                account=account,
                customer=customer,
                channel=OutreachChannel.EMAIL,
                status=OutreachTaskStatus.SCHEDULED,
                scheduled_at=scheduled_at,
                idempotency_key="task_001",
                policy_decision=decision,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_customer_account_and_audit_relationships(session: AsyncSession) -> None:
    correlation_id = uuid4()
    customer = Customer(
        external_id="cust_001",
        full_name="Jane Doe",
        timezone="America/New_York",
        phone_number="+14155550100",
        email="jane@example.com",
        sms_consent=True,
        call_consent=True,
        email_consent=True,
    )
    account = Account(
        external_id="acct_001",
        customer=customer,
        status=AccountStatus.DELINQUENT,
        balance_cents=12500,
        days_past_due=14,
    )
    audit_event = AuditEvent(
        entity_type="account",
        entity_id="acct_001",
        event_type="account_delinquent_received",
        actor_type=AuditActorType.SYSTEM,
        actor_id="policy-engine",
        correlation_id=correlation_id,
        payload={"account_external_id": "acct_001"},
    )
    session.add_all([customer, account, audit_event])
    await session.commit()

    loaded_customer = await session.scalar(
        select(Customer).where(Customer.external_id == "cust_001")
    )

    assert loaded_customer is not None
    assert loaded_customer.accounts[0].external_id == "acct_001"
    assert loaded_customer.accounts[0].status is AccountStatus.DELINQUENT

    loaded_audit_event = await session.scalar(
        select(AuditEvent).where(AuditEvent.correlation_id == correlation_id)
    )
    assert loaded_audit_event is not None
    assert loaded_audit_event.payload == {"account_external_id": "acct_001"}
    assert loaded_audit_event.actor_type is AuditActorType.SYSTEM


async def test_model_tables_match_phase_1_scope() -> None:
    assert set(Base.metadata.tables) == {
        "accounts",
        "audit_events",
        "customers",
        "inbound_events",
        "outreach_tasks",
        "policy_decisions",
    }

    assert Customer.external_id.property.columns[0].unique is True
    assert Account.external_id.property.columns[0].unique is True
    assert InboundEvent.external_id.property.columns[0].unique is not True
    assert InboundEvent.idempotency_key.property.columns[0].unique is True
    assert OutreachTask.idempotency_key.property.columns[0].unique is True
    assert {
        constraint.name
        for constraint in Base.metadata.tables["inbound_events"].constraints
    } >= {"uq_inbound_events_source_external_id"}
    assert InboundEvent.processing_status.property.columns[0].default.arg is (
        InboundEventStatus.RECEIVED
    )
    assert {index.name for index in Base.metadata.tables["outreach_tasks"].indexes} == {
        "ix_outreach_tasks_customer_id_created_at",
        "ix_outreach_tasks_status_scheduled_at",
    }
    assert {index.name for index in Base.metadata.tables["audit_events"].indexes} == {
        "ix_audit_events_correlation_id",
        "ix_audit_events_entity_type_entity_id",
    }


async def test_phase_1_avoids_database_enum_check_constraints() -> None:
    for table in Base.metadata.tables.values():
        assert not any(
            isinstance(constraint, CheckConstraint) for constraint in table.constraints
        )
