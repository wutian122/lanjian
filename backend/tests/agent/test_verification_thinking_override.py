"""差异化关思考（R-C2）测试：verification 保留思考，其余 agent 关思考。

背景（2026-09-26 生产实证，任务 668142b7 / a60e39be）：全局 LLM_DISABLE_THINKING
根治了 Orchestrator/Analysis 的思考失控，但 **Verification 深度验证任务被误伤**——
关思考后模型对验证决策"秒停空转"（连环 Empty，2 秒/轮，44 连发），无沙箱证据 →
门禁 3 次拒绝 → completed_with_gaps 降级。

契约：
- VerificationAgent.stream_llm_call 显式传 extra_params=
  {"chat_template_kwargs": {"enable_thinking": True}}——adapter merge 的
  setdefault 语义保证显式 True 覆盖全局注入的 False（护栏不剥开方向值）；
- 其他 agent（recon/orchestrator/analysis）不传 extra_params——维持全局关思考；
- adapter：开关开启且请求显式 enable_thinking=True → 端点收到 True。

单测占位 API Key 从环境变量读取（无真实凭据；适配器仅校验非空）。
"""

import asyncio
import os
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents.verification import VerificationAgent

DUMMY_API_KEY = os.environ.get("LANJIAN_TEST_DUMMY_KEY", "unit-test-dummy-key")
THINKING_ON = {"chat_template_kwargs": {"enable_thinking": True}}


def _make_verification_agent() -> VerificationAgent:
    # 生产同构：真实 AgentConfig（agent_type 为枚举——与 verification.py 构造一致）
    from app.services.agent.agents.base import AgentConfig, AgentPattern

    from app.services.agent.agents.base import AgentType

    agent = VerificationAgent.__new__(VerificationAgent)
    agent.config = AgentConfig(
        name="Verification",
        agent_type=AgentType.VERIFICATION,
        pattern=AgentPattern.REACT,
        max_iterations=10,
        system_prompt="test",
    )
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    agent._timeout_config = MagicMock()
    agent._last_empty_kind = None
    agent._truncated_empty_streak = 0
    return agent


def _make_llm_service_capture(captured: Dict[str, Any]):
    svc = MagicMock()

    def fake_stream(**kwargs):
        captured.update(kwargs)

        async def _gen():
            yield {"type": "done", "content": "ok", "reasoning": "",
                   "accumulated": "ok", "usage": None, "finish_reason": "stop"}
        return _gen()

    svc.chat_completion_stream = fake_stream
    return svc


class TestThinkingMatrix:
    """思考矩阵（2026-09-27 选项 a）：recon/analysis/verification 开思考，
    仅 orchestrator（调度决策）与未知类型维持关思考。
    直接测纯函数 `_thinking_override_for`（无 mock 依赖，稳定）。"""

    def _agent_cls(self, name: str):
        from app.services.agent.agents.analysis import AnalysisAgent
        from app.services.agent.agents.orchestrator import OrchestratorAgent
        from app.services.agent.agents.recon import ReconAgent
        from app.services.agent.agents.verification import VerificationAgent

        return {"analysis": AnalysisAgent, "orchestrator": OrchestratorAgent,
                "recon": ReconAgent, "verification": VerificationAgent}[name]

    @pytest.mark.parametrize("agent_name,agent_type", [
        ("recon", "recon"),
        ("analysis", "analysis"),
        ("verification", "verification"),
    ])
    def test_deep_agents_request_thinking(self, agent_name: str, agent_type):
        """recon/analysis/verification 的请求级思考覆盖（枚举与 str 双形态）"""
        from app.services.agent.agents.base import BaseAgent

        agent_cls = self._agent_cls(agent_name)
        agent = agent_cls.__new__(agent_cls)
        agent.config = MagicMock()
        agent.config.agent_type = agent_type
        override = BaseAgent._thinking_override_for(agent.config.agent_type)
        assert override == {"chat_template_kwargs": {"enable_thinking": True}}, \
            f"{agent_name} 必须开思考"
        # str 形态等价
        assert BaseAgent._thinking_override_for(agent_type) == override

    def test_orchestrator_stays_thinking_off(self):
        """orchestrator（调度短决策）维持关思考——注入返回 None（走全局）"""
        from app.services.agent.agents.base import BaseAgent, AgentType
        from app.services.agent.agents.orchestrator import OrchestratorAgent

        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent.config = MagicMock()
        agent.config.agent_type = AgentType.ORCHESTRATOR
        assert BaseAgent._thinking_override_for(agent.config.agent_type) is None
        assert BaseAgent._thinking_override_for("orchestrator") is None

    def test_unknown_type_stays_thinking_off(self):
        """未知类型保守默认 None（与全局一致，不意外开思考）"""
        from app.services.agent.agents.base import BaseAgent

        assert BaseAgent._thinking_override_for("mystery-agent") is None
        assert BaseAgent._thinking_override_for(None) is None


class TestVerificationThinkingOverride:
    @pytest.mark.asyncio
    async def test_verification_requests_thinking(self):
        """verification 的 LLM 调用必须显式请求 enable_thinking=True"""
        agent = _make_verification_agent()
        captured: Dict[str, Any] = {}
        agent.llm_service = _make_llm_service_capture(captured)

        with patch.object(agent, "_get_llm_rate_limiter",
                          return_value=MagicMock(acquire=AsyncMock())), \
             patch("app.services.agent.agents.base.get_llm_circuit") as fake_cb:
            fake_cb.return_value.call = lambda fn: fn()
            out, tokens = await agent.stream_llm_call(
                [{"role": "user", "content": "验证 1 个候选"}],
                tools=[{"type": "function", "function": {"name": "submit_findings"}}],
            )

        assert out == "ok"
        assert captured.get("extra_params") == THINKING_ON

    @pytest.mark.asyncio
    async def test_other_agents_default_no_extra_params(self):
        """BaseAgent 默认不传 extra_params——其他 agent 维持全局关思考"""
        from app.services.agent.agents.recon import ReconAgent

        agent = ReconAgent.__new__(ReconAgent)
        agent.config = MagicMock()
        agent.config.name = "Recon"
        agent.event_emitter = MagicMock()
        agent.event_emitter.emit = AsyncMock()
        agent._timeout_config = MagicMock()
        agent._last_empty_kind = None
        agent._truncated_empty_streak = 0
        captured: Dict[str, Any] = {}
        agent.llm_service = _make_llm_service_capture(captured)

        with patch.object(agent, "_get_llm_rate_limiter",
                          return_value=MagicMock(acquire=AsyncMock())), \
             patch("app.services.agent.agents.base.get_llm_circuit") as fake_cb:
            fake_cb.return_value.call = lambda fn: fn()
            await agent.stream_llm_call([{"role": "user", "content": "侦察"}])

        assert captured.get("extra_params") is None


class TestAdapterExplicitThinkingWins:
    @pytest.mark.asyncio
    async def test_explicit_true_survives_global_switch(self):
        """开关开启 + 请求级显式 enable_thinking=True → 端点收到 True（setdefault 语义）"""
        from app.core.config import settings
        from app.services.llm.types import (
            LLMConfig,
            LLMMessage,
            LLMProvider,
            LLMRequest,
        )
        from unittest.mock import patch as _patch

        captured: Dict[str, Any] = {}
        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = "ok"
        fake_response.choices[0].finish_reason = "stop"
        fake_response.model = "q"
        fake_response.usage = MagicMock(prompt_tokens=1, completion_tokens=2, total_tokens=3)

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return fake_response

        cfg = LLMConfig(provider=LLMProvider.QWEN, api_key=DUMMY_API_KEY,
                        model="Qwen3.8-27B", base_url="http://x/v1",
                        timeout=10, max_tokens=64)
        with _patch.object(settings, "LLM_DISABLE_THINKING", True), \
             _patch("litellm.acompletion", side_effect=fake_acompletion):
            adapter = __import__(
                "app.services.llm.adapters.litellm_adapter",
                fromlist=["LiteLLMAdapter"],
            ).LiteLLMAdapter(cfg)
            await adapter.complete(LLMRequest(
                messages=[LLMMessage(role="user", content="hi")],
                temperature=0.1, max_tokens=64,
                extra_params=THINKING_ON,
            ))

        ct = (captured.get("extra_body") or {}).get("chat_template_kwargs") or {}
        assert ct.get("enable_thinking") is True, "请求级显式开思考必须覆盖全局关思考"
