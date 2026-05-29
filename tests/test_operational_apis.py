from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.db import get_session
from orchestrator.domain.operational_outreach import (
    TaskStateConflictError,
    record_manual_delivery_result,
)
from orchestrator.main import create_app
from orchestrator.models import (
    Account,
    AuditEvent,
    Base,
    OutreachTask,
    OutreachTaskStatus,
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
    external_id: str = "evt_ops_123",
    event_type: str = "account_delinquent",
    customer_external_id: str = "cus_ops_123",
    account_external_id: str = "acct_ops_456",
    timezone: str = "America/New_York",
    sms_consent: bool = True,
    call_consent: bool = True,
    email_consent: bool = True,
    status: str = "delinquent",
) -> dict[str, object]:
    return {
        "source": source,
        "external_id": external_id,
        "event_type": event_type,
        "customer": {
            "external_id": customer_external_id,
            "full_name": "Jane Ops",
            "timezone": timezone,
            "phone_number": "+141****0100",
            "email": "jane.ops@example.com",
            "sms_consent": sms_consent,
            "call_consent": call_consent,
            "email_consent": email_consent,
        },
        "account": {
            "external_id": account_external_id,
            "status": status,
            "balance_cents": 12500,
            "days_past_due": 14,
        },
        "occurred_at": "2026-05-27T12:00:00Z",
        "metadata": {"source": source},
    }


async def _ingest(client: AsyncClient, **overrides: Any) -> dict[str, object]:
    response = await client.post("/v1/events", json=nested_payload(**overrides))
    assert response.status_code == 200
    return response.json()


async def _tasks(session: AsyncSession) -> list[OutreachTask]:
    return list(
        (
            await session.scalars(
                select(OutreachTask).order_by(OutreachTask.created_at, OutreachTask.id)
            )
        ).all()
    )


async def test_list_tasks_returns_customer_and_account_context_after_event_ingestion(
    client: AsyncClient,
) -> None:
    await _ingest(client)

    response = await client.get("/v1/tasks")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 3
    assert {task["customer_external_id"] for task in tasks} == {"cus_ops_123"}
    assert {task["account_external_id"] for task in tasks} == {"acct_ops_456"}
    assert {task["status"] for task in tasks} == {"scheduled"}
    assert {task["channel"] for task in tasks} == {"sms", "email", "call"}
    assert all(UUID(task["id"]) for task in tasks)


async def test_list_tasks_filters_by_status_account_customer_and_channel(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(
        client,
        external_id="evt_ops_a",
        customer_external_id="cus_ops_a",
        account_external_id="acct_ops_a",
    )
    await _ingest(
        client,
        external_id="evt_ops_b",
        customer_external_id="cus_ops_b",
        account_external_id="acct_ops_b",
    )
    first_account_task = next(
        task
        for task in await _tasks(session)
        if task.account.external_id == "acct_ops_a"
    )
    first_account_task.status = OutreachTaskStatus.SENT
    await session.commit()

    sent_response = await client.get("/v1/tasks", params={"status": "sent"})
    account_response = await client.get(
        "/v1/tasks", params={"account_external_id": "acct_ops_b"}
    )
    customer_response = await client.get(
        "/v1/tasks", params={"customer_external_id": "cus_ops_a"}
    )
    channel_response = await client.get("/v1/tasks", params={"channel": "sms"})

    assert [task["id"] for task in sent_response.json()] == [str(first_account_task.id)]
    assert {task["account_external_id"] for task in account_response.json()} == {
        "acct_ops_b"
    }
    assert len(account_response.json()) == 3
    assert {task["customer_external_id"] for task in customer_response.json()} == {
        "cus_ops_a"
    }
    assert len(customer_response.json()) == 3
    assert {task["channel"] for task in channel_response.json()} == {"sms"}
    assert len(channel_response.json()) == 2


async def test_get_task_includes_policy_decision_context(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    task = (await _tasks(session))[0]

    response = await client.get(f"/v1/tasks/{task.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(task.id)
    assert body["account_external_id"] == "acct_ops_456"
    assert body["customer_external_id"] == "cus_ops_123"
    assert task.policy_decision is not None
    assert body["policy_decision"] == {
        "id": str(task.policy_decision_id),
        "decision": task.policy_decision.decision.value,
        "channel": body["channel"],
        "reasons": task.policy_decision.reasons,
    }


async def test_delivery_result_sent_updates_status_last_error_and_appends_audit(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    task = (await _tasks(session))[0]
    task.last_error = "previous provider failure"
    await session.commit()

    response = await client.post(
        f"/v1/tasks/{task.id}/delivery-result",
        json={
            "status": "sent",
            "provider_message_id": "provider-123",
            "details": {"provider_status": "accepted"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.SENT
    assert task.last_error is None
    audit_event = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "delivery_result_recorded")
    )
    assert audit_event is not None
    assert audit_event.actor_id == "operational-api"
    assert audit_event.correlation_id == task.correlation_id
    assert audit_event.payload == {
        "provider_message_id": "provider-123",
        "details": {"provider_status": "accepted"},
        "previous_status": "scheduled",
        "new_status": "sent",
    }


async def test_delivery_result_failed_sets_last_error_and_appends_audit(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    task = (await _tasks(session))[0]

    response = await client.post(
        f"/v1/tasks/{task.id}/delivery-result",
        json={"status": "failed", "details": {"error": "provider rejected"}},
    )

    assert response.status_code == 200
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.FAILED
    assert task.last_error == "provider rejected"
    audit_event = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "delivery_result_recorded")
    )
    assert audit_event is not None
    assert audit_event.payload["previous_status"] == "scheduled"
    assert audit_event.payload["new_status"] == "failed"
    assert audit_event.payload["details"] == {"error": "provider rejected"}


async def test_delivery_result_rejects_cancelled_task_with_409_and_no_delivery_audit(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    task = (await _tasks(session))[0]
    task.status = OutreachTaskStatus.CANCELLED
    await session.commit()

    response = await client.post(
        f"/v1/tasks/{task.id}/delivery-result",
        json={"status": "sent", "provider_message_id": "provider-123"},
    )

    assert response.status_code == 409
    await session.refresh(task)
    assert task.status is OutreachTaskStatus.CANCELLED
    delivery_audits = (
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "delivery_result_recorded"
            )
        )
    ).all()
    assert delivery_audits == []


async def test_cancel_account_outreach_cancels_only_scheduled_tasks_and_appends_audit(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    scheduled_task, sent_task, failed_task = await _tasks(session)
    sent_task.status = OutreachTaskStatus.SENT
    failed_task.status = OutreachTaskStatus.FAILED
    await session.commit()
    request_correlation_id = uuid4()

    response = await client.post(
        "/v1/accounts/acct_ops_456/cancel-outreach",
        json={"reason": "customer requested agent review"},
        headers={"X-Correlation-ID": str(request_correlation_id)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "account_external_id": "acct_ops_456",
        "cancelled_tasks": 1,
    }
    await session.refresh(scheduled_task)
    await session.refresh(sent_task)
    await session.refresh(failed_task)
    assert scheduled_task.status is OutreachTaskStatus.CANCELLED
    assert sent_task.status is OutreachTaskStatus.SENT
    assert failed_task.status is OutreachTaskStatus.FAILED
    account = await session.scalar(
        select(Account).where(Account.external_id == "acct_ops_456")
    )
    assert account is not None
    assert account.status.value == "delinquent"
    cancel_audits = (
        await session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "outreach_cancelled")
        )
    ).all()
    assert len(cancel_audits) == 1
    assert cancel_audits[0].entity_id == str(scheduled_task.id)
    assert cancel_audits[0].correlation_id == request_correlation_id
    assert cancel_audits[0].payload == {
        "reason": "customer requested agent review",
        "account_external_id": "acct_ops_456",
        "channel": scheduled_task.channel.value,
        "cancelled_by": "operational_api",
    }


async def test_delivery_result_accepts_dispatching_and_failed_source_statuses(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    dispatching_task, failed_task, _ = await _tasks(session)
    dispatching_task.status = OutreachTaskStatus.DISPATCHING
    failed_task.status = OutreachTaskStatus.FAILED
    failed_task.last_error = "temporary provider failure"
    await session.commit()

    dispatching_response = await client.post(
        f"/v1/tasks/{dispatching_task.id}/delivery-result", json={"status": "sent"}
    )
    failed_response = await client.post(
        f"/v1/tasks/{failed_task.id}/delivery-result",
        json={"status": "failed", "details": {}},
    )

    assert dispatching_response.status_code == 200
    assert failed_response.status_code == 200
    await session.refresh(dispatching_task)
    await session.refresh(failed_task)
    assert dispatching_task.status is OutreachTaskStatus.SENT
    assert failed_task.status is OutreachTaskStatus.FAILED
    assert failed_task.last_error == "manual delivery result failed"


async def test_delivery_result_rejects_sent_and_blocked_tasks(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    sent_task, blocked_task, _ = await _tasks(session)
    sent_task.status = OutreachTaskStatus.SENT
    blocked_task.status = OutreachTaskStatus.BLOCKED
    await session.commit()

    sent_response = await client.post(
        f"/v1/tasks/{sent_task.id}/delivery-result", json={"status": "failed"}
    )
    blocked_response = await client.post(
        f"/v1/tasks/{blocked_task.id}/delivery-result", json={"status": "sent"}
    )

    assert sent_response.status_code == 409
    assert blocked_response.status_code == 409


async def test_manual_delivery_result_uses_conditional_status_update(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _ingest(client)
    task = (await _tasks(session))[0]

    await session.execute(
        update(OutreachTask)
        .where(OutreachTask.id == task.id)
        .values(status=OutreachTaskStatus.CANCELLED)
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(TaskStateConflictError):
        await record_manual_delivery_result(
            session,
            task=task,
            status="sent",
            provider_message_id="provider-123",
            details={},
        )

    delivery_audits = (
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "delivery_result_recorded"
            )
        )
    ).all()
    assert delivery_audits == []


async def test_cancel_account_outreach_rejects_blank_reason(
    client: AsyncClient,
) -> None:
    await _ingest(client)

    response = await client.post(
        "/v1/accounts/acct_ops_456/cancel-outreach", json={"reason": "   "}
    )

    assert response.status_code == 422


async def test_list_tasks_enforces_limit(client: AsyncClient) -> None:
    await _ingest(client)

    limited_response = await client.get("/v1/tasks", params={"limit": 2})
    invalid_response = await client.get("/v1/tasks", params={"limit": 101})

    assert limited_response.status_code == 200
    assert len(limited_response.json()) == 2
    assert invalid_response.status_code == 422


async def test_unknown_task_and_account_return_404(client: AsyncClient) -> None:
    unknown_id = uuid4()

    get_response = await client.get(f"/v1/tasks/{unknown_id}")
    delivery_response = await client.post(
        f"/v1/tasks/{unknown_id}/delivery-result", json={"status": "sent"}
    )
    cancel_response = await client.post(
        "/v1/accounts/unknown-account/cancel-outreach", json={"reason": "test"}
    )

    assert get_response.status_code == 404
    assert delivery_response.status_code == 404
    assert cancel_response.status_code == 404
