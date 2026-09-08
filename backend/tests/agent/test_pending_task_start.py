"""D2: POST /agent-tasks/{task_id}/start —— pending 搁浅任务启动入口。

生产实证（d177cc5c 类任务）：调度丢失后任务永久 pending，系统无恢复入口。
本端点仅接受 status=pending 的任务：
- pending → 状态翻 RUNNING 并经 _launch_task_bg 调度 _execute_agent_task；
- 非 pending（running/paused/completed...）→ 400；
- 任务不存在 → 404；无项目权限 → ProjectAccessDenied(404)；
- registry 仍有存活证据（任务实际在启动/运行中）→ 400 防重复启动。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.endpoints import agent_tasks as module
from app.core.rbac import ProjectAccessDenied


def _make_task(status="pending"):
    return SimpleNamespace(
        id="task-1",
        project_id="project-1",
        status=status,
        paused=False,
        paused_at=None,
        pause_reason=None,
        last_error_code=None,
        resume_count=0,
    )


def _make_project(owner_id="user-1"):
    return SimpleNamespace(id="project-1", owner_id=owner_id, name="proj")


def _wire_db(task, project):
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[task, project])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture(autouse=True)
def _common_mocks(monkeypatch):
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())
    monkeypatch.setattr(module, "_is_task_alive_in_registry", AsyncMock(return_value=False))
    monkeypatch.setattr(module, "_running_asyncio_tasks", {})
    monkeypatch.setattr(module, "_task_start_slots", set())


@pytest.mark.asyncio
async def test_start_pending_task_launches_execution(monkeypatch):
    """pending 任务 → 状态翻 running、_launch_task_bg 以 start-<id> 调度。"""
    task = _make_task(status="pending")
    db = _wire_db(task, _make_project())
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    result = await module.start_agent_task(
        "task-1", db=db, current_user=SimpleNamespace(id="user-1", role=None)
    )

    assert task.status == "running"
    launch_mock.assert_called_once()
    assert launch_mock.call_args.kwargs.get("task_name") == "start-task-1"
    assert result["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_start_running_task_rejected(monkeypatch):
    """running 任务 → 400，不调度。"""
    task = _make_task(status="running")
    db = _wire_db(task, _make_project())
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task(
            "task-1", db=db, current_user=SimpleNamespace(id="user-1", role=None)
        )
    assert exc.value.status_code == 400
    launch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_paused_task_rejected(monkeypatch):
    """paused 任务走 resume 通道，start → 400。"""
    task = _make_task(status="paused")
    db = _wire_db(task, _make_project())
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task(
            "task-1", db=db, current_user=SimpleNamespace(id="user-1", role=None)
        )
    assert exc.value.status_code == 400
    launch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_missing_task_returns_404(monkeypatch):
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task(
            "missing", db=db, current_user=SimpleNamespace(id="user-1", role=None)
        )
    assert exc.value.status_code == 404
    launch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_task_without_permission_denied(monkeypatch):
    """非 owner 且非 SUPER_ADMIN → ProjectAccessDenied(404)，不调度。"""
    task = _make_task(status="pending")
    db = _wire_db(task, _make_project(owner_id="user-1"))
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(ProjectAccessDenied):
        await module.start_agent_task(
            "task-1", db=db, current_user=SimpleNamespace(id="user-2", role=None)
        )
    launch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_task_already_alive_rejected(monkeypatch):
    """registry 仍有存活证据（任务实际在启动/运行中）→ 400 防重复启动。"""
    task = _make_task(status="pending")
    db = _wire_db(task, _make_project())
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)
    monkeypatch.setattr(module, "_is_task_alive_in_registry", AsyncMock(return_value=True))

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task(
            "task-1", db=db, current_user=SimpleNamespace(id="user-1", role=None)
        )
    assert exc.value.status_code == 400
    launch_mock.assert_not_called()
    # 状态不得被翻转，避免任务被误判为 running 后又无人执行
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_start_task_in_process_slot_rejected(monkeypatch):
    """本进程已有同任务启动协程在途（early heartbeat 尚未刷上 registry 的亚秒窗口）→ 400。"""
    task = _make_task(status="pending")
    db = _wire_db(task, _make_project())
    launch_mock = MagicMock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)
    monkeypatch.setattr(module, "_task_start_slots", {"task-1"})

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task(
            "task-1", db=db, current_user=SimpleNamespace(id="user-1", role=None)
        )
    assert exc.value.status_code == 400
    launch_mock.assert_not_called()
