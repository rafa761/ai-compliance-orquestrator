from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountStatus(StrEnum):
    CURRENT = "current"
    DELINQUENT = "delinquent"
    RESOLVED = "resolved"
    PAUSED = "paused"


class InboundEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"


class OutreachChannel(StrEnum):
    CALL = "call"
    SMS = "sms"
    EMAIL = "email"


class OutreachTaskStatus(StrEnum):
    SCHEDULED = "scheduled"
    BLOCKED = "blocked"
    DISPATCHING = "dispatching"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PolicyDecisionOutcome(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    DEFER = "defer"


class AuditActorType(StrEnum):
    SYSTEM = "system"
    API_CLIENT = "api_client"
    WORKER = "worker"
    POLICY_ENGINE = "policy_engine"


def enum_column(enum_type: type[StrEnum], name: str) -> SqlEnum:
    """Store enum values as portable strings instead of database-native enums.

    That keeps SQLite tests, PostgreSQL migrations, and local demo data aligned
    without dialect-specific enum DDL.
    """

    return SqlEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        values_callable=lambda enum: [item.value for item in enum],
    )


class Customer(Base):
    """Customer snapshot keyed by source-system identity.

    Consent flags represent the latest known channel permissions. `opted_out` is
    a durable compliance override produced by opt-out events, not a casual field
    overwritten by ordinary customer snapshots.
    """

    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="America/New_York"
    )
    phone_number: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    sms_consent: Mapped[bool] = mapped_column(default=False, nullable=False)
    call_consent: Mapped[bool] = mapped_column(default=False, nullable=False)
    email_consent: Mapped[bool] = mapped_column(default=False, nullable=False)
    opted_out: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    accounts: Mapped[list[Account]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", lazy="selectin"
    )


class Account(Base):
    """Serviced account whose customer relationship must remain stable.

    `external_id` is globally unique because policy decisions, outreach tasks,
    and audit rows rely on a consistent account/customer lineage.
    """

    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    customer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    status: Mapped[AccountStatus] = mapped_column(
        enum_column(AccountStatus, "account_status"), nullable=False
    )
    balance_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    days_past_due: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    customer: Mapped[Customer] = relationship(
        back_populates="accounts", lazy="selectin"
    )


class InboundEvent(Base):
    """Accepted source event and the replay boundary for ingestion.

    `source` + `external_id` is the business identity. The derived
    idempotency_key gives the database a compact unique value, while `payload`
    preserves the accepted source snapshot for audit and debugging.
    """

    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_id",
            name="uq_inbound_events_source_external_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    processing_status: Mapped[InboundEventStatus] = mapped_column(
        enum_column(InboundEventStatus, "inbound_event_status"),
        default=InboundEventStatus.RECEIVED,
        nullable=False,
    )


class PolicyDecision(Base):
    """Recorded policy outcome for a proposed outreach channel.

    The planner stores decisions for ALLOW, BLOCK, and DEFER outcomes. Blocked
    or frequency-capped attempts may have no OutreachTask, but the decision row
    still explains why automation did or did not proceed.
    """

    __tablename__ = "policy_decisions"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    customer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    inbound_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inbound_events.id")
    )
    decision: Mapped[PolicyDecisionOutcome] = mapped_column(
        enum_column(PolicyDecisionOutcome, "policy_decision_outcome"), nullable=False
    )
    channel: Mapped[OutreachChannel | None] = mapped_column(
        enum_column(OutreachChannel, "outreach_channel")
    )
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    account: Mapped[Account] = relationship(lazy="selectin")
    customer: Mapped[Customer] = relationship(lazy="selectin")
    inbound_event: Mapped[InboundEvent | None] = relationship(lazy="selectin")


class OutreachTask(Base):
    """Dispatch work item created only after deterministic policy evaluation.

    The task idempotency key is scoped to inbound event and channel so a replay
    cannot schedule duplicate work for the same logical outreach attempt.
    """

    __tablename__ = "outreach_tasks"
    __table_args__ = (
        Index("ix_outreach_tasks_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_outreach_tasks_customer_id_created_at", "customer_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    customer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    channel: Mapped[OutreachChannel] = mapped_column(
        enum_column(OutreachChannel, "outreach_channel"), nullable=False
    )
    status: Mapped[OutreachTaskStatus] = mapped_column(
        enum_column(OutreachTaskStatus, "outreach_task_status"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    policy_decision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("policy_decisions.id")
    )
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(lazy="selectin")
    customer: Mapped[Customer] = relationship(lazy="selectin")
    policy_decision: Mapped[PolicyDecision | None] = relationship(lazy="selectin")


class AuditEvent(Base):
    """Append-only evidence record for request, policy, and planner decisions.

    `correlation_id` ties rows to one request/workflow. `payload` carries the
    contextual facts needed to explain the event, not normalized mutable state.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_correlation_id", "correlation_id"),
        Index("ix_audit_events_entity_type_entity_id", "entity_type", "entity_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    entity_type: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_type: Mapped[AuditActorType] = mapped_column(
        enum_column(AuditActorType, "audit_actor_type"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
