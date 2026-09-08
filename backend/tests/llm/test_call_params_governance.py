"""
structured-output-protocol Task 9：采样参数治理（repetition_penalty/temperature）测试

覆盖 spec delta llm-call-params 两个 Requirement：

1. 「采样参数 SHALL 可配置并透传至推理端点」
   - LLMConfig.repetition_penalty 读取链：用户配置 llmConfig.repetitionPenalty
     > settings.LLM_REPETITION_PENALTY（默认 1.15）；
   - 适配器三条出站路径（native 非流式 / litellm 非流式 / litellm 流式）在
     config.repetition_penalty 非 None 时自动注入 extra_body——用户未配置时
     默认 1.15 同样注入，Agent/Service 调用方无需显式传 extra_params；
   - 请求级 extra_params 与配置级注入合并共存，显式值不被覆盖；
   - config.repetition_penalty=None（直构配置、未走 service 回退链）时零行为变化。
2. 「调用参数变更 SHALL 全链路可追溯」
   - LLMService.sampling_params_summary() 产出参数摘要
     （temperature/repetition_penalty/max_tokens/provider/model）；
   - _execute_agent_task 探测事件 metadata 挂载 llm_params（源码契约）。
3. 后端配置 API：LLMConfigSchema 接受 repetitionPenalty，默认配置含该字段。
"""

import inspect
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from app.core.config import settings
from app.services.llm.service import LLMService
from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.types import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMRequest,
)

SRC = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints" / "agent_tasks.py"


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------


def _service_with_llm_config(
    *,
    repetition_penalty: Optional[float] = ...,  # type: ignore[assignment]
    temperature: float = 0.6,
    provider: str = "openai",
    model: str = "qwen-test",
) -> LLMService:
    """构造 LLMService。repetition_penalty 默认不传（模拟用户未配置 → 走 settings 回退）。"""
    llm_config: Dict[str, Any] = {
        "llmProvider": provider,
        "llmApiKey": "sk-test-key",
        "llmModel": model,
        "llmBaseUrl": "http://sglang.example:30000/v1",
        "llmTemperature": temperature,
    }
    if repetition_penalty is not ...:
        llm_config["repetitionPenalty"] = repetition_penalty
    return LLMService(user_config={"llmConfig": llm_config})


def _make_request(**overrides: Any) -> LLMRequest:
    kwargs: Dict[str, Any] = {
        "messages": [LLMMessage(role="user", content="hi")],
        "temperature": 0.6,
        "max_tokens": 100,
    }
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def _fake_response(content: str = "ok") -> MagicMock:
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


# 真实 openai SDK create 签名：extra_body 是官方透传形参（合并进 HTTP body 顶层），
# 展开到顶层的未知参数（repetition_penalty=...）在 bind 阶段即 TypeError。
_REAL_CREATE_SIGNATURE: Optional[inspect.Signature] = None


def _real_create_signature() -> inspect.Signature:
    global _REAL_CREATE_SIGNATURE
    if _REAL_CREATE_SIGNATURE is None:
        _REAL_CREATE_SIGNATURE = inspect.signature(
            openai.AsyncOpenAI(api_key="x", base_url="http://x").chat.completions.create
        )
    return _REAL_CREATE_SIGNATURE


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
                _real_create_signature().bind(**kwargs)
                self._owner.created_kwargs.update(kwargs)
                return _fake_response()

        @property
        def completions(self) -> "_FakeOpenAIClient._Chat._Completions":
            return _FakeOpenAIClient._Chat._Completions(self._owner)

    @property
    def chat(self) -> "_FakeOpenAIClient._Chat":
        return _FakeOpenAIClient._Chat(self)


# ---------------------------------------------------------------------------
# ① LLMConfig.repetition_penalty 读取链（用户配置 > settings 默认）
# ---------------------------------------------------------------------------


class TestRepetitionPenaltyConfigChain:
    def test_user_config_repetition_penalty_wins(self):
        """用户配置 llmConfig.repetitionPenalty=1.2 → config.repetition_penalty=1.2"""
        service = _service_with_llm_config(repetition_penalty=1.2)
        assert service.config.repetition_penalty == 1.2

    def test_default_repetition_penalty_from_settings_when_unset(self):
        """用户未配置 → 回退 settings.LLM_REPETITION_PENALTY（默认 1.15）"""
        service = _service_with_llm_config()
        assert service.config.repetition_penalty == float(settings.LLM_REPETITION_PENALTY)
        assert service.config.repetition_penalty == 1.2

    def test_user_temperature_flows_to_config(self):
        """用户配置 llmTemperature=0.6 → config.temperature=0.6（不被代码层覆盖）"""
        service = _service_with_llm_config(temperature=0.6)
        assert service.config.temperature == 0.6

    def test_llm_config_field_is_optional_none_default(self):
        """直构 LLMConfig（未走 service 回退链）repetition_penalty 默认为 None：
        适配器据此判断"不注入"，保证旧调用方零行为变化。"""
        config = LLMConfig(
            provider=LLMProvider.DEEPSEEK,
            api_key="sk-test-key",
            model="deepseek-chat",
        )
        assert config.repetition_penalty is None


# ---------------------------------------------------------------------------
# ② 三条出站路径自动注入 extra_body（用户未配置时默认 1.15 也注入）
# ---------------------------------------------------------------------------


class TestNativePathInjection:
    """native 非流式路径（OPENAI + 自定义 base_url，SGLang 生产实际路径）"""

    @pytest.mark.asyncio
    async def test_native_body_contains_default_repetition_penalty(self):
        """用户未配置 repetitionPenalty → 默认 1.15 经 extra_body 到达 native 请求体"""
        service = _service_with_llm_config()
        adapter = LiteLLMAdapter(service.config)
        fake_client = _FakeOpenAIClient()

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter._send_request(_make_request())

        body = fake_client.created_kwargs
        assert body["extra_body"] == {"repetition_penalty": 1.2}
        # provider 特有参数绝不能展开到 create() 顶层（真实 SDK 无此形参，会 TypeError）
        assert "repetition_penalty" not in body

    @pytest.mark.asyncio
    async def test_native_body_uses_user_configured_value(self):
        """用户配置 repetitionPenalty=1.2 → native 请求体 extra_body=1.2"""
        service = _service_with_llm_config(repetition_penalty=1.2)
        adapter = LiteLLMAdapter(service.config)
        fake_client = _FakeOpenAIClient()

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter._send_request(_make_request())

        assert fake_client.created_kwargs["extra_body"] == {"repetition_penalty": 1.2}

    @pytest.mark.asyncio
    async def test_native_direct_call_without_config_rp_no_extra_body(self):
        """直构 config（rp=None）直接调 _native_openai_call 且无 extra_body → 不出现 extra_body 键"""
        config = LLMConfig(
            provider=LLMProvider.OPENAI,
            api_key="sk-test-key",
            model="qwen-test",
            base_url="http://sglang.example:30000/v1",
            timeout=10,
        )
        adapter = LiteLLMAdapter(config)
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

        assert "extra_body" not in fake_client.created_kwargs


class TestLiteLLMNonStreamInjection:
    """litellm 非流式路径（_send_request → litellm.acompletion，非 OPENAIN native 分支）"""

    @pytest.mark.asyncio
    async def test_send_request_extra_body_contains_default_rp(self):
        service = _service_with_llm_config(provider="deepseek", model="deepseek-chat")
        adapter = LiteLLMAdapter(service.config)
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _fake_response()

        with patch("litellm.acompletion", _fake_acompletion):
            response = await adapter._send_request(_make_request())

        assert response.content == "ok"
        assert captured["extra_body"] == {"repetition_penalty": 1.2}

    @pytest.mark.asyncio
    async def test_send_request_merges_request_extra_params(self):
        """请求级 extra_params 与配置级 repetition_penalty 合并共存"""
        service = _service_with_llm_config(provider="deepseek", model="deepseek-chat")
        adapter = LiteLLMAdapter(service.config)
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _fake_response()

        with patch("litellm.acompletion", _fake_acompletion):
            await adapter._send_request(
                _make_request(extra_params={"top_k": 20})
            )

        assert captured["extra_body"] == {"top_k": 20, "repetition_penalty": 1.2}

    @pytest.mark.asyncio
    async def test_send_request_explicit_extra_params_rp_takes_precedence(self):
        """请求级显式 repetition_penalty 不被配置默认值覆盖（per-request 覆盖优先）"""
        service = _service_with_llm_config(provider="deepseek", model="deepseek-chat")
        adapter = LiteLLMAdapter(service.config)
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _fake_response()

        with patch("litellm.acompletion", _fake_acompletion):
            await adapter._send_request(
                _make_request(extra_params={"repetition_penalty": 1.05})
            )

        assert captured["extra_body"] == {"repetition_penalty": 1.05}

    @pytest.mark.asyncio
    async def test_send_request_config_rp_none_zero_breakage(self):
        """直构 config（rp=None）且请求无 extra_params → kwargs 不含 extra_body"""
        config = LLMConfig(
            provider=LLMProvider.DEEPSEEK,
            api_key="sk-test-key",
            model="deepseek-chat",
            timeout=10,
        )
        adapter = LiteLLMAdapter(config)
        captured: Dict[str, Any] = {}

        async def _fake_acompletion(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _fake_response()

        with patch("litellm.acompletion", _fake_acompletion):
            await adapter._send_request(_make_request())

        assert "extra_body" not in captured


class TestLiteLLMStreamInjection:
    """litellm 流式路径（stream_complete → litellm.completion 同步线程桥，SGLang 流式实际路径）"""

    @pytest.mark.asyncio
    async def test_stream_extra_body_contains_default_rp(self):
        service = _service_with_llm_config()
        adapter = LiteLLMAdapter(service.config)
        captured: Dict[str, Any] = {}

        def _fake_completion(**kwargs: Any):
            captured.update(kwargs)
            return iter([_make_stream_chunk("x", finish_reason="stop")])

        with patch("litellm.completion", _fake_completion):
            chunks = [c async for c in adapter.stream_complete(_make_request(stream=True))]

        assert chunks[-1]["type"] == "done"
        assert captured["extra_body"] == {"repetition_penalty": 1.2}

    @pytest.mark.asyncio
    async def test_stream_merges_request_extra_params(self):
        service = _service_with_llm_config()
        adapter = LiteLLMAdapter(service.config)
        captured: Dict[str, Any] = {}

        def _fake_completion(**kwargs: Any):
            captured.update(kwargs)
            return iter([_make_stream_chunk("x", finish_reason="stop")])

        with patch("litellm.completion", _fake_completion):
            chunks = [
                c
                async for c in adapter.stream_complete(
                    _make_request(stream=True, extra_params={"top_k": 20})
                )
            ]

        assert chunks[-1]["type"] == "done"
        assert captured["extra_body"] == {"top_k": 20, "repetition_penalty": 1.2}


# ---------------------------------------------------------------------------
# ③ 参数摘要事件（全链路可追溯）
# ---------------------------------------------------------------------------


class TestSamplingParamsSummary:
    def test_summary_contains_governed_params(self):
        """sampling_params_summary 含 temperature/repetition_penalty/max_tokens/后端类型"""
        service = _service_with_llm_config(temperature=0.6)
        summary = service.sampling_params_summary()

        assert summary["provider"] == "openai"
        assert summary["model"] == "qwen-test"
        assert summary["temperature"] == 0.6
        assert summary["repetition_penalty"] == 1.2
        assert summary["max_tokens"] == service.config.max_tokens

    def test_summary_reflects_user_configured_rp(self):
        service = _service_with_llm_config(repetition_penalty=1.2, temperature=0.7)
        summary = service.sampling_params_summary()
        assert summary["repetition_penalty"] == 1.2
        assert summary["temperature"] == 0.7

    @staticmethod
    def _extract_function_body(source: str, func_name: str) -> str:
        import ast

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                lines = source.splitlines()
                return "\n".join(lines[node.lineno - 1: node.end_lineno])
        raise ValueError(f"function {func_name!r} not found")

    def test_execute_agent_task_attaches_llm_params_to_probe_event(self):
        """源码契约：_execute_agent_task 能力探测事件 metadata 挂载 llm_params 摘要
        （info/warning 两个分支共用），且摘要来自 sampling_params_summary()。"""
        content = SRC.read_text(encoding="utf-8")
        body = self._extract_function_body(content, "_execute_agent_task")

        assert "sampling_params_summary" in body, (
            "任务启动应调用 llm_service.sampling_params_summary() 生成参数摘要"
        )
        assert "llm_params" in body, (
            "探测事件 metadata 应含 llm_params（temperature/repetition_penalty/max_tokens）"
        )
        assert "backend_capabilities" in body, (
            "llm_params 应与 backend_capabilities（含 guided_style/后端类型）同事件挂载"
        )


# ---------------------------------------------------------------------------
# ④ 后端配置 API 字段
# ---------------------------------------------------------------------------


class TestConfigApiField:
    def test_schema_accepts_repetition_penalty(self):
        from app.api.v1.endpoints.config import LLMConfigSchema

        saved = LLMConfigSchema(repetitionPenalty=1.15).dict(exclude_none=True)
        assert saved["repetitionPenalty"] == 1.15

    def test_schema_default_none_excluded_from_payload(self):
        """未配置时 exclude_none 不含该字段（合并更新不覆盖已存值）"""
        from app.api.v1.endpoints.config import LLMConfigSchema

        assert "repetitionPenalty" not in LLMConfigSchema().dict(exclude_none=True)

    def test_default_config_contains_repetition_penalty(self):
        from app.api.v1.endpoints.config import get_default_config

        assert (
            get_default_config()["llmConfig"]["repetitionPenalty"]
            == float(settings.LLM_REPETITION_PENALTY)
        )

    @pytest.mark.asyncio
    async def test_user_config_request_round_trip(self):
        """UserConfigRequest 接受 llmConfig.repetitionPenalty（pydantic 校验不报错）"""
        from app.api.v1.endpoints.config import UserConfigRequest

        req = UserConfigRequest(
            llmConfig={"llmProvider": "openai", "repetitionPenalty": 1.15}
        )
        assert req.llmConfig is not None
        assert req.llmConfig.repetitionPenalty == 1.15
