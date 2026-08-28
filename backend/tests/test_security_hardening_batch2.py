"""
security-hardening-2026-08 第二批：功能正确性修复测试（TDD RED→GREEN）

B2: 仓库扫描的 rule_set_id / prompt_template_id 需从请求透传到 scan_repo_task 的 user_config
    （此前 database.ts 丢字段 + projects.py ScanRequest 无此字段 + 注入点只带 file_paths）
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.rbac import UserRole


def test_scan_request_accepts_rule_and_template_ids():
    """ScanRequest schema 必须承载 rule_set_id / prompt_template_id（此前缺失）。"""
    from app.api.v1.endpoints.projects import ScanRequest

    req = ScanRequest(
        file_paths=["a.py"],
        full_scan=False,
        exclude_patterns=["node_modules"],
        branch_name="main",
        rule_set_id="rs1",
        prompt_template_id="pt1",
    )
    assert req.rule_set_id == "rs1"
    assert req.prompt_template_id == "pt1"
    assert req.dict()["rule_set_id"] == "rs1"
    assert req.dict()["prompt_template_id"] == "pt1"


async def test_scan_project_injects_rule_and_template_into_user_config():
    """scan_project 必须把 rule_set_id / prompt_template_id 注入 user_config['scan_config']。"""
    from app.api.v1.endpoints.projects import ScanRequest, scan_project

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = None  # 用户无已存配置
    db.execute.return_value = db_result

    background = MagicMock()
    req = ScanRequest(
        file_paths=["a.py"],
        rule_set_id="rs1",
        prompt_template_id="pt1",
    )
    user = SimpleNamespace(id="u1", role=UserRole.USER)

    with patch(
        "app.api.v1.endpoints.projects.check_project_access",
        new=AsyncMock(return_value=SimpleNamespace(id="p1", default_branch="main")),
    ):
        await scan_project(
            id="p1",
            background_tasks=background,
            scan_request=req,
            db=db,
            current_user=user,
        )

    args = background.add_task.call_args.args
    user_config = args[3]
    assert user_config["scan_config"]["rule_set_id"] == "rs1"
    assert user_config["scan_config"]["prompt_template_id"] == "pt1"
    assert user_config["scan_config"]["file_paths"] == ["a.py"]


# ============ B4: 单条 finding PoC 重跑端点 ============

def _finding(id_: str, has_poc: bool, poc_code: str | None):
    from app.models.agent_task import VerificationStatus

    return SimpleNamespace(
        id=id_,
        task_id="t1",
        has_poc=has_poc,
        poc_code=poc_code,
        sandbox_attempts=None,
        verification_result=None,
        verification_status=VerificationStatus.NEEDS_CONTEXT,
        is_verified=False,
        verified_at=None,
    )


async def test_reverify_finding_rejects_without_poc():
    """没有 PoC 的 finding 不能重跑。"""
    from app.api.v1.endpoints.agent_tasks import reverify_finding

    db = AsyncMock()

    async def fake_get(model, pk):
        if model.__name__ == "AgentTask":
            return SimpleNamespace(id="t1", project_id="p1")
        return _finding("f1", has_poc=False, poc_code=None)

    db.get.side_effect = fake_get
    with patch("app.api.v1.endpoints.agent_tasks.assert_can_access_project", new=MagicMock()), patch(
        "app.api.v1.endpoints.agent_tasks.os.path.isdir", return_value=True
    ):
        with pytest.raises(Exception) as e:
            await reverify_finding(
                task_id="t1", finding_id="f1", db=db,
                current_user=SimpleNamespace(id="u1", role=UserRole.USER),
            )
    assert getattr(e.value, "status_code", None) == 400


async def test_reverify_finding_runs_poc_and_updates_status():
    """有 PoC 时在沙箱重放并更新 finding 验证状态。

    #2 修复：状态由确定性证据引擎推导——success 且输出含漏洞触发证据才 confirmed，
    不再"exit 0 即 confirmed"（旧语义是 #2 误降级缺陷的同源问题）。
    """
    from app.api.v1.endpoints.agent_tasks import reverify_finding
    from app.models.agent_task import VerificationStatus

    finding = _finding("f1", has_poc=True, poc_code="print('poc')")
    db = AsyncMock()

    async def fake_get(model, pk):
        if model.__name__ == "AgentTask":
            return SimpleNamespace(id="t1", project_id="p1")
        return finding

    db.get.side_effect = fake_get

    fake_result = {"success": True, "exit_code": 0, "stdout": "VULNERABILITY_CONFIRMED: poc executed", "stderr": ""}
    with patch("app.api.v1.endpoints.agent_tasks.assert_can_access_project", new=MagicMock()), patch(
        "app.api.v1.endpoints.agent_tasks.os.path.isdir", return_value=True
    ):
        with patch("app.services.agent.tools.sandbox_tool.SandboxManager") as SM:
            SM.return_value.initialize = AsyncMock()
            SM.return_value.is_available = True
            SM.return_value.execute_poc = AsyncMock(return_value=fake_result)
            resp = await reverify_finding(
                task_id="t1", finding_id="f1", db=db,
                current_user=SimpleNamespace(id="u1", role=UserRole.USER),
            )

    assert resp["success"] is True
    assert resp["verification_status"] == VerificationStatus.CONFIRMED
    assert finding.is_verified is True
    assert finding.verification_status == VerificationStatus.CONFIRMED
    assert finding.sandbox_attempts and len(finding.sandbox_attempts) == 1
    assert finding.verification_result["method"] == "poc-rerun"


async def test_reverify_finding_marks_not_reproducible_on_failure():
    """PoC 执行失败 → 状态由证据引擎推导（#2 修复后语义）。

    PoC 自身崩溃（exit_code=1）→ poc_error → NEEDS_CONTEXT（PoC 有问题，非漏洞不可复现）。
    若要验证'漏洞确实不可复现'场景，需 PoC 正常执行但输出无漏洞触发证据。
    """
    from app.api.v1.endpoints.agent_tasks import reverify_finding
    from app.models.agent_task import VerificationStatus

    finding = _finding("f1", has_poc=True, poc_code="raise SystemExit(1)")
    db = AsyncMock()

    async def fake_get(model, pk):
        if model.__name__ == "AgentTask":
            return SimpleNamespace(id="t1", project_id="p1")
        return finding

    db.get.side_effect = fake_get

    fake_result = {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"}
    with patch("app.api.v1.endpoints.agent_tasks.assert_can_access_project", new=MagicMock()), patch(
        "app.api.v1.endpoints.agent_tasks.os.path.isdir", return_value=True
    ):
        with patch("app.services.agent.tools.sandbox_tool.SandboxManager") as SM:
            SM.return_value.initialize = AsyncMock()
            SM.return_value.is_available = True
            SM.return_value.execute_poc = AsyncMock(return_value=fake_result)
            resp = await reverify_finding(
                task_id="t1", finding_id="f1", db=db,
                current_user=SimpleNamespace(id="u1", role=UserRole.USER),
            )

    assert resp["success"] is False
    # #2 修复：PoC 崩溃 → NEEDS_CONTEXT（PoC 错误），不再误判 NOT_REPRODUCIBLE
    assert finding.verification_status == VerificationStatus.NEEDS_CONTEXT
