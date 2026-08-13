"""add verification status to agent findings

Revision ID: 016_add_verification_status
Revises: add_source_type_001
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "016_add_verification_status"
down_revision = "015_add_department_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_findings",
        sa.Column(
            "verification_status",
            sa.String(length=32),
            nullable=False,
            server_default="needs_context",
        ),
    )
    op.create_index(
        "ix_agent_findings_verification_status",
        "agent_findings",
        ["verification_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_findings_verification_status", table_name="agent_findings")
    op.drop_column("agent_findings", "verification_status")