"""#6 修复回归测试：/projects/stats 聚合 agent_tasks。

E2E 实证：nginx agent 审计任务运行中时，仪表盘"运行中任务: 0"——Dashboard 取
``GET /projects/stats``（只统计传统 AuditTask）与 ``GET /tasks/``，agent 任务体系
完全不在统计口径内；而 /audit-tasks 页正确显示 1 运行中。两套任务体系口径不一致。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.database import get_database_stats
from app.models.agent_task import AgentTask, AgentTaskStatus


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


def _fake_execute_for_stats(*, trad_tasks, agent_tasks):
    async def fake_execute(stmt):
        text = str(stmt)
        if "FROM projects" in text:
            return _Result([SimpleNamespace(id="p1", is_active=True)])
        if "FROM audit_tasks" in text:
            return _Result(trad_tasks)
        if "FROM agent_tasks" in text or "JOIN projects" in text:
            return _Result(agent_tasks)
        if "FROM audit_issues" in text:
            return _Result([])
        if "instant_analyses" in text:
            return _Result([])
        if "project_members" in text:
            return _Result([])
        if "user_configs" in text:
            return _Result([None])
        raise AssertionError(f"unexpected statement: {text}")

    return fake_execute


def test_stats_aggregate_agent_tasks():
    """agent 任务计入 running/total/completed 统计。"""
    from app.models.audit import AuditTask as TraditionalTask

    trad = [
        SimpleNamespace(id="a1", status="running"),
        SimpleNamespace(id="a2", status="completed"),
    ]
    agents = [
        AgentTask(id="g1", project_id="p1", status=AgentTaskStatus.RUNNING),
        AgentTask(id="g2", project_id="p1", status=AgentTaskStatus.COMPLETED_WITH_GAPS),
        AgentTask(id="g3", project_id="p1", status=AgentTaskStatus.VERIFYING),
    ]
    db = AsyncMock()
    db.execute.side_effect = _fake_execute_for_stats(trad_tasks=trad, agent_tasks=agents)

    resp = asyncio.run(
        get_database_stats(db=db, current_user=SimpleNamespace(id="u1"))
    )

    # 传统 1 running + agent 2 running(RUNNING+VERIFYING) = 3
    assert resp.running_tasks == 3
    # 传统 2 + agent 3 = 5
    assert resp.total_tasks == 5
    # 传统 1 completed + agent 1 completed = 2
    assert resp.completed_tasks == 2


def test_stats_no_agent_tasks_still_consistent():
    """无 agent 任务时统计与旧口径一致。"""
    trad = [SimpleNamespace(id="a1", status="running")]
    db = AsyncMock()
    db.execute.side_effect = _fake_execute_for_stats(trad_tasks=trad, agent_tasks=[])

    resp = asyncio.run(
        get_database_stats(db=db, current_user=SimpleNamespace(id="u1"))
    )
    assert resp.running_tasks == 1
    assert resp.total_tasks == 1
