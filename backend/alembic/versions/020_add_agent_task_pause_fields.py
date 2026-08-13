"""add paused and last_checkpoint_id to agent_tasks

Revision ID: 020_pause
Revises: 019_scc
Create Date: 2026-07-03

"""
from alembic import op
import sqlalchemy as sa


revision = "020_pause"
down_revision = "019_scc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "agent_tasks",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_tasks",
        sa.Column("pause_reason", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "agent_tasks",
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "agent_tasks",
        sa.Column("last_checkpoint_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "agent_tasks",
        sa.Column("resume_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "resume_count")
    op.drop_column("agent_tasks", "last_checkpoint_id")
    op.drop_column("agent_tasks", "last_error_code")
    op.drop_column("agent_tasks", "pause_reason")
    op.drop_column("agent_tasks", "paused_at")
    op.drop_column("agent_tasks", "paused")
