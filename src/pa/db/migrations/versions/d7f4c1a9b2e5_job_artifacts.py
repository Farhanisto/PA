"""job artifacts

Revision ID: d7f4c1a9b2e5
Revises: baacc1b1e1e4
Create Date: 2026-10-19

Stores the structured app records a run surfaced (HubSpot engagements,
contacts, ...) alongside the prose summary, so the assistant can render them
as interactive cards instead of a markdown table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7f4c1a9b2e5"
down_revision: Union[str, None] = "baacc1b1e1e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("artifacts", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("jobs", "artifacts")
