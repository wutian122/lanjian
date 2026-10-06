"""P5-1 空响应退避重试（2026-10-04，任务 1fe2d9ce 实证）。

生产实证：LLM 服务瞬时过载（四任务并发）→ 连续空响应（每轮 2-4 秒
快速返回）→ 连续 3 轮即止损 → 编排提前收口。过载窗口通常 <2 分钟，
止损太快 = 白白夭折。

契约：
- `_empty_response_backoff(attempt)`：1→15s、2→30s、≥3→60s（封顶）；
- stream_llm_call 空响应救援序列扩展：原样 → 一搏（关思考+减半）→
  退避 15s + 纯文本重跑（去 tools）→ 退避 30s + 纯文本重跑 → 仍空才
  返回空给上层；
- 上层止损阈值 3→5（TRUNCATED_EMPTY_STOP_LIMIT），配合退避拉开空响应
  时间间隔，扛过过载窗口。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.base import BaseAgent
from app.services.agent.agents.recon import ReconAgent


def _make_agent():
    agent = ReconAgent.__new__(ReconAgent)
    agent.config = MagicMock()
    agent.config.name = "Recon"
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    agent._timeout_config = MagicMock()
    agent.task_id = "t-p5"
    agent.llm_rate_per_minute = 60
    agent._last_empty_kind = None
    agent._last_tool_calls = None
    agent._last_llm_truncated = False
    agent._truncated_empty_streak = 0
    agent._stream_iter = None
    return agent


class TestEmptyResponseBackoff:
    def test_backoff_schedule(self):
        assert BaseAgent._empty_response_backoff(1) == 15
        assert BaseAgent._empty_response_backoff(2) == 30
        assert BaseAgent._empty_response_backoff(3) == 60
        assert BaseAgent._empty_response_backoff(4) == 60

    def test_stop_limit_raised_to_five(self):
        assert BaseAgent.TRUNCATED_EMPTY_STOP_LIMIT == 5


class TestRescueSequence:
    def _agent_with_streams(self, agent, gens, captured):
        def fake_stream(**kwargs):
            captured.append(kwargs)
            return gens[len(captured) - 1]

        agent.llm_service = MagicMock()
        agent.llm_service.chat_completion_stream = fake_stream
        agent.llm_service.config = MagicMock(max_tokens=8192)

    @staticmethod
    def _empty_gen():
        async def _gen():
            yield {"type": "done", "content": "", "reasoning": "",
                   "accumulated": "", "usage": None, "finish_reason": "stop"}
        return _gen()

    @staticmethod
    def _text_gen(text):
        async def _gen():
            yield {"type": "done", "content": text, "reasoning": "",
                   "accumulated": text, "usage": {"total_tokens": 10},
                   "finish_reason": "stop"}
        return _gen()

    def test_full_rescue_sequence_with_backoff_and_plain_text(self):
        agent = _make_agent()
        captured = []
        self._agent_with_streams(
            agent,
            [self._empty_gen(), self._empty_gen(),      # 原样 + 一搏
             self._empty_gen(),                          # 退避 15s 纯文本重跑
             self._text_gen("recovered final answer")],  # 退避 30s 纯文本重跑
            captured)
        out, _ = asyncio.run(agent.stream_llm_call(
            [{"role": "user", "content": "hi"}], max_tokens=2048))
        assert out == "recovered final answer"
        assert len(captured) == 4
        # 第 3/4 次调用（退避重跑）必须是纯文本（去 tools）
        assert "tools" not in captured[2] or captured[2].get("tools") in (None, [])
        assert "tools" not in captured[3] or captured[3].get("tools") in (None, [])

    def test_all_rescue_attempts_fail_returns_empty(self):
        agent = _make_agent()
        captured = []
        self._agent_with_streams(
            agent,
            [self._empty_gen() for _ in range(4)],
            captured)
        out, _ = asyncio.run(agent.stream_llm_call(
            [{"role": "user", "content": "hi"}], max_tokens=1024))
        assert out.strip() == ""
        assert len(captured) == 4
