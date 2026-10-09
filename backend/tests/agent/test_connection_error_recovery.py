"""连接错误恢复后的收口语义（2026-10-02 韧性缺口修复）。

生产实证（部署对照任务 bba4d002，4 小时工作丢失）：09:38 一次 API 连接
错误（重试 1/3 后网络恢复）把 [API_ERROR:connection] 的 user_message 赋入
`error_message` 局部变量，恢复后从未重置；数小时后 LLM 正常决策 finish，
run() 尾部 `if error_message:` 命中残留值 → AgentResult(success=False) →
整个任务 failed。

契约：连接错误重试恢复后，error_message 必须随 _api_retry_count 一并清零；
后续正常 finish 收口不受历史错误污染，任务成功完成。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.agent.test_empty_response_nudge import _make_emitter, _make_orchestrator_agent, _make_service
from app.services.agent.structured_output import BackendCapabilities


def _scripted(rounds):
    state = {"i": 0}

    def _stream(messages=None, temperature=None, max_tokens=None, tools=None,
                response_format=None, extra_params=None):
        spec = rounds[state["i"]]
        state["i"] += 1

        async def _gen():
            if spec["form"] == "connection_error":
                yield {"type": "error", "error_type": "connection",
                       "error": "connect timeout", "usage": None,
                       "user_message": "无法连接到 API 服务，请检查网络连接",
                       "accumulated": ""}
            else:
                text = spec["text"]
                yield {"type": "done", "content": text, "reasoning": "",
                       "accumulated": text, "usage": {"total_tokens": 10},
                       "finish_reason": "stop"}
        return _gen()

    return _stream


@pytest.mark.asyncio
async def test_connection_error_then_recovery_finishes_successfully(monkeypatch):
    """连接错误 1 次（自动恢复）→ 之后正常 finish → 任务成功而非 failed。"""
    monkeypatch.setattr(
        "app.services.agent.agents.orchestrator.asyncio.sleep", AsyncMock(),
    )
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_orchestrator_agent(caps=caps)
    agent.llm_service.chat_completion_stream = _scripted([
        {"form": "connection_error"},
        {"form": "connection_error"},  # 一搏也遇错误（网络中断窗口）
        {"form": "ok", "text": "Thought: 审计完成\nAction: finish\nAction Input: {}"},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, (
        f"连接错误恢复后正常 finish 必须成功收口，实际: {result.error!r}"
    )
