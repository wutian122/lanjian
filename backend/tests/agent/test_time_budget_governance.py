"""w2 (T2): OrchestratorAgent 任务时间预算与调度夹逼。

背景（fix-audit-time-budget-2026-08）：任务级超时只有外部 wait_for 一条熔断，
orchestrator 内部无时间预算（agent_timeout 是死配置）。本测试规定：
1. 预算解析：input_data.task_timeout_seconds > _timeout_config.agent_timeout > 1800；
2. 主循环硬阈值 break → coverage_bypassed metadata（reason=task_deadline_exhausted，5 字段齐全）；
3. dispatch 超时 = min(类型上限, 剩余预算)，剩余不足 MIN_DISPATCH 拒发新调度；
4. 软停止只请求 analysis 且仅在剩余 < SOFT_STOP 时幂等触发；
5. mark_deadline_hit 置标志并传播取消。
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
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


# ---------- w4 (T4/T5): watchdog / 发现保全 / 诚实终态 ----------

def test_finalize_budget_metadata_task_timeout_wins(monkeypatch):
    """deadline 命中 → task_timeout 最高优先，覆盖已有 bypass。"""
    orch = _make_agent(monkeypatch)
    orch._coverage_bypassed = True
    orch._coverage_bypass_info = {"reason": "token_budget_exhausted"}
    orch.mark_deadline_hit()

    orch._finalize_budget_metadata()
    assert orch._coverage_bypassed is True
    assert orch._coverage_bypass_info["reason"] == "task_timeout"
    for field in ("covered_count", "total_dimensions", "gaps", "block_count"):
        assert field in orch._coverage_bypass_info


def test_finalize_budget_metadata_dispatch_exhausted(monkeypatch):
    """0 发现 + 存在调度失败 → dispatch_budget_exhausted（诚实终态）。"""
    orch = _make_agent(monkeypatch)
    orch._dispatch_failures = 2

    orch._finalize_budget_metadata()
    assert orch._coverage_bypassed is True
    info = orch._coverage_bypass_info
    assert info["reason"] == "dispatch_budget_exhausted"
    for field in ("covered_count", "total_dimensions", "gaps", "block_count"):
        assert field in info


def test_finalize_budget_metadata_clean_when_findings_exist(monkeypatch):
    """有发现（即使有调度失败）→ 不虚报缺口，走正常验证路径。"""
    orch = _make_agent(monkeypatch)
    orch._dispatch_failures = 3
    orch._all_findings = [{"title": "x"}]

    orch._finalize_budget_metadata()
    assert orch._coverage_bypassed is False


def test_finalize_budget_metadata_noop_when_clean(monkeypatch):
    """无 deadline、无失败、无既有 bypass → 不置位。"""
    orch = _make_agent(monkeypatch)
    orch._finalize_budget_metadata()
    assert orch._coverage_bypassed is False


def test_failed_subagent_findings_merged(monkeypatch):
    """失败子 Agent（success=False）data 中的已有发现仍合并入 _all_findings。"""
    from app.services.agent.agents.base import AgentResult

    orch = _make_agent(monkeypatch)
    fake = AgentResult(
        success=False,
        error="任务已取消",
        data={"findings": [{
            "vulnerability_type": "sql_injection", "severity": "high",
            "title": "SQLi", "file_path": "app/x.py", "line_start": 5,
            "confidence": 0.9,
        }]},
    )
    merged = orch._merge_failed_result_findings("analysis", fake)
    assert merged == 1
    assert len(orch._all_findings) == 1
    assert orch._all_findings[0]["vulnerability_type"] == "sql_injection"


def test_failed_subagent_without_findings_noop(monkeypatch):
    """失败子 Agent 无 findings 时不产生合并。"""
    from app.services.agent.agents.base import AgentResult

    orch = _make_agent(monkeypatch)
    fake = AgentResult(success=False, error="任务已取消", data={"findings": []})
    assert orch._merge_failed_result_findings("analysis", fake) == 0
    assert orch._all_findings == []


@pytest.mark.asyncio
async def test_budget_watchdog_graceful_wrap(monkeypatch):
    """watchdog 到点 → mark_deadline_hit；任务宽限内完成 → 注入 task_timeout bypass。"""
    from app.api.v1.endpoints.agent_tasks import _run_orchestrator_with_budget_watchdog
    from app.services.agent.agents.base import AgentResult

    monkeypatch.setattr(settings, "TIME_BUDGET_GRACE_SECONDS", 1)
    orch = SimpleNamespace(
        mark_deadline_hit=MagicMock(),
        _all_findings=[], _iteration=0, _tool_calls=0,
        _total_tokens=0, _sub_agent_total_tokens=0,
    )
    emitter = SimpleNamespace(emit_warning=AsyncMock())

    async def slow_run():
        await asyncio.sleep(0.4)
        return AgentResult(success=True, data={"findings": []}, metadata={})

    run_task = asyncio.create_task(slow_run())
    result, hit = await _run_orchestrator_with_budget_watchdog(
        orch, run_task, task_timeout=0.2, event_emitter=emitter, task_id="t-wd-1", task_started_at=time.time()
    )

    assert hit is True
    orch.mark_deadline_hit.assert_called_once()
    assert result.metadata["coverage_bypassed"] is True
    assert result.metadata["coverage_info"]["reason"] == "task_timeout"
    emitter.emit_warning.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_watchdog_hard_cancel_fallback(monkeypatch):
    """宽限耗尽仍不返回 → hard-cancel 兜底（保存已有发现，task_timeout metadata）。"""
    from app.api.v1.endpoints.agent_tasks import _run_orchestrator_with_budget_watchdog
    from app.services.agent.agents.base import AgentResult

    monkeypatch.setattr(settings, "TIME_BUDGET_GRACE_SECONDS", 1)
    orch = SimpleNamespace(
        mark_deadline_hit=MagicMock(),
        _all_findings=[{"title": "keep-me"}], _iteration=2, _tool_calls=1,
        _total_tokens=10, _sub_agent_total_tokens=0,
    )
    emitter = SimpleNamespace(emit_warning=AsyncMock())

    async def hanging_run():
        await asyncio.sleep(30)
        return AgentResult(success=True, data={"findings": []}, metadata={})

    run_task = asyncio.create_task(hanging_run())
    result, hit = await _run_orchestrator_with_budget_watchdog(
        orch, run_task, task_timeout=0.2, event_emitter=emitter, task_id="t-wd-2", task_started_at=time.time()
    )

    assert hit is True
    assert result.success is True
    assert result.metadata["coverage_bypassed"] is True
    assert result.metadata["coverage_info"]["reason"] == "task_timeout"
    assert result.data["findings"] == [{"title": "keep-me"}]
    emitter.emit_warning.assert_awaited()  # 兜底路径必须发 warning
    assert run_task.cancelled() or run_task.done()


@pytest.mark.asyncio
async def test_budget_watchdog_noop_within_budget(monkeypatch):
    """预算内完成 → 无注入、无 deadline 标记（现状行为不变）。"""
    from app.api.v1.endpoints.agent_tasks import _run_orchestrator_with_budget_watchdog
    from app.services.agent.agents.base import AgentResult

    orch = SimpleNamespace(
        mark_deadline_hit=MagicMock(),
        _all_findings=[], _iteration=0, _tool_calls=0,
        _total_tokens=0, _sub_agent_total_tokens=0,
    )
    emitter = SimpleNamespace(emit_warning=AsyncMock())

    async def fast_run():
        return AgentResult(success=True, data={"findings": []}, metadata={})

    run_task = asyncio.create_task(fast_run())
    result, hit = await _run_orchestrator_with_budget_watchdog(
        orch, run_task, task_timeout=10, event_emitter=emitter, task_id="t-wd-3", task_started_at=time.time()
    )

    assert hit is False
    orch.mark_deadline_hit.assert_not_called()
    assert "coverage_bypassed" not in (result.metadata or {})
    emitter.emit_warning.assert_not_awaited()
