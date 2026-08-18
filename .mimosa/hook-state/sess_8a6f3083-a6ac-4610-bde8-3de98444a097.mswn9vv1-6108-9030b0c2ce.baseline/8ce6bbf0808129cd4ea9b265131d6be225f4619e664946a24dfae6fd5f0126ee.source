"""drop dead tables from repo cleanup v3.6.0

移除以下未使用的表（自 v3.1 Fusion 合入以来从未被任何服务、API、Agent 逻辑读写）：

- security_controls / sensitive_operations / operation_required_controls / language_adapters
  （services/controls/config_loader.py 唯一存在处即定义处，无消费者，YAML 数据目录不存在）
- coverage_tracks
  （实际覆盖率由 services/agent/core/coverage.py 在内存 + AgentTask.metadata 里实现，D1-D10 十维度矩阵与 DB 表无关）

downgrade 保留占位以通过 CI 校验；若要真回滚，参考 009_add_security_controls.py 的 create_table 语句。

Revision ID: 023_drop_dead
Revises: 022_sli
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa

revision = "023_drop_dead"
down_revision = "022_sli"
branch_labels = None
depends_on = None


def upgrade():
    # 顺序：先删依赖表（含 FK），再删被依赖表
    op.execute("DROP TABLE IF EXISTS operation_required_controls CASCADE")
    op.execute("DROP TABLE IF EXISTS sensitive_operations CASCADE")
    op.execute("DROP TABLE IF EXISTS security_controls CASCADE")
    op.execute("DROP TABLE IF EXISTS language_adapters CASCADE")
    op.execute("DROP TABLE IF EXISTS coverage_tracks CASCADE")


def downgrade():
    # 占位：数据已丢弃，仅还原空表结构以通过 CI 校验
    op.create_table(
        "security_controls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("control_id", sa.String(50), unique=True, index=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_zh", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), server_default="MEDIUM"),
        sa.Column("cwe", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "sensitive_operations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, index=True, nullable=False),
        sa.Column("name_zh", sa.String(200), nullable=False),
        sa.Column("risk_level", sa.String(20), server_default="MEDIUM"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("patterns", sa.JSON(), server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "operation_required_controls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("operation_id", sa.String(), sa.ForeignKey("sensitive_operations.id"), nullable=False),
        sa.Column("control_id", sa.String(50), nullable=False),
    )
    op.create_table(
        "language_adapters",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("language", sa.String(50), unique=True, index=True, nullable=False),
        sa.Column("adapter_data", sa.JSON(), nullable=False),
        sa.Column("file_extensions", sa.JSON(), server_default="[]"),
        sa.Column("framework_count", sa.Integer(), server_default="0"),
        sa.Column("control_pattern_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "coverage_tracks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("audit_tasks.id"), nullable=False, index=True),
        sa.Column("dimension", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), server_default="uncovered"),
        sa.Column("findings_count", sa.Integer(), server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
