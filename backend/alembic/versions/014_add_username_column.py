"""添加 username 列到 users 表

Revision ID: 014_add_username_column
Revises: 013_add_rbac_fields
Create Date: 2026-05-11

此迁移添加:
1. users.username 列（用于支持用户名字段）
"""
from alembic import op
import sqlalchemy as sa


revision = '014_add_username_column'
down_revision = '013_add_rbac_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'username', sa.String(), nullable=True
    ))
    op.create_index(op.f('ix_users_username'), 'users', ['username'])
    op.create_unique_constraint('uq_users_username', 'users', ['username'])

    # 将现有 email 同步到 username（为空的行使用 email 前缀）
    op.execute("""
        UPDATE users SET username = email WHERE username IS NULL AND email IS NOT NULL
    """)
    op.execute("""
        UPDATE users SET username = 'user_' || substr(id::text, 1, 8) WHERE username IS NULL
    """)

    # 改为不可空
    op.alter_column('users', 'username',
                    existing_type=sa.String(),
                    nullable=False)


def downgrade() -> None:
    op.drop_constraint('uq_users_username', 'users', type_='unique')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_column('users', 'username')