"""添加 RBAC 权限字段

Revision ID: 013_add_rbac_fields
Revises: 012_add_security_fields
Create Date: 2026-05-08

此迁移添加:
1. users.parent_admin_id - 所属管理员ID（外键自引用）
2. 修改 users.role 默认值为 "user"
3. 修改 users.email 为可空
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '013_add_rbac_fields'
down_revision = '012_add_security_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 添加 parent_admin_id 列
    op.add_column('users', sa.Column(
        'parent_admin_id', sa.String(), nullable=True
    ))
    op.create_index(
        op.f('ix_users_parent_admin_id'),
        'users', ['parent_admin_id']
    )
    op.create_foreign_key(
        'fk_users_parent_admin_id_users',
        'users', 'users',
        ['parent_admin_id'], ['id'],
        ondelete='SET NULL'
    )

    # 2. 修改 email 为可空（RBAC 中 email 可选）
    op.alter_column('users', 'email',
                    existing_type=sa.String(),
                    nullable=True)

    # 3. 修改现有用户的 role（将 member -> user, admin -> admin）
    op.execute("""
        UPDATE users SET role = 'user' WHERE role = 'member'
    """)
    op.execute("""
        UPDATE users SET role = 'super_admin' WHERE is_superuser = true AND role != 'super_admin'
    """)


def downgrade() -> None:
    # 1. 删除外键和索引
    op.drop_constraint('fk_users_parent_admin_id_users', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_parent_admin_id'), table_name='users')
    op.drop_column('users', 'parent_admin_id')

    # 2. 恢复 email 为非空
    op.alter_column('users', 'email',
                    existing_type=sa.String(),
                    nullable=False)

    # 3. 恢复 role
    op.execute("""
        UPDATE users SET role = 'member' WHERE role = 'user'
    """)
