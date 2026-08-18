"""add sandbox_attempts to agent_findings

Revision ID: 021_sba
Revises: 020_pause
Create Date: 2026-07-12

"""
from alembic import op
import sqlalchemy as sa


revision = "021_sba"
down_revision = "020_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_findings",
        sa.Column("sandbox_attempts", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_findings", "sandbox_attempts")
