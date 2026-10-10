"""P10-T4 回归：手工 PoC reverify 端点更新 finding 后必须重算任务计数器。

旧实现（agent_tasks.py reverify_finding）：finding.verification_status/
is_verified 更新后直接 db.commit()，不调用
_recalc_task_counters_from_db——agent_tasks.verified_count 等计数器与
agent_findings 表陈旧不一致（对比正常完成路径在 :1397 有重算）。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import agent_tasks
from app.api.v1.endpoints.agent_tasks import reverify_finding


def _make_finding():
    finding = MagicMock()
    finding.task_id = "task-1"
    # 有成功历史命令 → 走 replay，绕过 poc_code AST 校验
    finding.sandbox_attempts = [{
        "success": True,
        "command": "python3 /tmp/poc_0.py",
    }]
    finding.poc_code = ""
    finding.verification_status = "not_reproducible"
    return finding


def _patch_sandbox(monkeypatch):
    class _FakeManager:
        def __init__(self, config=None):
            self.is_available = True

        async def initialize(self):
            return None

        async def execute_poc(self, **kwargs):
            return {
                "success": True,
                "exit_code": 0,
                "stdout": "VULNERABILITY_CONFIRMED",
                "stderr": "",
            }

    monkeypatch.setattr(
        "app.services.agent.tools.sandbox_tool.SandboxManager", _FakeManager
    )


async def _invoke(monkeypatch, compute_return):
    monkeypatch.setattr(agent_tasks, "assert_can_access_project", lambda u, p: None)
    monkeypatch.setattr(agent_tasks.os.path, "isdir", lambda p: True)
    _patch_sandbox(monkeypatch)
    monkeypatch.setattr(
        "app.services.agent.agents.verification.compute_verification_status",
        lambda *a, **kw: compute_return,
    )

    task = MagicMock()
    task.id = "task-1"
    task.project_id = "proj-1"
    project = MagicMock()
    finding = _make_finding()

    db = AsyncMock()
    db.get.side_effect = [task, project, finding]

    recalc = AsyncMock()

    async def _recalc_after_commit(_db, _task, _task_id):
        # 证明重算发生在首次 commit 之后（函数要求 finding 已落库）
        assert db.commit.await_count >= 1

    recalc.side_effect = _recalc_after_commit
    monkeypatch.setattr(agent_tasks, "_recalc_task_counters_from_db", recalc)

    response = await reverify_finding(
        task_id="task-1",
        finding_id="finding-1",
        db=db,
        current_user=MagicMock(),
    )
    return db, task, recalc, response


class TestReverifyRecalcCounters:
    @pytest.mark.asyncio
    async def test_confirmed_rerun_recalcs_counters(self, monkeypatch):
        """reverify 判 confirmed：verified_count 等计数器必须重算并二次提交"""
        db, task, recalc, response = await _invoke(
            monkeypatch, ("confirmed", True, {})
        )
        assert response["verification_status"] == "confirmed"
        recalc.assert_awaited_once_with(db, task, "task-1")
        assert db.commit.await_count == 2

    @pytest.mark.asyncio
    async def test_static_confirmed_rerun_recalcs_counters(self, monkeypatch):
        """reverify 判 static_confirmed：新口径下计数器同样必须重算"""
        db, task, recalc, response = await _invoke(
            monkeypatch, ("static_confirmed", True, {})
        )
        assert response["verification_status"] == "static_confirmed"
        recalc.assert_awaited_once_with(db, task, "task-1")
        assert db.commit.await_count == 2
