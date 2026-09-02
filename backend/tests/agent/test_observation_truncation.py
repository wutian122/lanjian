"""
Task 3（fix-audit-observability-time-governance）：Analysis observation 截断

共享实现：BaseAgent._truncate_head_tail(text, max_chars)（从 verification.py
_truncate_observation_for_history 提取，行为完全不变：超长时保头 1500 + 尾 1500 +
中间省略标注，小配置值有 budget 防御）。

Scenario 覆盖（spec: Analysis 循环 observation 注入历史前 MUST 截断）：
- 50000 字符 observation → history 中该条 ≤4000 字符、保头尾、含省略标注；
  完整 observation 仍进入 _steps（orchestrator 上报不受影响）
- 500 字符短输出 → 原样追加，无截断标注
- 共享实现单元语义：边界值、None/空、小 max_chars 防御
- verification 切换共享实现后行为不变（wrapper 与共享函数逐输入对照）
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent.agents.base import BaseAgent

# ---------- 共享实现单元测试 ----------

def test_shared_truncate_long_keeps_head_tail_marker_and_omitted_count():
    """50000 字符 → 头 1500 + 尾 1500 + 省略标注，omitted 计数准确，总长受控。"""
    text = "HEAD" + "A" * 49988 + "TAILMARK"  # 恰好 50000 字符
    assert len(text) == 50000
    out = BaseAgent._truncate_head_tail(text, 4000)
    # 总长：1500 + 标注 + 1500，远小于 4000 阈值
    assert len(out) < 3600, f"截断后应保头尾 1500+1500+标注，实际 {len(out)}"
    # 头部内容保留（命令/目标上下文在头部）
    assert out.startswith("HEAD" + "A" * 1496), "头部 1500 字符必须保留"
    # 尾部内容保留（PoC 铁证标记通常在尾部）
    assert out.endswith("A" * 1492 + "TAILMARK"), "尾部 1500 字符必须保留"
    # 省略标注与 omitted 计数（50000 - 1500 - 1500 = 47000）
    assert "observation truncated" in out
    assert "47000 chars omitted" in out


def test_shared_truncate_short_and_boundary_passthrough():
    """短文本与边界值原样返回；None/空输入安全处理。"""
    short = "B" * 500
    assert BaseAgent._truncate_head_tail(short, 4000) == short
    # 恰好等于阈值也原样（语义：len <= max_chars 不截断）
    boundary = "C" * 4000
    assert BaseAgent._truncate_head_tail(boundary, 4000) == boundary
    assert "truncated" not in BaseAgent._truncate_head_tail(short, 4000)
    # None / 空串 → 空串，不抛异常
    assert BaseAgent._truncate_head_tail(None, 4000) == ""
    assert BaseAgent._truncate_head_tail("", 4000) == ""


def test_shared_truncate_small_max_chars_defense():
    """max_chars 配置过小时收缩头尾（budget=max_chars-200，下限 200），不产生负 omitted/重复内容。"""
    text = "X" * 5000
    out = BaseAgent._truncate_head_tail(text, 500)
    # budget=300 → head=tail=150
    assert out.startswith("X" * 150)
    assert out.endswith("X" * 150)
    assert "observation truncated" in out
    assert "4700 chars omitted" in out
    # 极小配置：budget 下限 200 → head=tail=100，不崩溃
    out_tiny = BaseAgent._truncate_head_tail(text, 100)
    assert out_tiny.startswith("X" * 100)
    assert out_tiny.endswith("X" * 100)
    assert "observation truncated" in out_tiny


# ---------- verification 切换共享实现后行为不变 ----------

def test_verification_wrapper_matches_shared_impl():
    """verification._truncate_observation_for_history 与共享实现对同组输入输出完全一致。"""
    from app.services.agent.agents.verification import VerificationAgent

    # 照 test_verification_evidence._make_agent 的 __new__ 轻量构造（截断方法不依赖实例状态）
    agent = VerificationAgent.__new__(VerificationAgent)

    class _Cfg:
        name = "Verification"

    agent.config = _Cfg()
    cases = [
        "",
        "short",
        "A" * 2000 + "B" * 2000 + "C" * 2000,  # 6000（既有测试用例）
        "HEAD" + "A" * 49992 + "TAIL",         # 50000
        "D" * 4000,                            # 边界
        "E" * 4001,                            # 刚超阈值
        "中文" * 3000,                          # 多字节字符按 len() 计数语义不变
    ]
    for text in cases:
        wrapped = agent._truncate_observation_for_history(text)
        shared = BaseAgent._truncate_head_tail(str(text or ""), 4000)
        assert wrapped == shared, (
            f"verification wrapper 行为必须与共享实现一致（输入长度 {len(str(text or ''))}）"
        )


# ---------- Analysis 集成：history 截断、_steps 完整 ----------

def _make_analysis_agent(monkeypatch):
    """轻量构造 AnalysisAgent（照 Task 2 test_llm_truncation_visibility 的 mock 模式）。"""
    from app.services.agent.agents.analysis import AnalysisAgent

    agent = AnalysisAgent(llm_service=SimpleNamespace(), tools={})
    for name in (
        "emit_thinking", "emit_event", "emit_llm_decision", "emit_llm_thought",
        "emit_finding", "emit_llm_action", "emit_llm_observation", "emit_llm_complete",
    ):
        monkeypatch.setattr(agent, name, AsyncMock())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    agent.config.max_iterations = 5
    return agent


def _tool_then_final_answer_stream():
    """第 1 轮返回 read_file 动作，第 2 轮起返回含 1 条 finding 的 Final Answer（避免强制总结轮干扰）。"""
    calls = {"n": 0}

    async def _fake_stream(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                'Thought: 读取大文件\n'
                'Action: read_file\n'
                'Action Input: {"file_path": "big.py"}',
                10,
            )
        return (
            'Thought: 分析完成\n'
            'Final Answer: {"findings": [{"title": "t", "severity": "info"}], "summary": "done"}',
            10,
        )

    return _fake_stream


def _observation_messages(agent):
    """history 中工具 observation 条目（content 以 Observation: 开头）。"""
    return [
        m for m in agent._conversation_history
        if m.get("role") == "user" and m.get("content", "").startswith("Observation:\n")
    ]


@pytest.mark.asyncio
async def test_analysis_long_observation_truncated_in_history_full_in_steps(monkeypatch):
    """50000 字符 observation：history 条目 ≤4000 且保头尾含标注；_steps 中保留完整原文。"""
    agent = _make_analysis_agent(monkeypatch)
    long_obs = "HEAD" + "A" * 49988 + "TAILMARK"
    assert len(long_obs) == 50000

    monkeypatch.setattr(agent, "stream_llm_call", _tool_then_final_answer_stream())
    monkeypatch.setattr(agent, "execute_tool", AsyncMock(return_value=long_obs))

    result = await agent.run({"project_info": {}, "config": {}})
    assert result.success is True

    obs_msgs = _observation_messages(agent)
    assert len(obs_msgs) == 1, f"应恰好 1 条 observation 入历史，实际 {len(obs_msgs)}"
    obs_in_history = obs_msgs[0]["content"][len("Observation:\n"):]
    # spec Scenario：追加进 conversation_history 的 observation ≤ 4000 字符（保头尾）
    assert len(obs_in_history) <= 4000, f"history 中 observation 必须 ≤4000，实际 {len(obs_in_history)}"
    assert len(obs_in_history) < 3600
    assert "observation truncated" in obs_in_history
    assert "47000 chars omitted" in obs_in_history
    assert obs_in_history.startswith("HEAD"), "头部内容必须保留"
    assert obs_in_history.endswith("TAILMARK"), "尾部内容必须保留（铁证在尾部）"

    # 完整 observation 仍进入 _steps（上报 orchestrator 的数据不受影响）
    tool_steps = [s for s in agent._steps if getattr(s, "action", None) == "read_file"]
    assert len(tool_steps) == 1
    assert tool_steps[0].observation == long_obs, "_steps 必须保留完整 observation（不得截断）"
    assert len(tool_steps[0].observation) == 50000


@pytest.mark.asyncio
async def test_analysis_short_observation_passthrough_no_marker(monkeypatch):
    """500 字符短输出：原样追加进 history，无截断标注；_steps 同样完整。"""
    agent = _make_analysis_agent(monkeypatch)
    short_obs = "B" * 500

    monkeypatch.setattr(agent, "stream_llm_call", _tool_then_final_answer_stream())
    monkeypatch.setattr(agent, "execute_tool", AsyncMock(return_value=short_obs))

    result = await agent.run({"project_info": {}, "config": {}})
    assert result.success is True

    obs_msgs = _observation_messages(agent)
    assert len(obs_msgs) == 1
    # 原样追加，无任何截断标注
    assert obs_msgs[0]["content"] == "Observation:\n" + short_obs
    assert "truncated" not in obs_msgs[0]["content"]

    tool_steps = [s for s in agent._steps if getattr(s, "action", None) == "read_file"]
    assert tool_steps[0].observation == short_obs
