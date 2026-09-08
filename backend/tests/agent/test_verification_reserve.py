"""B1: verification 预算预留——剩余预算不足预留量时拒发新 analysis。

生产实证（重验 v4/v5）：verification 深入验证时被主循环轮次/时间预算耗尽
掐断，半途 findings 丢失、无 attempt 落库。治理：Analysis 已产出待验证项后，
剩余预算 < VERIFICATION_RESERVE_SECONDS（默认 900s）时不再派新 analysis，
把时间留给 verification；verification 自身派发不受预留影响（预留窗口内
照常跑，超时由既有 watchdog/弹性退出收口）。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent(monkeypatch, remaining: float, findings=None) -> OrchestratorAgent:
    agent = OrchestratorAgent(llm_service=SimpleNamespace(), tools={})
    monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
    monkeypatch.setattr(
        agent,
        "_run_semgrep_prescan",
        AsyncMock(return_value={"findings": [], "hot_files": [], "scan_success": False}),
    )
    monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "emit_event", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_decision", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_thought", AsyncMock())
    monkeypatch.setattr(agent, "check_messages", lambda: [])
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    # 确定性桩：钉死剩余预算
    monkeypatch.setattr(agent, "_remaining_seconds", lambda: remaining)
    if findings is not None:
        agent._all_findings = findings
    return agent


def _finding():
    """一条 analysis 产出的待验证 finding（非 recon 上下文线索）。"""
    return {
        "source": "analysis",
        "title": "SQL 注入",
        "vulnerability_type": "sql_injection",
        "severity": "high",
        "file_path": "app/main.py",
        "is_verified": False,
    }


# ---------- Scenario 1: 有待验证产出 + 剩余 < 900s → 拒发新 analysis ----------


def test_refuse_new_analysis_when_budget_below_verification_reserve(monkeypatch):
    agent = _make_agent(monkeypatch, remaining=800.0, findings=[_finding()])

    refusal = agent._budget_refusal("analysis")

    assert refusal is not None
    assert "verification" in refusal.lower() or "验证" in refusal
    # 文案须承载剩余秒数与预留量，供 LLM 决策 finish/派 verification
    assert "800" in refusal and "900" in refusal
    # 预留拒发与最小有效时长拒发分闸记录，便于观测区分
    reserve_obs = [o for o in agent._gate_observations if o["gate"] == "verification_reserve"]
    assert len(reserve_obs) == 1
    assert "800" in reserve_obs[0]["reason"] and "900" in reserve_obs[0]["reason"]


# ---------- Scenario 2: 剩余 >= 900s → 正常派发 analysis ----------


def test_allow_analysis_when_budget_covers_reserve(monkeypatch):
    agent = _make_agent(monkeypatch, remaining=900.0, findings=[_finding()])
    assert agent._budget_refusal("analysis") is None
    assert agent._gate_observations == []

    agent = _make_agent(monkeypatch, remaining=1200.0, findings=[_finding()])
    assert agent._budget_refusal("analysis") is None


# ---------- Scenario 3: 无待验证产出 → 无需预留，analysis 不受 900s 约束 ----------


def test_no_reserve_needed_without_actionable_findings(monkeypatch):
    # 剩余 500s（< 900 但 > analysis 最小有效 300s）且无任何产出 → 不拒
    agent = _make_agent(monkeypatch, remaining=500.0, findings=[])
    assert agent._budget_refusal("analysis") is None
    assert agent._gate_observations == []

    # recon 上下文线索不算待验证产出
    agent = _make_agent(
        monkeypatch,
        remaining=500.0,
        findings=[{"source": "recon", "title": "线索", "vulnerability_type": "sql_injection"}],
    )
    assert agent._budget_refusal("analysis") is None


# ---------- Scenario 4: verification 派发不受预留影响 ----------


def test_verification_dispatch_not_blocked_by_reserve(monkeypatch):
    # 剩余 600s（< 900 预留、> verification 最小有效 300s）→ verification 照常派发
    agent = _make_agent(monkeypatch, remaining=600.0, findings=[_finding()])
    assert agent._budget_refusal("verification") is None

    # 剩余 200s（< 300 最小有效）→ 仍走既有 dispatch_budget 拒发，而非预留闸
    agent = _make_agent(monkeypatch, remaining=200.0, findings=[_finding()])
    refusal = agent._budget_refusal("verification")
    assert refusal is not None
    assert agent._gate_observations[-1]["gate"] == "dispatch_budget"


# ---------- Scenario 5: recon 派发不受预留影响 ----------


def test_recon_dispatch_not_blocked_by_reserve(monkeypatch):
    agent = _make_agent(monkeypatch, remaining=500.0, findings=[_finding()])
    assert agent._budget_refusal("recon") is None  # 500 > recon 阈值 120


# ---------- 预留量可被 settings 覆盖 ----------


def test_reserve_seconds_overridable_via_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "VERIFICATION_RESERVE_SECONDS", 300, raising=False)
    agent = _make_agent(monkeypatch, remaining=400.0, findings=[_finding()])
    assert agent._budget_refusal("analysis") is None  # 400 >= 覆盖后的 300s

    agent = _make_agent(monkeypatch, remaining=250.0, findings=[_finding()])
    # 250 <= analysis 最小有效时长 300s：dispatch_budget 闸先拒（预留闸不及触发）
    refusal = agent._budget_refusal("analysis")
    assert refusal is not None
    assert agent._gate_observations[-1]["gate"] == "dispatch_budget"
