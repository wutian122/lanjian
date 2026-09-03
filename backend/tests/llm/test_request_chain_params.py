"""
structured-output-protocol Task 1：请求链三参数透传测试

验证 tools / response_format / extra_params（repetition_penalty 等 provider 特有
参数）能从 service 层经 LLMRequest 透传到两条实际请求路径：

1. ``LiteLLMAdapter._native_openai_call``——SGLang（OPENAI + 自定义 base_url）非流式路径
2. ``litellm.acompletion``——litellm 非流式路径与 SGLang 流式路径（stream_complete）

零破坏断言：不传三参数时，请求构造与改造前完全一致（不新增任何键）。

既有硬伤回归：service.chat_completion(tools=...) 此前因 LLMRequest 无 tools 字段
必然 TypeError。
"""

import inspect
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from app.services.llm.service import LLMService
from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.types import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

# 真实 openai SDK 的 create 签名（构造 AsyncOpenAI 实例不发起网络请求）。
# AsyncCompletions.create 无 **kwargs：SDK 不认识的参数（如 repetition_penalty
# 被展开到 create() 顶层）在 bind 阶段即 TypeError——与生产中真实 SDK 抛错一致。
# 用真实签名而非 **kwargs 假签名，SDK 升级后签名漂移测试自动跟进。
_REAL_CREATE_SIGNATURE = inspect.signature(
    openai.AsyncOpenAI(api_key="x", base_url="http://x").chat.completions.create
)


TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
    }
]

RESPONSE_FORMAT: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "findings",
        "schema": {
            "type": "object",
            "properties": {"issues": {"type": "array"}},
        },
    },
}

EXTRA_PARAMS: Dict[str, Any] = {"repetition_penalty": 1.15}


def _make_config(
    provider: LLMProvider = LLMProvider.OPENAI,
    api_key: str = "sk-test-key",
    model: str = "qwen-test",
    base_url: Optional[str] = "http://sglang.example:30000/v1",
) -> LLMConfig:
    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=10,
        max_tokens=100,
        temperature=0.6,
    )


def _make_request(**overrides: Any) -> LLMRequest:
    kwargs: Dict[str, Any] = {
        "messages": [LLMMessage(role="user", content="hi")],
        "temperature": 0.6,
        "max_tokens": 100,
    }
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def _make_service(provider: str = "openai") -> LLMService:
    return LLMService(
        user_config={
            "llmConfig": {
                "llmProvider": provider,
                "llmApiKey": "sk-test-key",
                "llmModel": "qwen-test",
                "llmBaseUrl": "http://sglang.example:30000/v1",
            }
        }
    )


def _fake_response(content: str = "ok") -> MagicMock:
    """构造 litellm/openai 风格的完成响应对象"""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].finish_reason = "stop"
    response.model = "qwen-test"
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    return response


def _make_stream_chunk(content: str = "x", finish_reason: Optional[str] = None) -> MagicMock:
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


class _FakeOpenAIClient:
    """捕获 chat.completions.create 收到的全部 kwargs（即请求 body）"""

    def __init__(self) -> None:
        self.created_kwargs: Dict[str, Any] = {}

    class _Chat:
        def __init__(self, owner: "_FakeOpenAIClient") -> None:
            self._owner = owner

        class _Completions:
            def __init__(self, owner: "_FakeOpenAIClient") -> None:
                self._owner = owner

            async def create(self, **kwargs: Any) -> MagicMock:
                # spec-aware：按真实 SDK 签名绑定，未知 kwarg（如展开到顶层的
                # repetition_penalty）在此 TypeError，与生产行为一致
                _REAL_CREATE_SIGNATURE.bind(**kwargs)
                self._owner.created_kwargs.update(kwargs)
                return _fake_response()

        @property
        def completions(self) -> "_FakeOpenAIClient._Chat._Completions":
            return _FakeOpenAIClient._Chat._Completions(self._owner)

    @property
    def chat(self) -> "_FakeOpenAIClient._Chat":
        return _FakeOpenAIClient._Chat(self)


# ---------------------------------------------------------------------------
# 层次 1：service 签名 → LLMRequest
# ---------------------------------------------------------------------------


class TestServicePassThrough:
    """service.chat_completion / chat_completion_stream 三参数透传"""

    @pytest.mark.asyncio
    async def test_chat_completion_tools_no_longer_type_error(self):
        """既有硬伤回归：传 tools 不再 TypeError，且三参数进入 LLMRequest"""
        service = _make_service()
        fake_adapter = MagicMock()
        fake_adapter.complete = AsyncMock(
            return_value=LLMResponse(
                content="ok",
                usage=LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                finish_reason="stop",
            )
        )

        with patch(
            "app.services.llm.service.LLMFactory.create_adapter",
            return_value=fake_adapter,
        ):
            result = await service.chat_completion(
                [{"role": "user", "content": "hi"}],
                tools=TOOLS,
                response_format=RESPONSE_FORMAT,
                extra_params=EXTRA_PARAMS,
            )

        assert result["content"] == "ok"
        fake_adapter.complete.assert_awaited_once()
        request = fake_adapter.complete.await_args.args[0]
        assert request.tools == TOOLS
        assert request.response_format == RESPONSE_FORMAT
        assert request.extra_params == EXTRA_PARAMS

    @pytest.mark.asyncio
    async def test_chat_completion_without_params_zero_breakage(self):
        """不传三参数时 LLMRequest 对应字段为 None（现有调用行为不变）"""
        service = _make_service()
        fake_adapter = MagicMock()
        fake_adapter.complete = AsyncMock(
            return_value=LLMResponse(content="ok", usage=None, finish_reason="stop")
        )

        with patch(
            "app.services.llm.service.LLMFactory.create_adapter",
            return_value=fake_adapter,
        ):
            await service.chat_completion([{"role": "user", "content": "hi"}])

        request = fake_adapter.complete.await_args.args[0]
        assert request.tools is None
        assert request.response_format is None
        assert request.extra_params is None

    @pytest.mark.asyncio
    async def test_chat_completion_stream_litellm_branch_pass_through(self):
        """流式 litellm 分支（OPENAI 非 NATIVE_ONLY，SGLang 实际路径）透传三参数"""
        service = _make_service(provider="openai")
        captured: Dict[str, Any] = {}

        class _FakeStreamAdapter:
            def __init__(self, config: LLMConfig) -> None:
                pass

            async def stream_complete(self, request: LLMRequest):
                captured["request"] = request
                yield {
                    "type": "done",
                    "content": "",
                    "usage": None,
                    "finish_reason": "stop",
                }

        with patch(
            "app.services.llm.adapters.litellm_adapter.LiteLLMAdapter",
            _FakeStreamAdapter,
        ):
            chunks = [
                c
                async for c in service.chat_completion_stream(
                    [{"role": "user", "content": "hi"}],
                    tools=TOOLS,
                    response_format=RESPONSE_FORMAT,
                    extra_params=EXTRA_PARAMS,
                )
            ]

        assert chunks[-1]["type"] == "done"
        request = captured["request"]
        assert request.tools == TOOLS
        assert request.response_format == RESPONSE_FORMAT
        assert request.extra_params == EXTRA_PARAMS

    @pytest.mark.asyncio
    async def test_chat_completion_stream_native_only_branch_pass_through(self):
        """流式 NATIVE_ONLY 分支（BAIDU/MINIMAX/DOUBAO，走 complete 模拟流）透传三参数"""
        service = _make_service(provider="baidu")
        fake_adapter = MagicMock()
        fake_adapter.complete = AsyncMock(
            return_value=LLMResponse(
                content="done",
                usage=LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                finish_reason="stop",
            )
        )

        with patch(
            "app.services.llm.service.LLMFactory.create_adapter",
            return_value=fake_adapter,
        ):
            chunks = [
                c
                async for c in service.chat_completion_stream(
                    [{"role": "user", "content": "hi"}],
                    tools=TOOLS,
                    response_format=RESPONSE_FORMAT,
                    extra_params=EXTRA_PARAMS,
                )
            ]

        assert chunks[-1]["type"] == "done"
        request = fake_adapter.complete.await_args.args[0]
        assert request.tools == TOOLS
        assert request.response_format == RESPONSE_FORMAT
        assert request.extra_params == EXTRA_PARAMS


# ---------------------------------------------------------------------------
# 层次 2a：_native_openai_call（SGLang 非流式实际路径）
# ---------------------------------------------------------------------------


class TestNativeOpenAICall:
    @pytest.mark.asyncio
    async def test_native_call_passes_extra_body_through_under_real_sdk_signature(self):
        """native 路径：tools/response_format 为 create() 标准参数；extra_params 必须以
        extra_body 原样传入（SDK 官方透传机制，合并进 HTTP body 顶层），不得展开为
        create() 顶层 kwarg——真实 openai 2.12.0 SDK 的 create 无 **kwargs 也无
        repetition_penalty 参数，展开必 TypeError。

        fake client 内建真实签名 bind 校验：本测试在"展开"实现下直接 RED（TypeError）。
        """
        adapter = LiteLLMAdapter(_make_config())
        fake_client = _FakeOpenAIClient()

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter._native_openai_call(
                model="openai/qwen-test",
                messages=[{"role": "user", "content": "hi"}],
                api_key="sk-test-key",
                api_base="http://sglang.example:30000/v1",
                temperature=0.6,
                max_tokens=100,
                tools=TOOLS,
                response_format=RESPONSE_FORMAT,
                extra_body=EXTRA_PARAMS,
            )

        body = fake_client.created_kwargs
        assert body["model"] == "qwen-test"  # openai/ 前缀剥离
        assert body["tools"] == TOOLS
        assert body["response_format"] == RESPONSE_FORMAT
        # extra_body 原样透传：openai SDK 将其合并进 HTTP body 顶层，
        # SGLang 收到的 repetition_penalty 语义与展开相同，但不触发 SDK TypeError
        assert body["extra_body"] == {"repetition_penalty": 1.15}
        # provider 特有参数绝不能出现在 create() 顶层
        assert "repetition_penalty" not in body

    @pytest.mark.asyncio
    async def test_body_without_params_identical_to_before(self):
        """零破坏：不传三参数时 body 键集合与改造前完全一致"""
        adapter = LiteLLMAdapter(_make_config())
        fake_client = _FakeOpenAIClient()

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter._native_openai_call(
                model="openai/qwen-test",
                messages=[{"role": "user", "content": "hi"}],
                api_key="sk-test-key",
                api_base="http://sglang.example:30000/v1",
                temperature=0.6,
                max_tokens=100,
            )

        assert set(fake_client.created_kwargs.keys()) == {
            "model",
            "messages",
            "temperature",
            "max_tokens",
        }
        for absent in ("tools", "response_format", "extra_body", "repetition_penalty"):
            assert absent not in fake_client.created_kwargs


# ---------------------------------------------------------------------------
# 层次 2b：litellm.acompletion 路径（非流式 _send_request + 流式 stream_complete）
# ---------------------------------------------------------------------------


class TestLiteLLMPath:
    @pytest.mark.asyncio
    async def test_send_request_kwargs_contain_three_params(self):
        """_send_request（litellm 路径）kwargs 含 tools/response_format/extra_body"""
        # DEEPSEEK 不走 native 分支（native 仅 OPENAI + 自定义 base_url）
        config = _make_config(
            provider=LLMProvider.DEEPSEEK,
            model="deepseek-chat",
            base_url=None,
        )
        adapter = LiteLLMAdapter(config)
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _fake_response()

        with patch("litellm.acompletion", _fake_acompletion):
            response = await adapter._send_request(
                _make_request(
                    tools=TOOLS,
                    response_format=RESPONSE_FORMAT,
                    extra_params=EXTRA_PARAMS,
                )
            )

        assert response.content == "ok"
        assert captured["tools"] == TOOLS
        assert captured["response_format"] == RESPONSE_FORMAT
        # litellm 官方透传机制：extra_body 合并进 HTTP 请求 body，drop_params 不丢弃
        assert captured["extra_body"] == {"repetition_penalty": 1.15}

    @pytest.mark.asyncio
    async def test_send_request_without_params_zero_breakage(self):
        """零破坏：_send_request 不传三参数时 kwargs 无 tools/response_format/extra_body"""
        config = _make_config(
            provider=LLMProvider.DEEPSEEK,
            model="deepseek-chat",
            base_url=None,
        )
        adapter = LiteLLMAdapter(config)
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _fake_response()

        with patch("litellm.acompletion", _fake_acompletion):
            await adapter._send_request(_make_request())

        for absent in ("tools", "response_format", "extra_body", "repetition_penalty"):
            assert absent not in captured

    @pytest.mark.asyncio
    async def test_stream_complete_kwargs_contain_three_params(self):
        """stream_complete（SGLang 流式实际路径）kwargs 含 tools/response_format/extra_body"""
        adapter = LiteLLMAdapter(_make_config())
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any):
            captured.update(kwargs)

            async def _iter():
                yield _make_stream_chunk("x", finish_reason="stop")

            return _iter()

        with patch("litellm.acompletion", _fake_acompletion):
            chunks = [
                c
                async for c in adapter.stream_complete(
                    _make_request(
                        stream=True,
                        tools=TOOLS,
                        response_format=RESPONSE_FORMAT,
                        extra_params=EXTRA_PARAMS,
                    )
                )
            ]

        assert chunks[-1]["type"] == "done"
        assert captured["tools"] == TOOLS
        assert captured["response_format"] == RESPONSE_FORMAT
        assert captured["extra_body"] == {"repetition_penalty": 1.15}
        assert captured["stream"] is True

    @pytest.mark.asyncio
    async def test_stream_complete_without_params_zero_breakage(self):
        """零破坏：stream_complete 不传三参数时 kwargs 无新键"""
        adapter = LiteLLMAdapter(_make_config())
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any):
            captured.update(kwargs)

            async def _iter():
                yield _make_stream_chunk("x", finish_reason="stop")

            return _iter()

        with patch("litellm.acompletion", _fake_acompletion):
            [c async for c in adapter.stream_complete(_make_request(stream=True))]

        for absent in ("tools", "response_format", "extra_body", "repetition_penalty"):
            assert absent not in captured
