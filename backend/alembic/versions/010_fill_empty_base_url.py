"""Fill empty llmBaseUrl in user_configs

Revision ID: 010_fill_empty_base_url
Revises: 009_add_security_controls
Create Date: 2026-05-06
"""
from alembic import op
from sqlalchemy import text
import json

revision = '010_fill_empty_base_url'
down_revision = '009_add_security_controls'
branch_labels = None
depends_on = None

DEFAULT_BASE_URL = "https://api.openai.com"


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(
        text("SELECT id, llm_config FROM user_configs")
    ).fetchall()

    updated = 0
    for row in rows:
        try:
            config = json.loads(row.llm_config) if row.llm_config else {}
        except (json.JSONDecodeError, TypeError):
            config = {}

        if not config.get("llmBaseUrl"):
            config["llmBaseUrl"] = DEFAULT_BASE_URL
            connection.execute(
                text("UPDATE user_configs SET llm_config = :config WHERE id = :id"),
                {"config": json.dumps(config), "id": row.id}
            )
            updated += 1

    print(f"Migration 010: 已填充 {updated} 条空 llmBaseUrl 为 {DEFAULT_BASE_URL}")


def downgrade():
    connection = op.get_bind()
    rows = connection.execute(
        text("SELECT id, llm_config FROM user_configs")
    ).fetchall()

    reverted = 0
    for row in rows:
        try:
            config = json.loads(row.llm_config) if row.llm_config else {}
        except (json.JSONDecodeError, TypeError):
            config = {}

        if config.get("llmBaseUrl") == DEFAULT_BASE_URL:
            config["llmBaseUrl"] = ""
            connection.execute(
                text("UPDATE user_configs SET llm_config = :config WHERE id = :id"),
                {"config": json.dumps(config), "id": row.id}
            )
            reverted += 1

    print(f"Rollback 010: 已恢复 {reverted} 条默认 llmBaseUrl 为空")
