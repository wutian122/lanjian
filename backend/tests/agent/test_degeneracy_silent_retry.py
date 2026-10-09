"""L2：生成崩坏萌芽检测 + 静默重试测试。

背景：Qwen3.8-27B 概率性采样崩坏（字符墙/循环，约 5-10% 轮次，实验实证无法
从输入侧消除）。L2 目标：崩坏文本不推给用户——流式接收中检测萌芽特征，命中
即中止本轮、丢弃本轮内容、按"无效轮"走既有重试/止损流（静默重试）。

契约：
- BaseAgent._detect_output_degeneracy(text)：
  * 同字符连续 run >= 30（字符墙萌芽）→ True；
  * 尾 500 字符中存在 >= 25 字符重复子串（循环）→ True；
  * 正常审计文本（含代码分隔线/常规重复短语）→ False；
- stream_llm_call：检测命中 → 本轮返回 ("", tokens)（丢弃崩坏内容），
  _last_empty_kind="degenerate"（供 nudge/止损消费），已发射的
  content_end 不携带崩坏文本；
- record_empty_round：degenerate 与 truncated 同样计入连续止损计数。
"""

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent(agent_type_value: str = "orchestrator") -> OrchestratorAgent:
    # BaseAgent 为抽象类（run 未实现），用具体子类 __new__ 绕过构造
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.config = MagicMock()
    agent.config.name = "Test"
    agent.config.agent_type = MagicMock(value=agent_type_value)
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    agent._timeout_config = MagicMock()
    agent.llm_service = MagicMock()
    agent._last_empty_kind = None
    agent._truncated_empty_streak = 0
    return agent


def _done(content: str, finish: str = "stop") -> Dict[str, Any]:
    return {"type": "done", "content": content, "reasoning": "",
            "accumulated": content, "usage": {"total_tokens": 10},
            "finish_reason": finish}


class TestDetectOutputDegeneracy:
    def test_charwall_detected(self):
        agent = _make_agent()
        text = "正常审计分析文本。" + "A" * 35 + "继续"
        assert agent._detect_output_degeneracy(text) is True

    def test_loop_detected(self):
        agent = _make_agent()
        frag = "international law human rights rule of lawdemocracy"[:30]
        text = "分析开始。" + (frag + frag + frag + frag)[:300] + "结尾"
        assert agent._detect_output_degeneracy(text) is True

    def test_normal_text_not_flagged(self):
        agent = _make_agent()
        text = (
            "## 分析结论\n\n"
            "1. src/http/ngx_http_request.c:88 存在路径拼接风险（confidence 0.72）\n"
            "2. 建议 dispatch verification 验证该候选\n"
            "3. " + "-" * 28 + " 分隔线\n"
            "4. 常规重复短语：需要关注需要关注（两次，正常强调）\n"
        )
        assert agent._detect_output_degeneracy(text) is False

    def test_empty_and_short_text_not_flagged(self):
        agent = _make_agent()
        assert agent._detect_output_degeneracy("") is False
        assert agent._detect_output_degeneracy("正常短文本") is False


class TestStreamSilentRetry:
    def _stream_agent(self, scripted: List[List[Dict[str, Any]]]):
        """构造 agent，chat_completion_stream 按 scripted 顺序返回每轮 chunk 流"""
        agent = _make_agent()
        state = {"i": 0}

        def fake_stream(**kwargs):
            rounds = scripted
            idx = state["i"]
            state["i"] += 1
            chunks = rounds[min(idx, len(rounds) - 1)]

            async def _gen():
                for c in chunks:
                    yield dict(c)
            return _gen()

        agent.llm_service = MagicMock()
        agent.llm_service.chat_completion_stream = fake_stream
        return agent

    @pytest.mark.asyncio
    async def test_degenerate_round_discarded_and_flagged(self):
        """字符墙轮：内容丢弃返回空串，形态标记 degenerate"""
        agent = _make_agent()
        wall = "正常思考开头。" + "Z" * 40 + "后继文本应当被丢弃" * 5
        agent.llm_service = MagicMock()

        def fake_stream(**kwargs):
            async def _gen():
                acc = ""
                for piece in ("正常思考开头。", "Z" * 45, "后面全部是崩坏输出" * 20):
                    acc += piece
                    yield {"type": "token", "kind": "content", "content": piece,
                           "accumulated": acc, "accumulated_content": acc,
                           "accumulated_reasoning": ""}
                yield _done("崩坏全文应当被丢弃")
            return _gen()
        agent.llm_service.chat_completion_stream = fake_stream

        with patch.object(agent, "_get_llm_rate_limiter",
                          return_value=MagicMock(acquire=AsyncMock())), \
             patch("app.services.agent.agents.base.get_llm_circuit") as fake_cb:
            fake_cb.return_value.call = lambda fn: fn()
            out, tokens = await agent.stream_llm_call([{"role": "user", "content": "x"}])

        assert out == "", "崩坏轮内容必须整体丢弃（返回空串走重试流）"
        assert agent._last_empty_kind == "degenerate"
        assert agent._truncated_empty_streak == 0, "止损计数由上层 record_empty_round 消费，检测点不自行累加"

    @pytest.mark.asyncio
    async def test_clean_round_unaffected(self):
        """正常流零影响：内容原样返回，无 degenerate 标记"""
        agent = _make_agent()
        agent.llm_service = MagicMock()

        def fake_stream(**kwargs):
            async def _gen():
                yield {"type": "token", "kind": "content", "content": "正常审计决策文本",
                       "accumulated": "正常审计决策文本", "accumulated_content": None,
                       "accumulated_reasoning": ""}
                yield _done("正常审计决策文本")
            return _gen()
        agent.llm_service.chat_completion_stream = fake_stream

        with patch.object(agent, "_get_llm_rate_limiter",
                          return_value=MagicMock(acquire=AsyncMock())), \
             patch("app.services.agent.agents.base.get_llm_circuit") as fake_cb:
            fake_cb.return_value.call = lambda fn: fn()
            out, _ = await agent.stream_llm_call([{"role": "user", "content": "x"}])

        assert out == "正常审计决策文本"
        assert agent._last_empty_kind is None


class TestDegenerateStopLoss:
    def test_degenerate_counts_toward_stop(self):
        """degenerate 形态计入连续止损计数（与 truncated 同权重；P5-1 阈值 3→5）"""
        agent = _make_agent()
        for kind in ("degenerate", "degenerate", "degenerate", "degenerate"):
            agent._last_empty_kind = kind
            assert agent.record_empty_round() is False
        agent._last_empty_kind = "degenerate"
        assert agent.record_empty_round() is True

    def test_truncated_streak_not_broken_by_degenerate(self):
        agent = _make_agent()
        for _ in range(4):
            agent._last_empty_kind = "truncated"
            agent.record_empty_round()
        agent._last_empty_kind = "degenerate"
        assert agent.record_empty_round() is True, "两种崩坏形态共享止损预算"
