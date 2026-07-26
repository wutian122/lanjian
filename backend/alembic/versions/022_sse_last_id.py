"""为 agent_events 表添加 sse_last_id 列，用于存储 SSE Last-Event-ID 语义，
支持断线重连时从最后一个已接收事件继续推送。

Revision ID: 022_sli
Revises: 021_sba
Create Date: 2026-07-18

"""
from alembic import op
import sqlalchemy as sa


revision = "022_sli"
down_revision = "021_sba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_events",
        sa.Column("sse_last_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_events", "sse_last_id")