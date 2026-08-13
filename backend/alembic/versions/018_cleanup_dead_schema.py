"""cleanup dead multi-tenancy schema and projects dead columns

Drops the half-baked multi-tenancy artifacts (tenants table + tenant_id on
6 tables, zero code references) and projects dead columns (status /
is_deleted / deleted_at, superseded by is_active).

Revision ID: 018_cleanup_dead_schema
Revises: 017_vco
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = "018_cleanup_dead_schema"
down_revision = "017_vco"
branch_labels = None
depends_on = None


_TENANT_TABLES = [
    "audit_tasks",
    "agent_findings",
    "user_configs",
    "agent_tasks",
    "projects",
    "users",
]


def upgrade():
    # 1. Drop foreign key on users.tenant_id -> tenants.id
    op.drop_constraint("fk_users_tenant", "users", type_="foreignkey")

    # 2. Drop tenant indexes
    op.drop_index("idx_users_tenant", table_name="users")
    op.drop_index("idx_projects_tenant", table_name="projects")
    op.drop_index("idx_agent_tasks_tenant", table_name="agent_tasks")

    # 3. Drop tenant_id columns (reverse order so users is last)
    for table in _TENANT_TABLES:
        op.drop_column(table, "tenant_id")

    # 4. Drop tenants table
    op.drop_table("tenants")

    # 5. Drop projects dead columns (superseded by is_active)
    op.drop_column("projects", "is_deleted")
    op.drop_column("projects", "deleted_at")
    op.drop_column("projects", "status")


def downgrade():
    # 1. Restore projects dead columns
    op.add_column(
        "projects",
        sa.Column("status", sa.String(), server_default="active", nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
    )

    # 2. Recreate tenants table
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO tenants (id, name, status) VALUES (0, 'SuperAdmin', 'active')")

    # 3. Restore tenant_id columns
    for table in reversed(_TENANT_TABLES):
        op.add_column(
            table,
            sa.Column("tenant_id", sa.Integer(), server_default="0", nullable=False),
        )

    # 4. Recreate indexes
    op.create_index("idx_agent_tasks_tenant", "agent_tasks", ["tenant_id"])
    op.create_index("idx_projects_tenant", "projects", ["tenant_id"])
    op.create_index("idx_users_tenant", "users", ["tenant_id"])

    # 5. Recreate foreign key
    op.create_foreign_key(
        "fk_users_tenant", "users", "tenants", ["tenant_id"], ["id"]
    )
