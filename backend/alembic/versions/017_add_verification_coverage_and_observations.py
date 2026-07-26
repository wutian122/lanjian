"""add verification_coverage and observations to agent_tasks

Revision ID: 017_vco
Revises: 5fc1cc05d5d0
Create Date: 2026-05-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "017_vco"
down_revision: Union[str, None] = "016_add_verification_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("verification_coverage", sa.Float(), nullable=True))
    op.add_column("agent_tasks", sa.Column("observations", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_tasks", "observations")
    op.drop_column("agent_tasks", "verification_coverage")
