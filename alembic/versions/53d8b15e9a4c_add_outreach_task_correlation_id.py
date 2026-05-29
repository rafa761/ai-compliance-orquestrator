"""add outreach task correlation id

Revision ID: 53d8b15e9a4c
Revises: 6d9d6c4f1f1a
Create Date: 2026-05-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "53d8b15e9a4c"
down_revision: str | None = "6d9d6c4f1f1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outreach_tasks",
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("outreach_tasks", "correlation_id")
