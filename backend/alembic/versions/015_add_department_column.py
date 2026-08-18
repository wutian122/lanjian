"""添加 department 列到 users 表

Revision ID: 015_add_department_column
Revises: 014_add_username_column
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa


revision = '015_add_department_column'
down_revision = '014_add_username_column'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'department', sa.String(), nullable=True
    ))


def downgrade() -> None:
    op.drop_column('users', 'department')