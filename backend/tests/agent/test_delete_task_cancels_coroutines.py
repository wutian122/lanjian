"""#7 修复回归测试：删除终态任务前取消仍在收尾的后台协程。

E2E 实证（A 机 backend 日志 14:01）：任务已删（agent_tasks 行级联删除）后，
后台仍有一轮 verification 在运行并尝试写事件 → ForeignKeyViolationError
（agent_events.task_id not present in agent_tasks）。根因：DB 状态已终态但
asyncio 协程尚未退出，"状态守卫"只看 DB status 不看运行中协程。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.v1.endpoints.agent_tasks as at_module
from app.api.v1.endpoints.agent_tasks import delete_agent_task
from app.models.agent_task import AgentTaskStatus


def _make_db(task):
    db = AsyncMock()

    async def fake_get(model, pk):
        if model.__name__ == "AgentTask":
            return task
        if model.__name__ == "Project":
            return SimpleNamespace(id="p1", source_type="zip")
        raise AssertionError(f"unexpected get: {model}")

    db.get.side_effect = fake_get
    return db


def test_delete_cancels_lingering_coroutines():
    """终态任务删除时：orchestrator 取消传播 + asyncio 协程被 cancel 并等待退出。"""
    task = SimpleNamespace(
        id="t1", project_id="p1", status=AgentTaskStatus.COMPLETED_WITH_GAPS
    )
    db = _make_db(task)
    orchestrator = MagicMock()

    async def _inner():
        orch_task = asyncio.create_task(asyncio.sleep(30))
        with patch.object(at_module, "_running_orchestrators", {"t1": orchestrator}), \
             patch.object(at_module, "_running_asyncio_tasks", {"t1": orch_task}), \
             patch.object(
                 at_module, "cleanup_agent_task_resources",
                 new=AsyncMock(return_value={"cleaned": True}),
             ), \
             patch.object(at_module, "assert_can_access_project"):
            resp = await delete_agent_task("t1", db=db, current_user=None)
        return resp, orch_task

    resp, orch_task = asyncio.run(_inner())
    assert resp == {"cleaned": True}
    orchestrator.cancel.assert_called_once()
    assert orch_task.cancelled()


def test_delete_without_lingering_coroutines_still_cleans():
    """无后台协程时删除流程不受影响。"""
    task = SimpleNamespace(
        id="t2", project_id="p1", status=AgentTaskStatus.COMPLETED_WITH_GAPS
    )
    db = _make_db(task)

    async def _inner():
        with patch.object(at_module, "_running_orchestrators", {}), \
             patch.object(at_module, "_running_asyncio_tasks", {}), \
             patch.object(
                 at_module, "cleanup_agent_task_resources",
                 new=AsyncMock(return_value={"cleaned": True}),
             ), \
             patch.object(at_module, "assert_can_access_project"):
            return await delete_agent_task("t2", db=db, current_user=None)

    resp = asyncio.run(_inner())
    assert resp == {"cleaned": True}


def test_delete_running_task_still_rejected():
    """运行中任务仍被 400 拒绝（既有语义不回退）。"""
    task = SimpleNamespace(
        id="t3", project_id="p1", status=AgentTaskStatus.RUNNING
    )
    db = _make_db(task)

    async def _inner():
        with patch.object(at_module, "assert_can_access_project"):
            return await delete_agent_task("t3", db=db, current_user=None)

    with pytest.raises(Exception) as e:
        asyncio.run(_inner())
    assert getattr(e.value, "status_code", None) == 400
