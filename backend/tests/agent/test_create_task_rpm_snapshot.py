"""create_agent_task RPM 快照持久化测试。

对应 spec delta llm-adapter:
- Scenario: 任务创建时从 UserConfig 快照 RPM
- Scenario: 无用户配置时 RPM 用默认值
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.endpoints import agent_tasks as module
from app.api.v1.endpoints.config import (
    decrypt_config,
    SENSITIVE_LLM_FIELDS,
    SENSITIVE_OTHER_FIELDS,
)


def _make_project():
    return SimpleNamespace(id="project-1", owner_id="user-1", name="proj")


def _make_request():
    return module.AgentTaskCreate(
        project_id="project-1",
        name="test",
        description="d",
    )


@pytest.mark.asyncio
async def test_create_agent_task_persists_rpm_from_user_config(monkeypatch):
    """UserConfig.otherConfig.llmRatePerMinute=5 → 创建任务后 task.agent_config 含 5。"""
    project = _make_project()
    db = AsyncMock()
    db.get = AsyncMock(return_value=project)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr(
        module,
        "_get_user_config",
        AsyncMock(return_value={"otherConfig": {"llmRatePerMinute": 5}}),
    )
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())

    captured = {}

    original_init = module.AgentTask.__init__

    def spy_init(self, *args, **kwargs):
        captured["agent_config"] = kwargs.get("agent_config")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(module.AgentTask, "__init__", spy_init)

    current_user = SimpleNamespace(id="user-1")
    request = _make_request()
    background_tasks = MagicMock()

    await module.create_agent_task(request, background_tasks, db=db, current_user=current_user)

    assert captured["agent_config"] == {"llm_rate_per_minute": 5}


@pytest.mark.asyncio
async def test_create_agent_task_defaults_when_no_user_config(monkeypatch):
    """无 UserConfig → task.agent_config 为 None。"""
    project = _make_project()
    db = AsyncMock()
    db.get = AsyncMock(return_value=project)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr(module, "_get_user_config", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())

    captured = {}
    original_init = module.AgentTask.__init__

    def spy_init(self, *args, **kwargs):
        captured["agent_config"] = kwargs.get("agent_config")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(module.AgentTask, "__init__", spy_init)

    current_user = SimpleNamespace(id="user-1")
    request = _make_request()
    background_tasks = MagicMock()

    await module.create_agent_task(request, background_tasks, db=db, current_user=current_user)

    assert captured["agent_config"] is None
