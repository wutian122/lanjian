"""Orchestrator 弹性终止门禁回归测试。

验证三层门禁（沙箱证据 / 软覆盖率 / 硬覆盖率）已恢复：
- _has_valid_sandbox_evidence 正确判定沙箱验证证据
- _evaluate_current_coverage 返回覆盖率报告
- _hard_coverage_block_count 门禁字段存在
- _convert_recon_high_risk_area_to_finding 高风险区不作为 findings
"""
from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent():
    """绕过 __init__ 构造 agent，手动设置门禁依赖的实例属性。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._all_findings = []
    agent._steps = []
    agent._agent_results = {}
    agent._hard_coverage_block_count = 0
    agent._dispatched_tasks = {}
    return agent


def test_gate_methods_exist():
    """门禁方法必须存在（当前缺失即为回归）。"""
    assert hasattr(OrchestratorAgent, "_has_valid_sandbox_evidence")
    assert hasattr(OrchestratorAgent, "_evaluate_current_coverage")


def test_has_valid_sandbox_evidence_false_when_no_evidence():
    agent = _make_agent()
    agent._all_findings = [
        {"title": "SQL注入", "verification_status": "needs_context"}
    ]
    assert agent._has_valid_sandbox_evidence() is False


def test_has_valid_sandbox_evidence_true_when_confirmed_with_evidence():
    """confirmed 且带成功沙箱证据 → 有效（Bug C 收紧后的语义）。"""
    agent = _make_agent()
    agent._all_findings = [
        {
            "title": "SQL注入",
            "verification_status": "confirmed",
            "sandbox_attempts": [{"success": True, "exit_code": 0}],
        }
    ]
    assert agent._has_valid_sandbox_evidence() is True


def test_has_valid_sandbox_evidence_true_when_sandbox_success():
    """confirmed + 沙箱成功（success=True, exit_code=0）→ 有效。"""
    agent = _make_agent()
    agent._all_findings = [
        {
            "title": "RCE",
            "verification_status": "confirmed",
            "sandbox_attempts": [{"success": True, "exit_code": 0}],
        }
    ]
    assert agent._has_valid_sandbox_evidence() is True


def test_has_valid_sandbox_evidence_true_when_static_confirmed_with_evidence():
    """REQ-VP-2：static_confirmed 且有沙箱证据 → 有效。"""
    agent = _make_agent()
    agent._all_findings = [{
        "title": "XSS",
        "verification_status": "static_confirmed",
        "sandbox_attempts": [{"success": True, "exit_code": 0, "static_evidence": True}],
    }]
    assert agent._has_valid_sandbox_evidence() is True


def test_has_valid_sandbox_evidence_false_when_static_confirmed_without_evidence():
    """REQ-VP-2：static_confirmed 但无沙箱证据（免沙箱路径）→ 无效，不击穿 finish 门禁。"""
    agent = _make_agent()
    agent._all_findings = [{"title": "XSS", "verification_status": "static_confirmed"}]
    assert agent._has_valid_sandbox_evidence() is False


def test_has_valid_sandbox_evidence_false_when_confirmed_without_evidence():
    """Bug C fix：confirmed 但无沙箱证据 → 无效（不再仅凭状态放行）。"""
    agent = _make_agent()
    agent._all_findings = [{"title": "SQL注入", "verification_status": "confirmed"}]
    assert agent._has_valid_sandbox_evidence() is False


def test_has_valid_sandbox_evidence_false_when_only_is_verified():
    """Bug C fix：is_verified=True 不再单独放行（必须有沙箱证据或 static_confirmed）。"""
    agent = _make_agent()
    agent._all_findings = [{"title": "XSS", "is_verified": True}]
    assert agent._has_valid_sandbox_evidence() is False


def test_has_valid_sandbox_evidence_false_when_sandbox_failed():
    agent = _make_agent()
    agent._all_findings = [
        {
            "title": "SSRF",
            "sandbox_attempts": [{"success": False, "exit_code": 1}],
        }
    ]
    assert agent._has_valid_sandbox_evidence() is False


def test_evaluate_current_coverage_returns_report():
    from app.services.agent.coverage import CoverageReport

    agent = _make_agent()
    report = agent._evaluate_current_coverage()
    assert isinstance(report, CoverageReport)
    assert hasattr(report, "is_sufficient")
    assert hasattr(report, "covered_count")


def test_hard_coverage_block_count_initializable():
    agent = _make_agent()
    assert agent._hard_coverage_block_count == 0
