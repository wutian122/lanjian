"""#2 修复回归测试：reverify 重放真实沙箱证据，PoC 崩溃不得误降级。

E2E 实证：SQLi finding 审计期已被确定性沙箱实证 confirmed（sandbox_attempts 有
exit=0 + VULNERABILITY_CONFIRMED 铁证），但 poc_code 存的是 LLM 输出的 URL payload
（"/login?username=admin' --..."），reverify 把它 base64 成 __poc.py 用 python3 执行
→ SyntaxError exit=2 → confirmed 被误降 not_reproducible，前端"已验证"归零。

本测试锁定修复语义：
1. 优先重放审计期最后一次成功 attempt 的真实 command；
2. poc_code 不可执行且无历史成功 attempt → 400，状态不变；
3. PoC 自身崩溃（poc_error）→ 保持 needs_context，绝不判 not_reproducible；
4. 有历史 confirmed 铁证时，单次 rerun 未复现不推翻历史结论。
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.endpoints.agent_tasks import reverify_finding
from app.models.agent_task import (
    AgentFinding,
    AgentTask,
    AgentTaskStatus,
    VerificationStatus,
)
from app.models.project import Project

TASK_ID = "t-1"
PROJ_ID = "p-1"
FIND_ID = "f-1"


class FakeDB:
    def __init__(self, task, project, finding):
        self._task = task
        self._project = project
        self._finding = finding
        self.committed = False

    async def get(self, model, ident):
        return {
            AgentTask: self._task,
            Project: self._project,
            AgentFinding: self._finding,
        }[model]

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


class FakeSandboxManager:
    is_available = True
    result = {"success": True, "exit_code": 0, "stdout": "VULNERABILITY_CONFIRMED\n", "stderr": ""}

    def __init__(self):
        self.last_poc_code = None

    async def initialize(self):
        pass

    async def execute_poc(self, poc_code, host_project_dir, timeout=60):
        self.last_poc_code = poc_code
        return self.result


@pytest.fixture
def base_setup():
    task = AgentTask(id=TASK_ID, project_id=PROJ_ID, status=AgentTaskStatus.COMPLETED_WITH_GAPS)
    project = Project(id=PROJ_ID, source_type="zip")
    return task, project


def make_finding(**overrides):
    kwargs = {
        "id": FIND_ID,
        "task_id": TASK_ID,
        "has_poc": True,
        "poc_code": "/login?username=admin' --&password=anything",
        "sandbox_attempts": [
            {
                "tool": "python_test",
                "success": True,
                "exit_code": 0,
                "command": "python3 -c 'print(\"VULNERABILITY_CONFIRMED\")'",
                "evidence_summary": "VULNERABILITY_CONFIRMED: sqlite demo",
            }
        ],
        "verification_status": VerificationStatus.CONFIRMED,
        "is_verified": True,
    }
    kwargs.update(overrides)
    return AgentFinding(**kwargs)


async def _run(base_setup, finding, fake_mgr):
    task, project = base_setup
    db = FakeDB(task, project, finding)
    with patch("app.api.v1.endpoints.agent_tasks.assert_can_access_project"), \
         patch("app.services.agent.tools.sandbox_tool.SandboxManager", return_value=fake_mgr), \
         patch("app.api.v1.endpoints.agent_tasks.os.path.isdir", return_value=True):
        return await reverify_finding(TASK_ID, FIND_ID, db=db, current_user=None)


def test_replays_last_successful_attempt_command(base_setup):
    finding = make_finding()  # poc_code 是 URL，但历史有成功 attempt
    mgr = FakeSandboxManager()
    result = asyncio.run(_run(base_setup, finding, mgr))
    # 重放的是历史真实命令，不是 URL poc_code
    assert mgr.last_poc_code == "python3 -c 'print(\"VULNERABILITY_CONFIRMED\")'"
    assert result["verification_status"] == VerificationStatus.CONFIRMED
    assert finding.verification_status == VerificationStatus.CONFIRMED
    assert len(finding.sandbox_attempts) == 2  # 新 attempt 追加


def test_unexecutable_poc_without_history_returns_400(base_setup):
    finding = make_finding(sandbox_attempts=[])
    mgr = FakeSandboxManager()
    with pytest.raises(Exception) as e:
        asyncio.run(_run(base_setup, finding, mgr))
    # HTTPException 400：不改状态、不调沙箱
    assert getattr(e.value, "status_code", None) == 400
    assert mgr.last_poc_code is None
    assert finding.verification_status == VerificationStatus.CONFIRMED


def test_poc_error_keeps_needs_context(base_setup):
    # 合法 Python poc_code 执行时自身崩溃（Traceback）→ 不得判 not_reproducible
    finding = make_finding(
        poc_code="import nonexistent_module_xyz\n",
        sandbox_attempts=[],
        verification_status=VerificationStatus.NEEDS_CONTEXT,
        is_verified=False,
    )
    mgr = FakeSandboxManager()
    mgr.result = {
        "success": False,
        "exit_code": 1,
        "stdout": "",
        "stderr": "Traceback (most recent call last):\nModuleNotFoundError",
    }
    result = asyncio.run(_run(base_setup, finding, mgr))
    assert result["verification_status"] == VerificationStatus.NEEDS_CONTEXT
    assert finding.verification_status == VerificationStatus.NEEDS_CONTEXT


def test_prior_confirmed_evidence_not_overridden_by_single_failure(base_setup):
    # 历史 confirmed 铁证 + rerun 正常未复现（无崩溃特征）→ 结论不被单次失败推翻
    finding = make_finding(
        poc_code="print('run but no vuln marker')",
    )
    mgr = FakeSandboxManager()
    mgr.result = {"success": True, "exit_code": 0, "stdout": "no confirmation output\n", "stderr": ""}
    result = asyncio.run(_run(base_setup, finding, mgr))
    assert result["verification_status"] == VerificationStatus.CONFIRMED


def test_no_poc_returns_400(base_setup):
    finding = make_finding(has_poc=False, poc_code=None, sandbox_attempts=[])
    mgr = FakeSandboxManager()
    with pytest.raises(Exception) as e:
        asyncio.run(_run(base_setup, finding, mgr))
    assert getattr(e.value, "status_code", None) == 400
    assert mgr.last_poc_code is None
