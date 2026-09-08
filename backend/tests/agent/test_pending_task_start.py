"""D2: POST /agent-tasks/{task_id}/start —— pending 搁浅任务启动入口。

生产实证（d177cc5c 类任务）：调度丢失后任务永久 pending，系统无恢复入口。
本端点仅接受 status=pending 的任务：
- pending → 状态翻 RUNNING 并经 _launch_task_bg 调度 _execute_agent_task；
- 非 pending（running/paused/completed...）→ 400；
- 任务不存在 → 404；无项目权限 → ProjectAccessDenied(404)；
- registry 仍有存活证据（任务实际在启动/运行中）→ 400 防重复启动；
- 并发重复启动（slot 占位冲突）→ 409。

防重入原子性（审查 Important-1/2）：slot 占位（检查+add）在端点函数体第一行
同步完成——与后续 await registry/db 检查之间无让出点，两并发请求不可能同穿。
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


def _make_launch_mock():
    """_launch_task_bg 桩：捕获调用并 close 传入协程，防 never-awaited 警告。"""
    mock = MagicMock()

    def _capture(coro, task_name=None):
        coro.close()
        return MagicMock()

    mock.side_effect = _capture
    return mock


@pytest.fixture(autouse=True)
def _common_mocks(monkeypatch):
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())
    monkeypatch.setattr(module, "_is_task_alive_in_registry", AsyncMock(return_value=False))
    monkeypatch.setattr(module, "_running_asyncio_tasks", {})
    monkeypatch.setattr(module, "_task_start_slots", set())
    monkeypatch.setattr(module, "_early_heartbeat_tasks", {})
    monkeypatch.setattr(module, "_reset_task_to_pending_after_start_failure", AsyncMock())


_USER = SimpleNamespace(id="user-1", role=None)


@pytest.mark.asyncio
async def test_start_pending_task_launches_execution(monkeypatch):
    """pending 任务 → 状态翻 running、_launch_task_bg 以 start-<id> 调度。"""
    task = _make_task(status="pending")
    db = _wire_db(task, _make_project())
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    result = await module.start_agent_task("task-1", db=db, current_user=_USER)

    assert task.status == "running"
    launch_mock.assert_called_once()
    assert launch_mock.call_args.kwargs.get("task_name") == "start-task-1"
    assert result["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_start_running_task_rejected(monkeypatch):
    """running 任务 → 400，不调度；失败后 slot 不残留。"""
    task = _make_task(status="running")
    db = _wire_db(task, _make_project())
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task("task-1", db=db, current_user=_USER)
    assert exc.value.status_code == 400
    launch_mock.assert_not_called()
    assert module._task_start_slots == set()


@pytest.mark.asyncio
async def test_start_paused_task_rejected(monkeypatch):
    """paused 任务走 resume 通道，start → 400。"""
    task = _make_task(status="paused")
    db = _wire_db(task, _make_project())
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task("task-1", db=db, current_user=_USER)
    assert exc.value.status_code == 400
    launch_mock.assert_not_called()
    assert module._task_start_slots == set()


@pytest.mark.asyncio
async def test_start_missing_task_returns_404(monkeypatch):
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task("missing", db=db, current_user=_USER)
    assert exc.value.status_code == 404
    launch_mock.assert_not_called()
    # 404 路径同样必须释放同步占位
    assert module._task_start_slots == set()


@pytest.mark.asyncio
async def test_start_task_without_permission_denied(monkeypatch):
    """非 owner 且非 SUPER_ADMIN → ProjectAccessDenied(404)，不调度。"""
    task = _make_task(status="pending")
    db = _wire_db(task, _make_project(owner_id="user-1"))
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    with pytest.raises(ProjectAccessDenied):
        await module.start_agent_task(
            "task-1", db=db, current_user=SimpleNamespace(id="user-2", role=None)
        )
    launch_mock.assert_not_called()
    assert module._task_start_slots == set()


@pytest.mark.asyncio
async def test_start_task_already_alive_rejected(monkeypatch):
    """registry 仍有存活证据（任务实际在启动/运行中）→ 400 防重复启动。"""
    task = _make_task(status="pending")
    db = _wire_db(task, _make_project())
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)
    monkeypatch.setattr(module, "_is_task_alive_in_registry", AsyncMock(return_value=True))

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task("task-1", db=db, current_user=_USER)
    assert exc.value.status_code == 400
    launch_mock.assert_not_called()
    # 状态不得被翻转，避免任务被误判为 running 后又无人执行
    assert task.status == "pending"
    # registry 拒绝路径必须释放同步占位
    assert module._task_start_slots == set()


@pytest.mark.asyncio
async def test_start_task_slot_held_returns_409_without_db_access(monkeypatch):
    """本进程已有同任务启动协程在途 → 409；同步段拒绝，不访问 DB/registry。"""
    db = AsyncMock()
    db.get = AsyncMock()
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)
    monkeypatch.setattr(module, "_task_start_slots", {"task-1"})

    with pytest.raises(HTTPException) as exc:
        await module.start_agent_task("task-1", db=db, current_user=_USER)
    assert exc.value.status_code == 409
    launch_mock.assert_not_called()
    db.get.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_start_only_one_launches(monkeypatch):
    """两并发 start 同一 pending 任务：恰一个成功 launch，另一个 409（不双跑）。"""
    task = _make_task(status="pending")
    # 成功者消耗两次 db.get（task、project）；409 者在同步段拒绝、不访问 DB
    db = _wire_db(task, _make_project())
    launch_mock = _make_launch_mock()
    monkeypatch.setattr(module, "_launch_task_bg", launch_mock)

    results = await asyncio.gather(
        module.start_agent_task("task-1", db=db, current_user=_USER),
        module.start_agent_task("task-1", db=db, current_user=_USER),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, dict)]
    conflicts = [
        r for r in results
        if isinstance(r, HTTPException) and r.status_code == 409
    ]
    assert len(successes) == 1
    assert len(conflicts) == 1
    launch_mock.assert_called_once()
    assert task.status == "running"


@pytest.mark.asyncio
async def test_guarded_execute_releases_slot_on_early_failure(monkeypatch):
    """_guarded_execute_task：协程早段异常 → cancel 泄漏的 early heartbeat、
    回置 pending、释放 slot 并 re-raise（_launch_task_bg 的 done_callback 落日志）。"""
    monkeypatch.setattr(
        module, "_execute_agent_task", AsyncMock(side_effect=RuntimeError("sandbox init boom"))
    )
    module._task_start_slots.add("task-1")
    hb_task = MagicMock()
    hb_task.done.return_value = False
    module._early_heartbeat_tasks["task-1"] = hb_task

    with pytest.raises(RuntimeError, match="sandbox init boom"):
        await module._guarded_execute_task("task-1")

    hb_task.cancel.assert_called_once()
    module._reset_task_to_pending_after_start_failure.assert_awaited_once_with("task-1")
    assert "task-1" not in module._task_start_slots
    assert "task-1" not in module._early_heartbeat_tasks


@pytest.mark.asyncio
async def test_guarded_execute_releases_slot_on_success(monkeypatch):
    """正常执行结束 → 释放 slot（无 early heartbeat 残留时不报错）。"""
    execute_mock = AsyncMock()
    monkeypatch.setattr(module, "_execute_agent_task", execute_mock)
    module._task_start_slots.add("task-1")

    await module._guarded_execute_task("task-1")

    execute_mock.assert_awaited_once_with("task-1")
    assert "task-1" not in module._task_start_slots
    module._reset_task_to_pending_after_start_failure.assert_not_called()
