"""job focus

Revision ID: a1b2c3d4e5f6
Revises: f2c7b4e6a913
Create Date: 2026-10-19

Stores the record a job was acting on. Clicking a card in the chat focuses
it, and the next message is about that record — the assistant uses its id
in tool arguments rather than guessing which record was meant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f2c7b4e6a913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("focus", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("jobs", "focus")
