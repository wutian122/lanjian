"""Add security fields to users and create audit_logs table

Revision ID: 012_add_security_fields
Revises: 011_add_tenancy
Create Date: 2026-05-06
"""
from alembic import op
import sqlalchemy as sa

revision = '012_add_security_fields'
down_revision = '011_add_tenancy'
branch_labels = None
depends_on = None


def upgrade():
    # 1. users 表添加安全字段
    op.add_column('users', sa.Column('is_first_login', sa.Boolean(), server_default='true', nullable=True))
    op.add_column('users', sa.Column('password_history', sa.JSON(), server_default='[]', nullable=True))
    op.add_column('users', sa.Column('failed_login_attempts', sa.Integer(), server_default='0', nullable=True))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('last_password_change', sa.DateTime(timezone=True), nullable=True))

    # 2. 创建 audit_logs 表
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('action', sa.String(50), nullable=False, index=True),
        sa.Column('actor_id', sa.String(36), nullable=True),
        sa.Column('target_type', sa.String(50), nullable=True),
        sa.Column('target_id', sa.String(36), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    # 删除 audit_logs 表
    op.drop_table('audit_logs')

    # 删除 users 安全字段
    op.drop_column('users', 'last_password_change')
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_login_attempts')
    op.drop_column('users', 'password_history')
    op.drop_column('users', 'is_first_login')
