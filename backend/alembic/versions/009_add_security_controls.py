"""Add security controls and coverage tracking tables (v3.1 Fusion)

Revision ID: 009_add_security_controls
Revises: 008_add_files_with_findings
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '009_add_security_controls'
down_revision = '008_add_files_with_findings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 安全控制定义表
    op.create_table(
        'security_controls',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('control_id', sa.String(50), unique=True, index=True, nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('name_zh', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(20), default='MEDIUM'),
        sa.Column('cwe', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # 敏感操作定义表
    op.create_table(
        'sensitive_operations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(100), unique=True, index=True, nullable=False),
        sa.Column('name_zh', sa.String(200), nullable=False),
        sa.Column('risk_level', sa.String(20), default='MEDIUM'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('patterns', sa.JSON(), default=list),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # 操作-控制关联表
    op.create_table(
        'operation_required_controls',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('operation_id', sa.String(), sa.ForeignKey('sensitive_operations.id'), nullable=False),
        sa.Column('control_id', sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # 语言适配器缓存表
    op.create_table(
        'language_adapters',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('language', sa.String(50), unique=True, index=True, nullable=False),
        sa.Column('adapter_data', sa.JSON(), nullable=False),
        sa.Column('file_extensions', sa.JSON(), default=list),
        sa.Column('framework_count', sa.Integer(), default=0),
        sa.Column('control_pattern_count', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # D1-D10 覆盖追踪表
    op.create_table(
        'coverage_tracks',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), sa.ForeignKey('audit_tasks.id'), nullable=False, index=True),
        sa.Column('dimension', sa.String(10), nullable=False),
        sa.Column('status', sa.String(20), default='uncovered'),
        sa.Column('findings_count', sa.Integer(), default=0),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('coverage_tracks')
    op.drop_table('language_adapters')
    op.drop_table('operation_required_controls')
    op.drop_table('sensitive_operations')
    op.drop_table('security_controls')
