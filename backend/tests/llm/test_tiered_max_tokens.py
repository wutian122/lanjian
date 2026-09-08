"""
W1: 分阶段 max_tokens——按 Agent 类型差异化输出预算

老板生产观察（fp8 内网推理）：
- 2048：ReAct 中间轮短决策稳定，胡乱输出大减；
- 4096：reasoning 思考流吃光预算，正文空响应；
- 32768：长输出漂移 + fp8 KV 累积误差，胡乱输出增多。

单一全局值无法同时满足「中间轮要短稳」与「报告轮要空间」，落地映射：
- orchestrator / recon = 2048（调度决策/侦察短输出，防长输出漂移）；
- analysis / verification = 8192（验证结论与 submit_findings 报告需空间：
  单 finding ≈900 tokens × 8 ≈ 7200 + JSON 结构 < 8192）；
- analysis 强制总结轮（guided_json 一次性全量 findings JSON）= 32768
  （schema 约束漂移面小，预算留大防截断）；
- 显式传参优先于映射；未知类型无映射 → None 回退用户全局 llmMaxTokens。

submit_findings 轮与中间 ReAct 轮在请求时同构（都是 tools 形态工具调用），
无法请求时区分，故 analysis 中间轮统一 8192；极端大报告由强制总结轮 32768 兜底。
"""
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.agents.verification import VerificationAgent
from app.services.agent.config import get_agent_config, get_agent_type_config

_TIMEOUT_CONFIG = {
    "llm_first_token_timeout": 30,
    "llm_stream_timeout": 60,
    "agent_timeout": 1800,
    "sub_agent_timeout": 600,
    "tool_timeout": 60,
}


# ---------- 工厂 ----------

def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    for name in [
        "emit_info", "emit_warning", "emit_error", "emit_thinking",
        "emit_tool_call", "emit_tool_result", "emit_finding", "emit_progress",
        "emit_phase_start", "emit_phase_complete", "emit_task_complete",
    ]:
        setattr(e, name, AsyncMock())
    return e


def _capturing_stream(calls: list[dict[str, Any]]):
    """记录每次 chat_completion_stream 调用的采样参数，产出单个 done chunk。"""

    async def _gen(
        messages=None,
        temperature=None,
        max_tokens=None,
        tools=None,
        response_format=None,
    ):
        calls.append({
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": response_format,
        })
        yield {
            "type": "done", "content": "ok", "reasoning": "",
            "accumulated": "ok",
            "usage": {"total_tokens": 5, "prompt_tokens": 10, "completion_tokens": 5},
            "finish_reason": "stop",
        }

    return _gen


def _make_service(calls: list[dict[str, Any]]):
    # 全局配置故意给一个与映射都不同的值：映射生效时它不应被使用
    service = MagicMock()
    service.backend_capabilities = None
    service.get_agent_timeout_config = MagicMock(return_value=_TIMEOUT_CONFIG)
    service.config = SimpleNamespace(max_tokens=99999)
    service.chat_completion_stream = _capturing_stream(calls)
    return service


def _agent(cls, calls):
    return cls(llm_service=_make_service(calls), tools={}, event_emitter=_make_emitter())


# ---------- 映射表 ----------

@pytest.mark.parametrize(
    "agent_type,expected",
    [
        ("orchestrator", 2048),
        ("recon", 2048),
        ("analysis", 8192),
        ("verification", 8192),
    ],
)
def test_agent_type_max_tokens_mapping(agent_type: str, expected: int):
    assert get_agent_type_config(agent_type).max_tokens == expected


def test_unknown_agent_type_has_no_mapping():
    """无映射类型 max_tokens=None → 调用链回退用户全局 llmMaxTokens。"""
    assert get_agent_type_config("mystery-agent").max_tokens is None


# ---------- stream_llm_call 端到端 ----------

@pytest.mark.asyncio
async def test_orchestrator_stream_uses_2048():
    calls: list[dict[str, Any]] = []
    agent = _agent(OrchestratorAgent, calls)
    await agent.stream_llm_call([{"role": "user", "content": "dispatch decision"}])
    assert calls[-1]["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_recon_stream_uses_2048():
    calls: list[dict[str, Any]] = []
    agent = _agent(ReconAgent, calls)
    await agent.stream_llm_call([{"role": "user", "content": "recon step"}])
    assert calls[-1]["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_analysis_intermediate_round_uses_8192():
    """analysis ReAct 中间轮（含 submit_findings 工具轮）= 8192。"""
    calls: list[dict[str, Any]] = []
    agent = _agent(AnalysisAgent, calls)
    await agent.stream_llm_call([{"role": "user", "content": "analyze"}])
    assert calls[-1]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_verification_round_uses_8192():
    calls: list[dict[str, Any]] = []
    agent = _agent(VerificationAgent, calls)
    await agent.stream_llm_call([{"role": "user", "content": "verify"}])
    assert calls[-1]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_explicit_max_tokens_not_overridden_by_mapping():
    """调用方显式传 max_tokens 时，per-agent 映射不得覆盖。"""
    calls: list[dict[str, Any]] = []
    agent = _agent(AnalysisAgent, calls)
    await agent.stream_llm_call(
        [{"role": "user", "content": "custom budget"}],
        max_tokens=12345,
    )
    assert calls[-1]["max_tokens"] == 12345


@pytest.mark.asyncio
async def test_no_mapping_falls_back_to_global_config(monkeypatch):
    """未知 agent 类型（映射 None）时 max_tokens 保持 None，由 service 层回退全局配置。"""
    calls: list[dict[str, Any]] = []
    agent = _agent(OrchestratorAgent, calls)
    # 模拟该 agent 类型无 per-agent 映射（patch 源模块属性，lazy import 同样生效）
    import app.services.agent.config as agent_config_mod
    monkeypatch.setattr(
        agent_config_mod, "get_agent_type_config",
        lambda _t: SimpleNamespace(max_tokens=None),
    )
    await agent.stream_llm_call([{"role": "user", "content": "unknown type"}])
    assert calls[-1]["max_tokens"] is None


# ---------- 强制总结轮大预算 ----------

@pytest.mark.asyncio
async def test_forced_summary_round_uses_large_budget():
    """analysis 强制总结轮（guided_json 全量 findings JSON）= 32768。"""
    calls: list[dict[str, Any]] = []
    agent = _agent(AnalysisAgent, calls)
    await agent._forced_summary_round()
    expected = get_agent_config().llm_max_tokens_forced_summary
    assert expected == 32768
    assert calls[-1]["max_tokens"] == expected
