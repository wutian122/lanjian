"""Task 5 (audit-time-governance): 类型化拒发新调度阈值。

spec Requirement「时间预算将尽时禁止无效派发」：
- analysis/verification 最小有效工作时长 300s、recon 120s（可配置），未知类型保守 300s；
- 剩余预算 <= 类型化阈值时拒发，返回收口文案并记 _gate_observations
  （gate="dispatch_budget"，reason 文本承载 remaining_seconds/required_seconds/agent_name）；
- 阈值(300s)高于软停止阈值(180s)，消除"派发后同一轮询周期立即软停止"的无效派发。
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent(monkeypatch, remaining: float | None = None) -> OrchestratorAgent:
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
    if remaining is not None:
        # 确定性桩：直接钉死剩余预算，避免 deadline 时序抖动
        monkeypatch.setattr(agent, "_remaining_seconds", lambda: remaining)
    return agent


# ---------- Scenario 1: 剩余 120s 派发 analysis（需 300s）→ 拒绝 + 观测记录 ----------


def test_refuse_analysis_when_remaining_below_typed_threshold(monkeypatch):
    agent = _make_agent(monkeypatch, remaining=120.0)
    assert agent._gate_observations == []

    refusal = agent._budget_refusal("analysis")

    assert refusal is not None
    assert "预算" in refusal
    # 文案须含剩余秒数与所需时长（spec Scenario 1："预算不足以完成有效分析，提前收口"）
    assert "120" in refusal and "300" in refusal and "analysis" in refusal
    # spec Scenario 3：_gate_observations 新增 dispatch_budget 记录，数字编入 reason 文本
    assert len(agent._gate_observations) == 1
    obs = agent._gate_observations[0]
    assert obs["gate"] == "dispatch_budget"
    assert "120" in obs["reason"] and "300" in obs["reason"] and "analysis" in obs["reason"]


def test_refuse_verification_at_same_threshold_as_analysis(monkeypatch):
    agent = _make_agent(monkeypatch, remaining=250.0)
    refusal = agent._budget_refusal("verification")
    assert refusal is not None
    assert "250" in refusal and "300" in refusal and "verification" in refusal
    assert agent._gate_observations[-1]["gate"] == "dispatch_budget"


# ---------- Scenario 2: 预算充足（900s）→ 不拦截、不记观测 ----------


def test_allow_dispatch_when_budget_sufficient(monkeypatch):
    agent = _make_agent(monkeypatch, remaining=900.0)
    assert agent._budget_refusal("analysis") is None
    assert agent._budget_refusal("verification") is None
    assert agent._gate_observations == []  # 未拒绝不得留观测记录


# ---------- Scenario 3: recon 阈值 120s 边界 ----------


def test_recon_threshold_boundary(monkeypatch):
    # 剩余 121s > 120s → 派发
    agent = _make_agent(monkeypatch, remaining=121.0)
    assert agent._budget_refusal("recon") is None

    # 剩余 119s < 120s → 拒发
    agent = _make_agent(monkeypatch, remaining=119.0)
    refusal = agent._budget_refusal("recon")
    assert refusal is not None
    assert "119" in refusal and "120" in refusal and "recon" in refusal
    assert agent._gate_observations[-1]["gate"] == "dispatch_budget"

    # 剩余恰等阈值 120s → 拒发（<= 语义：无安全余量，扣除调度/启动开销即不足，
    # 与既有 `<= MIN_DISPATCH` 取向一致；且拒发阈值高于软停止 180s 才不矛盾）
    agent = _make_agent(monkeypatch, remaining=120.0)
    assert agent._budget_refusal("recon") is not None


# ---------- 未知 agent 类型：保守默认 300s ----------


def test_unknown_agent_type_uses_conservative_default(monkeypatch):
    agent = _make_agent(monkeypatch, remaining=200.0)
    refusal = agent._budget_refusal("some_future_agent")
    assert refusal is not None  # 200 <= 保守默认 300 → 拒
    assert "300" in refusal

    agent = _make_agent(monkeypatch, remaining=400.0)
    assert agent._budget_refusal("some_future_agent") is None


# ---------- 阈值可被 settings 覆盖 ----------


def test_thresholds_overridable_via_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TIME_BUDGET_MIN_EFFECTIVE_RECON", 60, raising=False)
    agent = _make_agent(monkeypatch, remaining=90.0)
    assert agent._budget_refusal("recon") is None  # 90 > 覆盖后的 60s


# ---------- deadline 未初始化（inf）→ 不拒 ----------


def test_no_deadline_means_no_refusal(monkeypatch):
    agent = _make_agent(monkeypatch)
    assert agent._deadline is None
    assert agent._budget_refusal("analysis") is None
    assert agent._gate_observations == []


# ---------- 回归锚点：真实 deadline 耗尽仍拒（原 test_time_budget_governance 语义） ----------


def test_real_deadline_exhausted_still_refuses(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._deadline = time.time() - 1  # 已耗尽
    refusal = agent._budget_refusal("analysis")
    assert refusal is not None and "预算" in refusal

    agent._init_task_deadline({"task_timeout_seconds": 100000})
    assert agent._budget_refusal("analysis") is None
