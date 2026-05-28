"""derive inbound event idempotency from source identity

Revision ID: 6d9d6c4f1f1a
Revises: be0bd9c687b8
Create Date: 2026-05-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6d9d6c4f1f1a"
down_revision: str | None = "be0bd9c687b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inbound_events",
        sa.Column(
            "source",
            sa.String(length=255),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE inbound_events
        SET source = COALESCE(NULLIF(payload ->> 'source', ''), 'unknown')
        """
    )
    op.alter_column("inbound_events", "source", server_default=None)
    op.drop_constraint(
        "inbound_events_external_id_key",
        "inbound_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_inbound_events_source_external_id",
        "inbound_events",
        ["source", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_inbound_events_source_external_id",
        "inbound_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "inbound_events_external_id_key",
        "inbound_events",
        ["external_id"],
    )
    op.drop_column("inbound_events", "source")
