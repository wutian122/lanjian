"""Add multi-tenant support

Revision ID: 011_add_tenancy
Revises: 010_fill_empty_base_url
Create Date: 2026-05-06
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '011_add_tenancy'
down_revision = '010_fill_empty_base_url'
branch_labels = None
depends_on = None


def upgrade():
    # 1. 创建 tenants 表
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('status', sa.String(20), server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # 2. 插入 SuperAdmin 的默认 tenant
    op.execute("INSERT INTO tenants (id, name, status) VALUES (0, 'SuperAdmin', 'active')")

    # 3. users 表添加 tenant_id
    op.add_column('users', sa.Column('tenant_id', sa.Integer(), server_default='0', nullable=False))
    op.create_foreign_key('fk_users_tenant', 'users', 'tenants', ['tenant_id'], ['id'])
    op.create_index('idx_users_tenant', 'users', ['tenant_id'])

    # 4. projects 表添加 tenant_id
    op.add_column('projects', sa.Column('tenant_id', sa.Integer(), server_default='0', nullable=False))
    op.create_index('idx_projects_tenant', 'projects', ['tenant_id'])

    # 5. agent_tasks 表添加 tenant_id
    op.add_column('agent_tasks', sa.Column('tenant_id', sa.Integer(), server_default='0', nullable=False))
    op.create_index('idx_agent_tasks_tenant', 'agent_tasks', ['tenant_id'])

    # 6. user_configs 表添加 tenant_id
    op.add_column('user_configs', sa.Column('tenant_id', sa.Integer(), server_default='0', nullable=False))

    # 7. agent_findings 表添加 tenant_id
    op.add_column('agent_findings', sa.Column('tenant_id', sa.Integer(), server_default='0', nullable=False))

    # 8. audit_tasks 表添加 tenant_id
    op.add_column('audit_tasks', sa.Column('tenant_id', sa.Integer(), server_default='0', nullable=False))

    # 9. 现有 SuperAdmin 的 tenant_id 设为 0
    op.execute("UPDATE users SET tenant_id = 0 WHERE is_superuser = true")


def downgrade():
    # 反向操作: 删除所有 tenant_id 列和 tenants 表
    op.drop_constraint('fk_users_tenant', 'users', type_='foreignkey')
    op.drop_index('idx_users_tenant', table_name='users')
    op.drop_index('idx_projects_tenant', table_name='projects')
    op.drop_index('idx_agent_tasks_tenant', table_name='agent_tasks')

    op.drop_column('users', 'tenant_id')
    op.drop_column('projects', 'tenant_id')
    op.drop_column('agent_tasks', 'tenant_id')
    op.drop_column('user_configs', 'tenant_id')
    op.drop_column('agent_findings', 'tenant_id')
    op.drop_column('audit_tasks', 'tenant_id')

    op.drop_table('tenants')
