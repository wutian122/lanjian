"""空响应最后一搏（2026-09-29 层 3c）：一博测试。

背景：空响应（思考吃光预算/仅思考无正文）在矩阵移除前每轮烧 ~100s 才进入
上层止损。一搏 = 同轮内自动以「关思考 + 预算减半」重跑一次——几百 token
代价挽救一整轮；失败才交给上层空响应止损。

契约：
- stream_llm_call 第一遍空正文且无 tool_calls（且调用方未显式传关思考
  extra_params、本轮非 degenerate 崩坏）→ 自动重跑一次；
- 重跑参数：extra_params.chat_template_kwargs.enable_thinking=False；
  max_tokens = max(512, 原预算 // 2)；
- 第二次有产出 → 返回第二次结果；第二次仍空 → 返回空并保持形态分类。
"""
import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.base import AgentConfig, AgentPattern, AgentType
from app.services.agent.agents.recon import ReconAgent


@pytest.fixture(autouse=True)
def _patch_base_sleep(monkeypatch):
    """P5-1：救援退避 sleep mock（防真睡 45s）。"""
    import app.services.agent.agents.base as _base_mod
    monkeypatch.setattr(f"{_base_mod.__name__}.asyncio.sleep", AsyncMock())


def _make_agent() -> ReconAgent:
    agent = ReconAgent.__new__(ReconAgent)
    agent.config = AgentConfig(
        name="Recon", agent_type=AgentType.RECON,
        pattern=AgentPattern.REACT, max_iterations=5, system_prompt="x",
    )
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    agent._timeout_config = MagicMock()
    agent.task_id = "test-task-id"
    agent.llm_rate_per_minute = 60
    agent._last_empty_kind = None
    agent._last_tool_calls = None
    agent._last_llm_truncated = False
    agent._truncated_empty_streak = 0
    agent._stream_iter = None
    return agent


def _stream_with(content: str):
    async def _gen():
        yield {"type": "done", "content": content, "reasoning": "",
               "accumulated": content, "usage": None, "finish_reason": "stop"}
    return _gen()


class TestLastDitchEmptyRetry:
    def _agent_with_scripted_streams(self, agent, responses, captured):
        # 同步工厂函数：chat_completion_stream 返回 async generator 对象
        # （async def 包装会把返回值闷进 coroutine，触发 __aiter__ 失败）。
        def fake_stream(**kwargs):
            captured.append(kwargs)
            return responses[len(captured) - 1]

        agent.llm_service = MagicMock()
        agent.llm_service.chat_completion_stream = fake_stream
        agent.llm_service.config = MagicMock(max_tokens=8192)

    def test_second_ditch_attempt_rescues_round(self):
        agent = _make_agent()
        captured: List[Dict[str, Any]] = []
        self._agent_with_scripted_streams(
            agent, [_stream_with(""), _stream_with("ok decision")], captured)
        out, tokens = asyncio.run(agent.stream_llm_call(
            [{"role": "user", "content": "hi"}], max_tokens=2048))
        assert out.strip() == "ok decision"
        assert len(captured) == 2
        ditch_kwargs = captured[1]
        assert ditch_kwargs["extra_params"]["chat_template_kwargs"]["enable_thinking"] is False
        assert ditch_kwargs["max_tokens"] == max(512, 2048 // 2)

    def test_no_ditch_when_caller_disabled_thinking_explicitly(self):
        agent = _make_agent()
        captured: List[Dict[str, Any]] = []
        self._agent_with_scripted_streams(
            agent, [_stream_with(""), _stream_with("unused")], captured)
        out, _ = asyncio.run(agent.stream_llm_call(
            [{"role": "user", "content": "hi"}], max_tokens=2048,
            extra_params={"chat_template_kwargs": {"enable_thinking": False}}))
        assert out.strip() == ""
        assert len(captured) == 1  # 显式关思考仍空：不再一搏

    def test_second_attempt_empty_still_returns_empty(self):
        agent = _make_agent()
        captured: List[Dict[str, Any]] = []
        # P5-1 救援序列：原样 + 一搏 + 退避15s 纯文本 + 退避30s 纯文本 = 4 次
        self._agent_with_scripted_streams(
            agent, [_stream_with("") for _ in range(4)], captured)
        out, _ = asyncio.run(agent.stream_llm_call(
            [{"role": "user", "content": "hi"}], max_tokens=1024))
        assert out.strip() == ""
        assert len(captured) == 4, "P5-1 救援序列：原样+一搏+两轮退避 = 4 次调用"
        assert captured[1]["max_tokens"] == max(512, 1024 // 2)
        # P5-1：救援扩展为 4 段，tokens 累加（4 次空响应 × usage 记账）

    def test_tool_calls_round_not_treated_as_empty(self):
        agent = _make_agent()
        captured: List[Dict[str, Any]] = []

        async def _gen_with_tool():
            agent._last_tool_calls = [{"id": "t1", "type": "function",
                                       "function": {"name": "list_files",
                                                    "arguments": "{}"}}]
            yield {"type": "done", "content": "", "reasoning": "",
                   "accumulated": "", "usage": None, "finish_reason": "stop"}

        def fake_stream(**kwargs):
            captured.append(kwargs)
            return _gen_with_tool()

        agent.llm_service = MagicMock()
        agent.llm_service.chat_completion_stream = fake_stream
        agent.llm_service.config = MagicMock(max_tokens=8192)
        out, _ = asyncio.run(agent.stream_llm_call(
            [{"role": "user", "content": "hi"}], max_tokens=1024))
        assert len(captured) == 1  # 工具轮不触发一搏
        assert out == ""
