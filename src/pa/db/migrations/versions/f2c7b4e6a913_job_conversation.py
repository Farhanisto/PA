"""job conversation id

Revision ID: f2c7b4e6a913
Revises: d7f4c1a9b2e5
Create Date: 2026-10-19

Groups the turns of a conversation so a short answer to a clarifying
question ("tickets") is understood in the context of the question it
answers. A thread remains one run; a conversation is the exchange.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c7b4e6a913"
down_revision: Union[str, None] = "d7f4c1a9b2e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("conversation_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index("ix_jobs_conversation_id", "jobs", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_conversation_id", table_name="jobs")
    op.drop_column("jobs", "conversation_id")
