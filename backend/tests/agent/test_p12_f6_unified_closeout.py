"""P12 Wave 0 · F6 回归夹具（三收口路径 observations 四 gate 齐全）。

事件证据（任务 8a3a713f 收口段）：Semgrep 兜底/线索留痕仅在正常 finish 与
轮次耗尽 finalize 可达；deadline 早返（orchestrator.py 2584）、error 早返
（2635）、外层 except（2788）三个收口点无任何兜底钩子——这三条路径销毁证据
且 observations 零留痕。

Wave 2.1 契约（方案第二章 2.1）：
- 收敛三个已幂等函数为单一 best-effort 收口钩子
  （_register_semgrep_leads_observation + _apply_semgrep_fallback +
  _apply_output_floor_closeout，整体 try/except 绝不抛错）；
- 挂载四点：正常 return、deadline 早返、error 早返、外层 except；
- 失败返回体（error/except）data 必须补齐 findings+observations+steps（Wave 2.2）。

本夹具锁定可观察契约：四条收口路径返回体都携带 observations，且预扫命中
至少留下 semgrep_leads gate 留痕（正常路径为控制组，当前即应满足）。

T0 阶段：deadline/error/except 三用例 RED（当前无钩子/无 observations）。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _semgrep_hit(path="app/main.py", line=42, rule="p.sql-injection"):
    """_run_semgrep_prescan 输出形态（file_path 用真实存在文件避免幻觉过滤）。"""
    return {
        "title": rule,
        "file_path": path,
        "line_start": line,
        "line_end": line,
        "severity": "high",
        "description": "SQL injection sink",
        "vulnerability_type": "injection",
        "code_snippet": "cursor.execute(query)",
        "source": "semgrep",
        "verification_method": "semgrep_static_analysis",
        "is_verified": False,
    }


def _make_agent(monkeypatch):
    agent = OrchestratorAgent(llm_service=SimpleNamespace(), tools={})
    monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
    monkeypatch.setattr(
        agent,
        "_run_semgrep_prescan",
        AsyncMock(return_value={
            "findings": [_semgrep_hit()],
            "hot_files": ["app/main.py"],
            "scan_success": True,
        }),
    )
    monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "emit_event", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_decision", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_thought", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_observation", AsyncMock())
    monkeypatch.setattr(agent, "check_messages", lambda: [])
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    monkeypatch.setattr(agent, "_inject_trace_summary", AsyncMock())
    return agent


def _closeout_gates(result):
    """提取收口返回体 observations 中的 gate 名集合。"""
    assert isinstance(result.data, dict), (
        f"所有收口路径返回体必须含 data（error/except 路径当前裸退）: {result.data!r}"
    )
    observations = result.data.get("observations")
    assert isinstance(observations, list), (
        f"收口返回体必须显式携带 observations 列表，实际: {observations!r}"
    )
    return {o.get("gate") for o in observations if isinstance(o, dict)}


# ---------- 控制组：正常 finish 路径（当前即有钩子） ----------

@pytest.mark.asyncio
async def test_f6_normal_finish_runs_unified_closeout(monkeypatch):
    """正常 finish：semgrep_leads 留痕 + 兜底候选保全（回归基线）。"""
    agent = _make_agent(monkeypatch)
    monkeypatch.setattr(
        agent, "stream_llm_call",
        AsyncMock(return_value=(
            "Thought: 审计完成\nAction: finish\nAction Input: {}", 0
        )),
    )

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, result.error
    gates = _closeout_gates(result)
    assert "semgrep_leads" in gates, "正常 finish 必须留 semgrep_leads 痕"


# ---------- deadline 早返 ----------

@pytest.mark.asyncio
async def test_f6_deadline_early_return_runs_unified_closeout(monkeypatch):
    """watchdog deadline 命中（is_cancelled + _deadline_hit）早返：
    返回体 observations 必须含统一收口 gate 留痕，预扫证据不销毁。"""
    agent = _make_agent(monkeypatch)
    monkeypatch.setattr(
        agent, "stream_llm_call",
        AsyncMock(return_value=("不应被调用", 0)),
    )
    # watchdog 在运行中并发置位（run 入口会重置 _deadline_hit）：
    # 在首轮循环头 _maybe_pause 处模拟到点 + cancel
    async def _deadline_side_effect():
        agent.mark_deadline_hit()

    monkeypatch.setattr(agent, "_maybe_pause", _deadline_side_effect)

    result = await agent.run({
        "project_info": {}, "config": {}, "task_timeout_seconds": 1800,
    })

    assert result.success
    gates = _closeout_gates(result)
    assert "semgrep_leads" in gates, (
        f"deadline 早返必须补跑统一收口（当前三路径无钩子），gates={gates}"
    )


# ---------- error 早返 ----------

@pytest.mark.asyncio
async def test_f6_error_early_return_runs_unified_closeout(monkeypatch):
    """编排循环连续空响应止损（error_message）早返：data 必须补齐
    observations+findings+steps，且含统一收口 gate 留痕。"""
    agent = _make_agent(monkeypatch)
    monkeypatch.setattr(
        agent, "stream_llm_call",
        AsyncMock(return_value=("", 0)),
    )
    # 预置 4：首轮空响应即达 5 连空止损，避免真实 sleep
    agent._empty_retry_count = 4

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is False
    assert result.error, "error 早返必须保留错误文案"
    gates = _closeout_gates(result)
    assert "semgrep_leads" in gates, (
        f"error 早返必须补跑统一收口，gates={gates}"
    )
    assert isinstance(result.data.get("steps"), list), "失败返回体必须补 steps"
    assert isinstance(result.data.get("findings"), list), "失败返回体必须补 findings"


# ---------- 外层 except ----------

@pytest.mark.asyncio
async def test_f6_outer_except_runs_unified_closeout(monkeypatch):
    """编排循环抛出未预期异常（外层 except 兜底）：不得裸 AgentResult(error)，
    data 必须补齐 findings+observations+steps，统一收口 best-effort 留痕。"""
    agent = _make_agent(monkeypatch)

    async def _boom(messages, **kwargs):
        raise RuntimeError("unexpected orchestration boom")

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_boom))

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is False
    assert "boom" in (result.error or "")
    gates = _closeout_gates(result)
    assert "semgrep_leads" in gates, (
        f"外层 except 必须 best-effort 补跑统一收口，gates={gates}"
    )
    assert isinstance(result.data.get("findings"), list)
    assert isinstance(result.data.get("steps"), list)
