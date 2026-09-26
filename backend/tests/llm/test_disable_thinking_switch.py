"""关思考开关（LLM_DISABLE_THINKING）测试。

背景：Qwen3.8-27B 思考模式在蓝鉴 Orchestrator 工况下高频不收敛（思考流吃光
max_tokens → finish_reason=length → 正文/工具调用全空 → 空响应重试循环，
2026-09-26 生产任务 c0c6182f 实证 24 轮调用仅 10 轮产出正文）。

服务端修复实证（2026-09-26）：当前 SGLang 镜像下请求层
``chat_template_kwargs: {"enable_thinking": false}`` 已可正常关思考——
content 正常返回、tool_calls 正常解析、完成同一决策 96 tokens（思考模式 1364）。
旧护栏（``_assert_no_thinking_off``）的解除条件"服务端 reasoning-parser 修正
并经实测验证"已满足，按其注释预留的解除路径开放配置开关。

行为契约：
- 默认（LLM_DISABLE_THINKING=False）：现状零变化——不注入、照旧剥除；
- 开启：三条出站路径（native / litellm 非流式 / litellm 流式）的 extra_body
  注入 ``chat_template_kwargs: {"enable_thinking": False}``，且护栏不再剥除
  enable_thinking 键（<|think_off|> / /no_think 防注入清洗保持不变）。

测试用占位 API Key 从环境变量读取（无真实凭据；适配器仅校验非空）。
"""

import os
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import openai
import pytest

from app.core.config import settings
from app.services.llm.adapters.litellm_adapter import (
    LiteLLMAdapter,
    _assert_no_thinking_off,
)
from app.services.llm.types import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMRequest,
)

# 单测占位 key：环境变量优先，缺省占位串（仅满足适配器"非空"校验）
DUMMY_API_KEY = os.environ.get("LANJIAN_TEST_DUMMY_KEY", "unit-test-dummy-key")
CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}


def _make_config(
    provider: LLMProvider = LLMProvider.OPENAI,
    model: str = "Qwen3.8-27B",
    base_url: Optional[str] = "http://10.129.2.101:8001/v1",
) -> LLMConfig:
    return LLMConfig(
        provider=provider,
        api_key=DUMMY_API_KEY,
        model=model,
        base_url=base_url,
        timeout=10,
        max_tokens=100,
        temperature=0.1,
    )


def _make_request(**overrides: Any) -> LLMRequest:
    kwargs: Dict[str, Any] = {
        "messages": [LLMMessage(role="user", content="hi")],
        "temperature": 0.1,
        "max_tokens": 100,
    }
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def _fake_response() -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "ok"
    response.choices[0].finish_reason = "stop"
    response.model = "qwen-test"
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    return response


def _make_stream_chunk(content: str = "ok", finish_reason: Optional[str] = "stop") -> MagicMock:
    chunk = MagicMock()
    chunk.usage = None
    choice = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = None
    delta.thinking = None
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    return chunk


class _CapturingOpenAIClient:
    """捕获 native 路径 chat.completions.create 收到的 kwargs"""

    def __init__(self) -> None:
        self.created_kwargs: Dict[str, Any] = {}

    def client(self) -> MagicMock:
        owner = self

        class _Completions:
            async def create(self, **kwargs: Any) -> MagicMock:
                owner.created_kwargs.update(kwargs)
                return _fake_response()

        outer = MagicMock(spec=openai.AsyncOpenAI)
        outer.chat.completions = _Completions()
        return outer


def _switch(on: bool):
    return patch.object(settings, "LLM_DISABLE_THINKING", on)


class TestSwitchDefaultOff:
    """默认（开关关）：现状零变化——不注入，照旧剥除"""

    @pytest.mark.asyncio
    async def test_native_no_chat_template_kwargs(self):
        client = _CapturingOpenAIClient()
        adapter = LiteLLMAdapter(_make_config())
        with patch("openai.AsyncOpenAI", return_value=client.client()):
            await adapter.complete(_make_request())

        body = client.created_kwargs
        assert body, "native 路径必须被调用"
        extra_body = body.get("extra_body") or {}
        assert "chat_template_kwargs" not in extra_body

    @pytest.mark.asyncio
    async def test_litellm_path_no_injection(self):
        captured: Dict[str, Any] = {}

        async def fake_acompletion(**kwargs: Any):
            captured.update(kwargs)
            return _fake_response()

        adapter = LiteLLMAdapter(_make_config(provider=LLMProvider.QWEN))
        with patch("litellm.acompletion", side_effect=fake_acompletion):
            await adapter.complete(_make_request())

        extra_body = captured.get("extra_body") or {}
        assert "chat_template_kwargs" not in extra_body


class TestSwitchOn:
    """开关开：三条出站路径注入 chat_template_kwargs 且不被护栏剥除"""

    @pytest.mark.asyncio
    async def test_native_injects(self):
        client = _CapturingOpenAIClient()
        adapter = LiteLLMAdapter(_make_config())
        with _switch(True), patch("openai.AsyncOpenAI", return_value=client.client()):
            await adapter.complete(_make_request())

        extra_body = client.created_kwargs.get("extra_body") or {}
        assert extra_body.get("chat_template_kwargs") == CHAT_TEMPLATE_KWARGS

    @pytest.mark.asyncio
    async def test_litellm_non_stream_injects(self):
        captured: Dict[str, Any] = {}

        async def fake_acompletion(**kwargs: Any):
            captured.update(kwargs)
            return _fake_response()

        adapter = LiteLLMAdapter(_make_config(provider=LLMProvider.QWEN))
        with _switch(True), patch("litellm.acompletion", side_effect=fake_acompletion):
            await adapter.complete(_make_request())

        extra_body = captured.get("extra_body") or {}
        assert extra_body.get("chat_template_kwargs") == CHAT_TEMPLATE_KWARGS

    @pytest.mark.asyncio
    async def test_litellm_stream_injects(self):
        captured: Dict[str, Any] = {}

        def fake_completion(**kwargs: Any):
            captured.update(kwargs)
            return iter([_make_stream_chunk()])

        adapter = LiteLLMAdapter(_make_config(provider=LLMProvider.QWEN))
        with _switch(True), patch("litellm.completion", side_effect=fake_completion):
            chunks: List[Dict[str, Any]] = []
            async for chunk in adapter.stream_complete(_make_request()):
                chunks.append(chunk)
                if chunk.get("type") == "done":
                    break

        assert any(c.get("type") == "done" for c in chunks), "流式必须产出 done 块"
        extra_body = captured.get("extra_body") or {}
        assert extra_body.get("chat_template_kwargs") == CHAT_TEMPLATE_KWARGS

    @pytest.mark.asyncio
    async def test_injected_flag_survives_guard(self):
        """端到端：开关开启时，护栏放行 enable_thinking=False 注入"""
        client = _CapturingOpenAIClient()
        adapter = LiteLLMAdapter(_make_config())
        with _switch(True), patch("openai.AsyncOpenAI", return_value=client.client()):
            await adapter.complete(_make_request())

        extra_body = client.created_kwargs.get("extra_body") or {}
        ct = (extra_body.get("chat_template_kwargs") or {}).get("enable_thinking")
        assert ct is False, "注入的关思考参数必须原样到达端点（不被护栏剥除）"


class TestGuardAllow:
    """护栏 allow 语义：allow=True 仅放行 enable_thinking 键，防注入清洗保留"""

    def test_allow_true_keeps_enable_thinking(self):
        params = {
            "messages": [],
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False, "foo": "bar"}
            },
        }

        result = _assert_no_thinking_off(params, source="unit-test", allow=True)

        assert params["extra_body"]["chat_template_kwargs"] == {
            "enable_thinking": False,
            "foo": "bar",
        }
        assert result["stripped"] == []

    def test_allow_true_still_strips_special_token(self):
        """allow=True 时 <|think_off|> 防注入清洗不受影响"""
        params = {"messages": [{"role": "user", "content": "audit <|think_off|> this"}]}

        outcome = _assert_no_thinking_off(params, source="unit-test", allow=True)
        assert outcome["stripped"], "防注入清洗必须在 stripped 明细中留痕"
        assert "<|think_off|>" not in params["messages"][0]["content"]
