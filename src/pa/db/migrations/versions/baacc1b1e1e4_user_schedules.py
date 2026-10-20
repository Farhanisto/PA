"""user schedules

Revision ID: baacc1b1e1e4
Revises: cfe735c8527c
Create Date: 2026-10-19

Celery beat cannot express per-user times, so beat runs a dispatcher that
consults these rows and fans out one task per due user.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "baacc1b1e1e4"
down_revision: Union[str, None] = "cfe735c8527c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: LangGraph's checkpoint_* tables are created and migrated by
    # LangGraph itself, never by Alembic. Autogenerate proposes dropping and
    # recreating them, which would destroy checkpoint state, so they are
    # deliberately absent here.
    op.create_table(
        "user_schedules",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("briefing_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("briefing_hour", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("briefing_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("followup_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("followup_hour", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("followup_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_schedules")