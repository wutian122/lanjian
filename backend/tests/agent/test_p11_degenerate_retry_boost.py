"""P11：崩坏重试差异化采样（打破同 prompt 确定性再崩的连续簇）。

数据依据（P10 spike 三档 12 任务实证）：59% 崩坏落在连续重试簇——同参数同
输入确定性再崩（fp8 权重漂移对相同 prompt 复现相同崩坏）。rp 参数整体定值
无效（1.1/1.2 三档非单调），但**崩坏后的临时差异化采样**可打破确定性循环：
崩坏（F1-F4 命中）后的重试请求注入 rp 增量（+0.15×boost，封顶 +0.45），
成功轮归零。差异化仅作用于崩坏后 1-3 次重试，请求级 extra_params 优先于
config（adapter setdefault 语义），成功后自然恢复用户配置。
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent() -> OrchestratorAgent:
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.config = MagicMock()
    agent.config.name = "Test"
    agent.config.agent_type = MagicMock(value="orchestrator")
    agent.config.repetition_penalty = 1.1
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    agent._timeout_config = MagicMock()
    agent.llm_service = MagicMock()
    agent._last_empty_kind = None
    agent._truncated_empty_streak = 0
    agent._degenerate_retry_boost = 0
    return agent


def _degenerate_stream(captured: Dict[str, Any]):
    """返回崩坏流 fake（F1 字符墙）并捕获调用 kwargs。"""

    def fake_stream(**kwargs):
        captured.update(kwargs)

        async def _gen():
            acc = ""
            for piece in ("正常思考开头。", "Z" * 45, "后面全部是崩坏输出" * 20):
                acc += piece
                yield {"type": "token", "kind": "content", "content": piece,
                       "accumulated": acc, "accumulated_content": acc,
                       "accumulated_reasoning": ""}
            yield {"type": "done", "content": "崩坏全文", "reasoning": "",
                   "accumulated": "崩坏全文", "usage": {"total_tokens": 10},
                   "finish_reason": "stop"}
        return _gen()
    return fake_stream


def _clean_stream(captured: Dict[str, Any]):
    def fake_stream(**kwargs):
        captured.update(kwargs)

        async def _gen():
            yield {"type": "token", "kind": "content", "content": "正常审计决策文本",
                   "accumulated": "正常审计决策文本", "accumulated_content": None,
                   "accumulated_reasoning": ""}
            yield {"type": "done", "content": "正常审计决策文本", "reasoning": "",
                   "accumulated": "正常审计决策文本",
                   "usage": {"total_tokens": 10}, "finish_reason": "stop"}
        return _gen()
    return fake_stream


async def _run(agent, fake_stream):
    agent.llm_service.chat_completion_stream = fake_stream
    with patch.object(agent, "_get_llm_rate_limiter",
                      return_value=MagicMock(acquire=AsyncMock())), \
         patch("app.services.agent.agents.base.get_llm_circuit") as fake_cb:
        fake_cb.return_value.call = lambda fn: fn()
        return await agent.stream_llm_call([{"role": "user", "content": "x"}])


class TestDegenerateRetryBoost:
    @pytest.mark.asyncio
    async def test_degenerate_increments_boost(self):
        agent = _make_agent()
        captured: Dict[str, Any] = {}
        await _run(agent, _degenerate_stream(captured))
        assert agent._last_empty_kind == "degenerate"
        assert agent._degenerate_retry_boost == 1

    @pytest.mark.asyncio
    async def test_next_call_injects_rp(self):
        agent = _make_agent()
        agent._degenerate_retry_boost = 1  # 模拟上一轮崩坏
        captured: Dict[str, Any] = {}
        await _run(agent, _clean_stream(captured))
        rp = captured.get("extra_params", {}).get("repetition_penalty")
        assert rp == pytest.approx(1.25), f"boost=1 应注入 base 1.1 + 0.15 = 1.25，实际 {rp}"

    @pytest.mark.asyncio
    async def test_boost_scaled(self):
        agent = _make_agent()
        agent._degenerate_retry_boost = 3
        captured: Dict[str, Any] = {}
        await _run(agent, _clean_stream(captured))
        rp = captured.get("extra_params", {}).get("repetition_penalty")
        assert rp == pytest.approx(1.55), "boost=3 应注入 1.1 + 0.45 = 1.55"

    @pytest.mark.asyncio
    async def test_clean_round_resets_boost(self):
        agent = _make_agent()
        agent._degenerate_retry_boost = 2
        captured: Dict[str, Any] = {}
        await _run(agent, _clean_stream(captured))
        assert agent._degenerate_retry_boost == 0, "成功轮必须归零（恢复用户参数）"

    @pytest.mark.asyncio
    async def test_boost_capped_at_3(self):
        agent = _make_agent()
        agent._degenerate_retry_boost = 3
        captured: Dict[str, Any] = {}
        await _run(agent, _degenerate_stream(captured))
        assert agent._degenerate_retry_boost == 3, "封顶 3（+0.45），不无限增强"

    @pytest.mark.asyncio
    async def test_rp_none_falls_back_to_settings(self):
        agent = _make_agent()
        agent.config.repetition_penalty = None
        agent._degenerate_retry_boost = 1
        captured: Dict[str, Any] = {}
        await _run(agent, _clean_stream(captured))
        rp = captured.get("extra_params", {}).get("repetition_penalty")
        assert rp == pytest.approx(1.35), "config rp=None 应回落 settings 默认 1.2 + 0.15"

    @pytest.mark.asyncio
    async def test_no_boost_no_injection(self):
        agent = _make_agent()  # boost=0
        captured: Dict[str, Any] = {}
        await _run(agent, _clean_stream(captured))
        assert "repetition_penalty" not in (captured.get("extra_params") or {}), \
            "正常路径不得污染请求参数"
