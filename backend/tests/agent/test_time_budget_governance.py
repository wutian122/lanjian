"""w2 (T2): OrchestratorAgent 任务时间预算与调度夹逼。

背景（fix-audit-time-budget-2026-08）：任务级超时只有外部 wait_for 一条熔断，
orchestrator 内部无时间预算（agent_timeout 是死配置）。本测试规定：
1. 预算解析：input_data.task_timeout_seconds > _timeout_config.agent_timeout > 1800；
2. 主循环硬阈值 break → coverage_bypassed metadata（reason=task_deadline_exhausted，5 字段齐全）；
3. dispatch 超时 = min(类型上限, 剩余预算)，剩余不足 MIN_DISPATCH 拒发新调度；
4. 软停止只请求 analysis 且仅在剩余 < SOFT_STOP 时幂等触发；
5. mark_deadline_hit 置标志并传播取消。
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent


def _make_agent(monkeypatch) -> OrchestratorAgent:
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
    return agent


# ---------- 预算解析 ----------


def test_resolve_task_timeout_prefers_input(monkeypatch):
    agent = _make_agent(monkeypatch)
    assert agent._resolve_task_timeout({"task_timeout_seconds": 100}) == 100.0


def test_resolve_task_timeout_falls_back_to_config_then_default(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._timeout_config = {"agent_timeout": 900}
    assert agent._resolve_task_timeout({}) == 900.0

    agent._timeout_config = {}
    assert agent._resolve_task_timeout({}) == 1800.0


def test_init_task_deadline_and_remaining(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._init_task_deadline({"task_timeout_seconds": 100})
    remaining = agent._remaining_seconds()
    assert 0 < remaining <= 100

    agent._deadline = time.time() - 5
    assert agent._remaining_seconds() <= 0


# ---------- mark_deadline_hit ----------


def test_mark_deadline_hit_sets_flag_and_cancels(monkeypatch):
    agent = _make_agent(monkeypatch)
    assert agent._deadline_hit is False

    agent.mark_deadline_hit()
    assert agent._deadline_hit is True
    assert agent.is_cancelled is True


# ---------- dispatch 超时夹逼与拒发 ----------


def test_resolve_dispatch_timeout_clamps_by_remaining(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._timeout_config = {"sub_agent_timeout": 1200}

    agent._init_task_deadline({"task_timeout_seconds": 500})
    analysis_timeout = agent._resolve_dispatch_timeout("analysis")
    assert 490 <= analysis_timeout <= 500
    assert agent._resolve_dispatch_timeout("recon") == 300  # min(300, 剩余)

    agent._init_task_deadline({"task_timeout_seconds": 100000})
    assert agent._resolve_dispatch_timeout("analysis") == 1200  # 剩余充足时等于类型上限
    assert agent._resolve_dispatch_timeout("verification") == 1800  # max(1200, 1800)


def test_budget_refusal_when_remaining_below_min_dispatch(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._deadline = time.time() - 1  # 已耗尽

    refusal = agent._budget_refusal()
    assert refusal is not None and "预算" in refusal

    agent._init_task_deadline({"task_timeout_seconds": 100000})
    assert agent._budget_refusal() is None


# ---------- 软停止请求（仅 analysis、幂等、仅低剩余） ----------


def test_maybe_request_soft_stop_analysis_only(monkeypatch):
    orchestrator = _make_agent(monkeypatch)
    analysis = OrchestratorAgent(llm_service=SimpleNamespace(), tools={})
    recon = ReconAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())

    orchestrator._deadline = time.time() + 50  # 剩余 < SOFT_STOP(180)
    assert orchestrator._maybe_request_soft_stop(analysis, "analysis") is True
    assert analysis.is_soft_stopped is True
    # 幂等：已置位不再重复请求
    assert orchestrator._maybe_request_soft_stop(analysis, "analysis") is False
    # 非 analysis 不请求
    assert orchestrator._maybe_request_soft_stop(recon, "recon") is False
    assert recon.is_soft_stopped is False


def test_maybe_request_soft_stop_skipped_with_plenty_budget(monkeypatch):
    orchestrator = _make_agent(monkeypatch)
    analysis = OrchestratorAgent(llm_service=SimpleNamespace(), tools={})

    orchestrator._init_task_deadline({"task_timeout_seconds": 100000})
    assert orchestrator._maybe_request_soft_stop(analysis, "analysis") is False
    assert analysis.is_soft_stopped is False


# ---------- 主循环 deadline break ----------


@pytest.mark.asyncio
async def test_loop_deadline_break_sets_bypass_metadata(monkeypatch):
    agent = _make_agent(monkeypatch)
    stream_mock = AsyncMock(return_value=("Final Answer: {}", 0))
    monkeypatch.setattr(agent, "stream_llm_call", stream_mock)

    result = await agent.run({"project_info": {}, "config": {}, "task_timeout_seconds": 30})

    stream_mock.assert_not_awaited()  # 循环头即 break，不进 LLM 轮次
    assert result.success is True
    assert result.metadata["coverage_bypassed"] is True
    info = result.metadata["coverage_info"]
    assert info["reason"] == "task_deadline_exhausted"
    for field in ("covered_count", "total_dimensions", "gaps", "block_count"):
        assert field in info


# ---------- w3 (T3): analysis 软终止交卷 ----------

_SUMMARY_OUTPUT = """```json
{"findings": [{"vulnerability_type": "sql_injection", "severity": "high", "title": "SQL 注入", "file_path": "a.java", "line_start": 10}], "summary": "s"}
```"""


def _make_analysis_agent(monkeypatch):
    from app.services.agent.agents.analysis import AnalysisAgent

    agent = AnalysisAgent(llm_service=SimpleNamespace(), tools={})
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "emit_event", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_decision", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_thought", AsyncMock())
    monkeypatch.setattr(agent, "emit_finding", AsyncMock())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    agent.config.max_iterations = 3
    return agent


@pytest.mark.asyncio
async def test_analysis_soft_stop_breaks_loop_and_submits(monkeypatch):
    """软停止在循环头被消费：立即退出探索并强制总结交卷（不跑满剩余轮次）。"""
    agent = _make_analysis_agent(monkeypatch)
    agent.request_soft_stop()
    # 循环头 break 后的第一次 LLM 调用即强制总结；后续条目仅在未消费时才会用到
    stream_mock = AsyncMock(return_value=("Thought: 还需要更多信息", 0))
    stream_mock.side_effect = [(_SUMMARY_OUTPUT, 0)] + [("Thought: 还需要更多信息", 0)] * 3
    monkeypatch.setattr(agent, "stream_llm_call", stream_mock)

    result = await agent.run({"project_info": {}, "config": {}})

    assert stream_mock.await_count == 1  # 循环头 break + 1 次强制总结
    assert result.success is True
    findings = result.data["findings"]
    assert len(findings) == 1
    assert findings[0]["vulnerability_type"] == "sql_injection"


@pytest.mark.asyncio
async def test_analysis_cancel_wins_over_soft_stop(monkeypatch):
    """软停止与用户取消并存时取消语义胜出：不强制总结、不虚报。"""
    agent = _make_analysis_agent(monkeypatch)
    agent.request_soft_stop()
    agent.set_cancel_callback(lambda: True)
    stream_mock = AsyncMock(return_value=(_SUMMARY_OUTPUT, 0))
    monkeypatch.setattr(agent, "stream_llm_call", stream_mock)

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is False
    assert "取消" in (result.error or "")
    stream_mock.assert_not_awaited()  # 取消路径不做强制总结


@pytest.mark.asyncio
async def test_forced_summary_merges_into_existing_findings(monkeypatch):
    """强制总结合并语义：已有发现时扩展（orchestrator 层去重），空时替换。"""
    agent = _make_analysis_agent(monkeypatch)
    existing = [
        {
            "vulnerability_type": "xss",
            "severity": "medium",
            "title": "XSS",
            "file_path": "b.js",
            "line_start": 1,
        }
    ]
    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(return_value=(_SUMMARY_OUTPUT, 0)))

    merged = await agent._run_forced_summary(list(existing))
    assert len(merged) == 2  # 已有 XSS + 总结补报的 SQL 注入
    assert merged[0]["vulnerability_type"] == "xss"

    replaced = await agent._run_forced_summary([])
    assert len(replaced) == 1
    assert replaced[0]["vulnerability_type"] == "sql_injection"


@pytest.mark.asyncio
async def test_forced_summary_ignores_non_list_findings(monkeypatch):
    """防御分支：parsed findings 非 list 时保持原值不崩。"""
    agent = _make_analysis_agent(monkeypatch)
    existing = [{"vulnerability_type": "xss", "title": "XSS"}]
    monkeypatch.setattr(
        agent, "stream_llm_call", AsyncMock(return_value=('{"findings": "oops"}', 0))
    )

    merged = await agent._run_forced_summary(list(existing))
    assert merged == existing
