from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.endpoints import agent_tasks as module
from app.models.agent_task import AgentTaskStatus


def _make_task(status: str = AgentTaskStatus.RUNNING) -> SimpleNamespace:
    return SimpleNamespace(
        id="task-1",
        project_id="project-1",
        status=status,
        paused=(status == AgentTaskStatus.PAUSED),
        paused_at=None,
        pause_reason=None,
        last_error_code=None,
        last_checkpoint_id="ckpt-existing" if status == AgentTaskStatus.PAUSED else None,
        resume_count=0,
        completed_at=None,
    )


def _make_project() -> SimpleNamespace:
    return SimpleNamespace(id="project-1", owner_id="user-1")


@pytest.mark.asyncio
async def test_pause_agent_task_marks_task_paused_and_records_checkpoint(monkeypatch):
    task = _make_task()
    project = _make_project()

    db = AsyncMock()
    db.get = AsyncMock(side_effect=[task, project])
    db.commit = AsyncMock()

    orchestrator = MagicMock()
    orchestrator.request_pause = AsyncMock(return_value="ckpt-123")
    monkeypatch.setitem(module._running_orchestrators, task.id, orchestrator)

    current_user = SimpleNamespace(id="user-1")

    result = await module.pause_agent_task(task.id, db=db, current_user=current_user)

    assert result["task_id"] == task.id
    assert result["checkpoint_id"] == "ckpt-123"
    assert task.status == AgentTaskStatus.PAUSED
    assert task.paused is True
    assert task.pause_reason == "manual"
    assert task.last_checkpoint_id == "ckpt-123"
    assert task.paused_at is not None
    db.commit.assert_awaited()

    module._running_orchestrators.pop(task.id, None)

@pytest.mark.asyncio
async def test_pause_agent_task_allows_non_running_states(monkeypatch):
    task = _make_task(status=AgentTaskStatus.INDEXING)
    project = _make_project()

    db = AsyncMock()
    db.get = AsyncMock(side_effect=[task, project])
    db.commit = AsyncMock()

    monkeypatch.setitem(module._running_orchestrators, task.id, MagicMock())

    current_user = SimpleNamespace(id="user-1")

    result = await module.pause_agent_task(task.id, db=db, current_user=current_user)

    assert result["task_id"] == task.id
    assert result["checkpoint_id"] is None
    assert task.status == AgentTaskStatus.PAUSED
    assert task.paused is True
    assert task.pause_reason == "manual"
    assert task.paused_at is not None
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_pause_agent_task_allows_running_without_orchestrator(monkeypatch):
    task = _make_task(status=AgentTaskStatus.RUNNING)
    project = _make_project()

    db = AsyncMock()
    db.get = AsyncMock(side_effect=[task, project])
    db.commit = AsyncMock()

    module._running_orchestrators.pop(task.id, None)

    current_user = SimpleNamespace(id="user-1")

    result = await module.pause_agent_task(task.id, db=db, current_user=current_user)

    assert result["task_id"] == task.id
    assert result["checkpoint_id"] is None
    assert task.status == AgentTaskStatus.PAUSED
    assert task.paused is True
    assert task.pause_reason == "manual"
    assert task.paused_at is not None
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_resume_agent_task_uses_last_checkpoint_and_restarts_runner(monkeypatch):
    task = _make_task(status=AgentTaskStatus.PAUSED)
    project = _make_project()

    db = AsyncMock()
    db.get = AsyncMock(side_effect=[task, project])
    db.commit = AsyncMock()

    scheduled = {}

    async def fake_execute(task_id: str, resume_checkpoint_id: str | None = None):
        scheduled["task_id"] = task_id
        scheduled["resume_checkpoint_id"] = resume_checkpoint_id

    fake_asyncio_task = MagicMock()

    def fake_create_task(coro, **kwargs):
        scheduled["coroutine"] = coro
        frame_locals = coro.cr_frame.f_locals if coro.cr_frame else {}
        scheduled["task_id"] = frame_locals.get("task_id")
        scheduled["resume_checkpoint_id"] = frame_locals.get("resume_checkpoint_id")
        coro.close()
        return fake_asyncio_task

    monkeypatch.setattr(module, "_execute_agent_task", fake_execute)
    monkeypatch.setattr(module.asyncio, "create_task", fake_create_task)

    current_user = SimpleNamespace(id="user-1")

    result = await module.resume_agent_task(task.id, db=db, current_user=current_user)

    assert result["task_id"] == task.id
    assert result["checkpoint_id"] == "ckpt-existing"
    assert task.status == AgentTaskStatus.RUNNING
    assert task.paused is False
    assert task.resume_count == 1
    assert scheduled["task_id"] == task.id
    assert scheduled["resume_checkpoint_id"] == "ckpt-existing"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_resume_agent_task_falls_back_to_latest_checkpoint(monkeypatch):
    task = _make_task(status=AgentTaskStatus.PAUSED)
    task.last_checkpoint_id = None
    project = _make_project()

    checkpoint_result = MagicMock()
    checkpoint_result.scalar_one_or_none.return_value = "ckpt-fallback"

    db = AsyncMock()
    db.get = AsyncMock(side_effect=[task, project])
    db.execute = AsyncMock(return_value=checkpoint_result)
    db.commit = AsyncMock()

    scheduled = {}

    async def fake_execute(task_id: str, resume_checkpoint_id: str | None = None):
        scheduled["task_id"] = task_id
        scheduled["resume_checkpoint_id"] = resume_checkpoint_id

    fake_asyncio_task = MagicMock()

    def fake_create_task(coro, **kwargs):
        scheduled["coroutine"] = coro
        frame_locals = coro.cr_frame.f_locals if coro.cr_frame else {}
        scheduled["task_id"] = frame_locals.get("task_id")
        scheduled["resume_checkpoint_id"] = frame_locals.get("resume_checkpoint_id")
        coro.close()
        return fake_asyncio_task

    monkeypatch.setattr(module, "_execute_agent_task", fake_execute)
    monkeypatch.setattr(module.asyncio, "create_task", fake_create_task)

    current_user = SimpleNamespace(id="user-1")

    result = await module.resume_agent_task(task.id, db=db, current_user=current_user)

    assert result["checkpoint_id"] == "ckpt-fallback"
    assert task.last_checkpoint_id == "ckpt-fallback"
    assert scheduled["task_id"] == task.id
    assert scheduled["resume_checkpoint_id"] == "ckpt-fallback"
    db.execute.assert_awaited()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_resume_agent_task_without_checkpoint_starts_fresh(monkeypatch):
    task = _make_task(status=AgentTaskStatus.PAUSED)
    task.last_checkpoint_id = None
    project = _make_project()

    checkpoint_result = MagicMock()
    checkpoint_result.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.get = AsyncMock(side_effect=[task, project])
    db.execute = AsyncMock(return_value=checkpoint_result)
    db.commit = AsyncMock()

    scheduled = {}

    async def fake_execute(task_id: str, resume_checkpoint_id: str | None = None):
        scheduled["task_id"] = task_id
        scheduled["resume_checkpoint_id"] = resume_checkpoint_id

    fake_asyncio_task = MagicMock()

    def fake_create_task(coro, **kwargs):
        frame_locals = coro.cr_frame.f_locals if coro.cr_frame else {}
        scheduled["task_id"] = frame_locals.get("task_id")
        scheduled["resume_checkpoint_id"] = frame_locals.get("resume_checkpoint_id")
        coro.close()
        return fake_asyncio_task

    monkeypatch.setattr(module, "_execute_agent_task", fake_execute)
    monkeypatch.setattr(module.asyncio, "create_task", fake_create_task)

    current_user = SimpleNamespace(id="user-1")

    result = await module.resume_agent_task(task.id, db=db, current_user=current_user)

    assert result["checkpoint_id"] is None
    assert scheduled["task_id"] == task.id
    assert scheduled["resume_checkpoint_id"] is None
    db.execute.assert_awaited()
    db.commit.assert_awaited()
