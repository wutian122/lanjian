"""思考策略统一（2026-09-29 根治）：一切 agent 不再静默请求开思考。

背景（2026-09-27～09-29 生产实证，任务 c6d6cd09 / 327b6430）：
思考矩阵（选项 a）强制 recon/analysis/verification enable_thinking=True，
实测 10.129.2.101（SGLang 部署 Qwen3_5 魔改权重）思考流 8192 tokens 仍不
收敛、正文 0 字、finish_reason=length——子 Agent 三轮全空响应回退，0 finding。
对照实验：enable_thinking=False 时正文完整输出、自然停。

契约（单真相源）：
- `_thinking_override_for` 对任意 agent_type 恒返回 None——思考开关仅由
  全局环境变量治理（默认关；显式 LLM_ENABLE_THINKING + 服务端分离预算
  支持时才开，见 tests/llm/test_thinking_off_guard.py）；
- 所有 Agent 的 stream_llm_call 默认不带 extra_params——请求签名不出
  现 enable_thinking 键，交由 adapter 全局注入层裁决。
"""

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent
from app.services.agent.agents.base import AgentConfig, AgentPattern, AgentType
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.agents.verification import VerificationAgent


class TestThinkingOverrideRemoved:
    """思考矩阵已移除：_thinking_override_for 恒返回 None。"""

    @pytest.mark.parametrize("agent_type", [
        AgentType.RECON, AgentType.ANALYSIS, AgentType.VERIFICATION,
        AgentType.ORCHESTRATOR, "recon", "analysis", "verification", "orchestrator",
        "unknown", None,
    ])
    def test_no_override_for_any_type(self, agent_type: Any):
        from app.services.agent.agents.base import BaseAgent

        assert BaseAgent._thinking_override_for(agent_type) is None

    def test_deep_agents_default_no_extra_params(self):
        """recon/analysis/verification 不再隐式携带 chat_template_kwargs。"""
        for cls in (ReconAgent, AnalysisAgent, VerificationAgent, OrchestratorAgent):
            agent = cls.__new__(cls)
            agent.config = AgentConfig(
                name="t", agent_type=AgentType.VERIFICATION,
                pattern=AgentPattern.REACT, max_iterations=5, system_prompt="x",
            )
            agent.event_emitter = MagicMock()
            agent.event_emitter.emit = AsyncMock()
            agent._timeout_config = MagicMock()
            captured: Dict[str, Any] = {}

            async def fake_stream(**kwargs):
                captured.update(kwargs)

                async def _gen():
                    yield {"type": "done", "content": "ok", "reasoning": "",
                           "accumulated": "ok", "usage": None, "finish_reason": "stop"}
                return _gen()

            agent.llm_service = MagicMock()
            agent.llm_service.chat_completion_stream = fake_stream
            agent.llm_service.config = MagicMock()
            agent.llm_service.config.max_tokens = 8192

            asyncio.run(agent.stream_llm_call(
                [{"role": "user", "content": "hi"}], max_tokens=128))
            # extra_params 为 None 时不得出现在调用签名（R-C2 兼容约束）
            assert "extra_params" not in captured
