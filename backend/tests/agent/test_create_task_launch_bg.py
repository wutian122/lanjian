"""D1: 创建端点必须经 _launch_task_bg 调度执行协程。

生产实证（任务 d177cc5c）：Starlette ``background_tasks.add_task`` 在 worker
忙于前序任务时会丢失调度（连入口日志都没有），任务永久 pending。创建端点改用
``_launch_task_bg``（强引用 + 异常 logger.exception 的 fire-and-forget 包装）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.endpoints import agent_tasks as module


def _make_project():
    return SimpleNamespace(id="project-1", owner_id="user-1", name="proj")


def _make_request():
    return module.AgentTaskCreate(
        project_id="project-1",
        name="test",
        description="d",
    )


@pytest.mark.asyncio
async def test_create_agent_task_schedules_execution_via_launch_task_bg(monkeypatch):
    """创建任务后 _launch_task_bg 被调用（task_name=execute-<id>），不再走 BackgroundTasks。"""
    project = _make_project()
    db = AsyncMock()
    db.get = AsyncMock(return_value=project)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr(module, "uuid4", lambda: "fixed-task-id")
    monkeypatch.setattr(module, "_get_user_config", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    # 端点签名已移除 background_tasks 形参；若仍依赖 BackgroundTasks 调度，此调用失败
    await module.create_agent_task(
        _make_request(),
        db=db,
        current_user=SimpleNamespace(id="user-1"),
    )

    launch_mock.assert_called_once()
    assert launch_mock.call_args.kwargs.get("task_name") == "execute-fixed-task-id"
