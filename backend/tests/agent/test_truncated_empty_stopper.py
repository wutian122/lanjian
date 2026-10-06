"""连续 truncated 空响应止损（R-C1）测试。

背景（2026-09-26 生产实证）：Qwen3.8-27B 思考流吃光 max_tokens → truncated
形态空响应 → Orchestrator 重试（每轮约 100 秒 / 4k-10k tokens）→ 任务
c0c6182f 连续 6 轮空转后才靠累计 5 次上限兜底。现有上限不区分形态、累计不
重置，系统性工况（预算饥饿）下重试必然同样失败，纯烧资源。

行为契约：
- BaseAgent.record_empty_round()：truncated 形态空响应连续计数，达
  TRUNCATED_EMPTY_STOP_LIMIT（默认 3）返回 True（止损）；
- reasoning_only / other 等其他空响应形态不计入（沿用既有累计 5 次上限）；
- BaseAgent.reset_empty_streak()：非空轮调用，重置连续计数；
- 止损命中后 Orchestrator 停止重试（收口路径沿用既有 break 语义）。
"""

import pytest

from app.services.agent.agents.base import BaseAgent
from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent() -> OrchestratorAgent:
    """绕过 __init__ 构造，手动设置止损依赖的实例属性。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._truncated_empty_streak = 0
    agent._last_empty_kind = None
    return agent


class TestTruncatedEmptyStopper:
    def test_three_consecutive_truncated_hits_limit(self):
        """P5-1：连续 5 轮 truncated 空响应 → 第 5 轮触发止损（阈值 3→5）"""
        agent = _make_agent()
        results = []
        for _ in range(5):
            agent._last_empty_kind = "truncated"
            results.append(agent.record_empty_round())

        assert results == [False, False, False, False, True]

    def test_reasoning_only_does_not_count(self):
        """reasoning_only 形态不计入连续 truncated（沿用既有 5 次累计上限）"""
        agent = _make_agent()
        agent._last_empty_kind = "truncated"
        assert agent.record_empty_round() is False
        agent._last_empty_kind = "reasoning_only"
        assert agent.record_empty_round() is False
        agent._last_empty_kind = "other"
        assert agent.record_empty_round() is False
        # reasoning_only / other 不打断也不累加 truncated 连续计数：
        # truncated 总共 5 次（1+3），第 5 次触发止损
        for _ in range(3):
            agent._last_empty_kind = "truncated"
            assert agent.record_empty_round() is False
        agent._last_empty_kind = "truncated"
        assert agent.record_empty_round() is True

    def test_non_empty_round_resets_streak(self):
        """非空轮（正常产出正文/工具调用）重置连续计数"""
        agent = _make_agent()
        agent._last_empty_kind = "truncated"
        agent.record_empty_round()
        agent.record_empty_round()
        # 模型恢复：一轮正常产出
        agent.reset_empty_streak()
        # 再次进入 truncated 空响应：重新计数
        for _ in range(4):
            agent._last_empty_kind = "truncated"
            assert agent.record_empty_round() is False
        agent._last_empty_kind = "truncated"
        assert agent.record_empty_round() is True

    def test_base_agent_method_available_on_base_class(self):
        """止损方法挂在 BaseAgent（recon/analysis/verification 同样受保护）"""
        assert hasattr(BaseAgent, "record_empty_round")
        assert hasattr(BaseAgent, "reset_empty_streak")
        assert BaseAgent.TRUNCATED_EMPTY_STOP_LIMIT == 5
