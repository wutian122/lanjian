"""D1: 创建端点必须经 _launch_task_bg 调度执行协程。

生产实证（任务 d177cc5c）：Starlette ``background_tasks.add_task`` 在 worker
忙于前序任务时会丢失调度（连入口日志都没有），任务永久 pending。创建端点改用
``_launch_task_bg``（强引用 + 异常 logger.exception 的 fire-and-forget 包装）。

审查 Important-2：创建路径同样占 _task_start_slots——协程被事件循环延迟期间
（early heartbeat 未刷、_running_asyncio_tasks 要到沙箱/RAG 后才注册，可达
10+ 分钟）用户走 /start 恢复会三闸全空双跑。
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


def _wire_db():
    db = AsyncMock()
    db.get = AsyncMock(return_value=_make_project())
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _make_launch_mock():
    """捕获 _launch_task_bg 调用并 close 传入协程（防 never-awaited 警告）。"""
    mock = MagicMock()

    def _capture(coro, task_name=None):
        coro.close()
        return MagicMock()

    mock.side_effect = _capture
    return mock


@pytest.mark.asyncio
async def test_create_agent_task_schedules_execution_via_launch_task_bg(monkeypatch):
    """创建任务后 _launch_task_bg 被调用（task_name=execute-<id>），不再走 BackgroundTasks。"""
    monkeypatch.setattr(module, "uuid4", lambda: "fixed-task-id")
    monkeypatch.setattr(module, "_get_user_config", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())
    monkeypatch.setattr(module, "_task_start_slots", set())
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    # 端点签名已移除 background_tasks 形参；若仍依赖 BackgroundTasks 调度，此调用失败
    await module.create_agent_task(
        _make_request(),
        db=_wire_db(),
        current_user=SimpleNamespace(id="user-1"),
    )

    launch_mock.assert_called_once()
    assert launch_mock.call_args.kwargs.get("task_name") == "execute-fixed-task-id"


@pytest.mark.asyncio
async def test_create_agent_task_acquires_start_slot_before_launch(monkeypatch):
    """创建路径在 _launch_task_bg 前同步占 slot：launch 瞬间 slot 必须已含新任务 id
    （覆盖协程被事件循环延迟、early heartbeat 未刷的双跑窗口）。"""
    monkeypatch.setattr(module, "uuid4", lambda: "fixed-task-id")
    monkeypatch.setattr(module, "_get_user_config", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())
    slots = set()
    monkeypatch.setattr(module, "_task_start_slots", slots)
    launch_mock = _make_launch_mock()
    slot_seen_at_launch = {}

    def _capture(coro, task_name=None):
        slot_seen_at_launch["held"] = "fixed-task-id" in slots
        coro.close()
        return MagicMock()

    launch_mock.side_effect = _capture
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    await module.create_agent_task(
        _make_request(),
        db=_wire_db(),
        current_user=SimpleNamespace(id="user-1"),
    )

    launch_mock.assert_called_once()
    assert slot_seen_at_launch["held"] is True
