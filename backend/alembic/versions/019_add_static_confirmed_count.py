"""add static_confirmed_count to agent_tasks

Revision ID: 019_scc
Revises: 018_cleanup_dead_schema
Create Date: 2026-06-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "019_scc"
down_revision = "018_cleanup_dead_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column("static_confirmed_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "static_confirmed_count")
