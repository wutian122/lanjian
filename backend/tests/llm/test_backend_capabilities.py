"""
structured-output-protocol Task 6：多后端能力探测层测试

覆盖 spec「多推理后端能力探测与协议自动选择」三个 Scenario：
1. SGLang 双可用（tools + guided_json）→ 探测 True/True 且进程内缓存命中
2. 后端不支持 tools（探测请求报错/无 tool_calls）→ 能力 False，降级文本协议
3. 探测超时/异常 → 全 False、不抛出、任务可继续

另覆盖：缓存键按 (base_url, model) 区分、ollama format 字段形态、
capabilities_summary 可追溯形态、LLMService 挂载点、is_supported 向后兼容、
探测请求经关思考护栏（无 enable_thinking）。
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from app.services.agent import structured_output as so
from app.services.llm.service import LLMService
from app.services.llm.types import LLMConfig, LLMProvider

_BASE_URL = "http://sglang.example:30000/v1"
_MODEL = "qwen3-coder-test"


# ---------------------------------------------------------------------------
# Fake openai client：按 create() kwargs 区分 tools 探测 / guided 探测
# ---------------------------------------------------------------------------


def _tool_calls_response() -> MagicMock:
    resp = MagicMock()
    fn = MagicMock()
    fn.name = "capability_probe"
    fn.arguments = '{"answer": 1}'
    tc = MagicMock()
    tc.function = fn
    msg = MagicMock()
    msg.tool_calls = [tc]
    msg.content = None
    resp.choices = [MagicMock(message=msg)]
    return resp


def _guided_response(content: str = '{"ok": true}') -> MagicMock:
    resp = MagicMock()
    msg = MagicMock()
    msg.tool_calls = None
    msg.content = content
    resp.choices = [MagicMock(message=msg)]
    return resp


def _bad_request(message: str = "unsupported parameter") -> Exception:
    return openai.BadRequestError(
        message=message,
        response=MagicMock(status_code=400),
        body={"error": {"message": message}},
    )


def _timeout_error() -> Exception:
    return openai.APITimeoutError(request=MagicMock())


class _FakeProbeClient:
    """记录全部 create() 调用；按行为字典决定每次调用的响应/异常。

    behaviors 键：
      "tools"——携带 tools 参数的探测请求
      "guided_rf"——携带 response_format 的 OpenAI 风格 guided 探测
      "guided_format"——携带 extra_body={"format": ...} 的 ollama 风格探测
    值为响应 MagicMock 或 Exception 实例（抛出）。
    """

    def __init__(self, behaviors: Dict[str, Any]) -> None:
        self.behaviors = behaviors
        self.create_calls: List[Dict[str, Any]] = []

    class _Chat:
        def __init__(self, owner: "_FakeProbeClient") -> None:
            self._owner = owner

        class _Completions:
            def __init__(self, owner: "_FakeProbeClient") -> None:
                self._owner = owner

            async def create(self, **kwargs: Any) -> MagicMock:
                self._owner.create_calls.append(kwargs)
                if "tools" in kwargs:
                    key = "tools"
                elif "response_format" in kwargs:
                    key = "guided_rf"
                elif "extra_body" and "format" in kwargs.get("extra_body", {}):
                    key = "guided_format"
                else:
                    key = "other"
                behavior = self._owner.behaviors.get(key)
                if isinstance(behavior, Exception):
                    raise behavior
                return behavior

        @property
        def completions(self) -> "_FakeProbeClient._Chat._Completions":
            return _FakeProbeClient._Chat._Completions(self._owner)

    @property
    def chat(self) -> "_FakeProbeClient._Chat":
        return _FakeProbeClient._Chat(self)


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个用例前后清空进程内能力缓存，保证用例独立"""
    so._reset_capabilities_cache()
    yield
    so._reset_capabilities_cache()


def _make_config(
    base_url: Optional[str] = _BASE_URL,
    model: str = _MODEL,
    api_key: str = "sk-test-key",
) -> LLMConfig:
    return LLMConfig(
        provider=LLMProvider.OPENAI,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=10,
        max_tokens=100,
        temperature=0.6,
    )


class _ServiceStub:
    """最小 service 形态：get_backend_capabilities 只读 service.config"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config


def _make_real_service(base_url: str = _BASE_URL, model: str = _MODEL) -> LLMService:
    return LLMService(
        user_config={
            "llmConfig": {
                "llmProvider": "openai",
                "llmApiKey": "sk-test-key",
                "llmModel": model,
                "llmBaseUrl": base_url,
            }
        }
    )


# ---------------------------------------------------------------------------
# Scenario 1：SGLang 双可用
# ---------------------------------------------------------------------------


class TestProbeDualCapable:
    @pytest.mark.asyncio
    async def test_sglang_tools_and_guided_both_detected(self):
        """tools 探测返回 tool_calls、guided 探测返回严格 JSON → 双 True，
        guided_style=response_format_json_schema"""
        fake = _FakeProbeClient(
            {
                "tools": _tool_calls_response(),
                "guided_rf": _guided_response(),
            }
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        assert caps.tools is True
        assert caps.guided_json is True
        assert caps.guided_style == "response_format_json_schema"
        assert caps.probe_error is None
        assert caps.probed_at is not None
        assert isinstance(caps.elapsed_ms, int)

    @pytest.mark.asyncio
    async def test_probe_emits_two_light_requests_without_thinking_switch(self):
        """探测请求为小 max_tokens 轻量请求，且不带任何关思考参数（护栏联动）"""
        fake = _FakeProbeClient(
            {"tools": _tool_calls_response(), "guided_rf": _guided_response()}
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        assert len(fake.create_calls) == 2
        for call in fake.create_calls:
            # 轻量请求：预算上限 512（需容纳思考模型 reasoning 前缀，见实现注释）
            assert call["max_tokens"] <= 512
            assert "enable_thinking" not in call
            assert "extra_body" not in call or "enable_thinking" not in call.get(
                "extra_body", {}
            )
            for msg in call["messages"]:
                assert "<|think_off|>" not in str(msg.get("content", ""))

    @pytest.mark.asyncio
    async def test_guided_probe_budget_covers_thinking_prefix(self):
        """思考模型先输出 reasoning 再吐正文：预算不足时 content=null、
        finish_reason=length（真实 SGLang Qwen3 实测 36 reasoning token）。
        探测预算必须容纳 reasoning 前缀，否则把"预算截断"误判成"不支持 guided"。"""

        class _BudgetAwareClient:
            def __init__(self) -> None:
                self.create_calls: List[Dict[str, Any]] = []

            class _Chat:
                def __init__(self, owner: "_BudgetAwareClient") -> None:
                    self._owner = owner

                class _Completions:
                    def __init__(self, owner: "_BudgetAwareClient") -> None:
                        self._owner = owner

                    async def create(self, **kwargs: Any) -> MagicMock:
                        self._owner.create_calls.append(kwargs)
                        if "tools" in kwargs:
                            return _tool_calls_response()
                        if kwargs.get("max_tokens", 0) < 100:
                            # 模拟思考模型：小预算下 token 全被 reasoning 耗尽
                            truncated = MagicMock()
                            msg = MagicMock()
                            msg.tool_calls = None
                            msg.content = None
                            truncated.choices = [MagicMock(message=msg)]
                            truncated.choices[0].finish_reason = "length"
                            return truncated
                        return _guided_response()

                @property
                def completions(self) -> "_BudgetAwareClient._Chat._Completions":
                    return _BudgetAwareClient._Chat._Completions(self._owner)

            @property
            def chat(self) -> "_BudgetAwareClient._Chat":
                return _BudgetAwareClient._Chat(self)

        fake = _BudgetAwareClient()
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        assert caps.guided_json is True
        assert caps.guided_style == "response_format_json_schema"

    @pytest.mark.asyncio
    async def test_cache_hit_second_call_does_not_reprobe(self):
        """同 (base_url, model) 第二次走缓存，不再发探测请求"""
        fake = _FakeProbeClient(
            {"tools": _tool_calls_response(), "guided_rf": _guided_response()}
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps1 = await so.get_backend_capabilities(_ServiceStub(_make_config()))
            caps2 = await so.get_backend_capabilities(_ServiceStub(_make_config()))

        assert caps1 is caps2
        # 两个探测请求各一次；第二次全部命中缓存
        assert len(fake.create_calls) == 2

    @pytest.mark.asyncio
    async def test_cache_key_distinguishes_model_and_base_url(self):
        """缓存键区分 base_url 与 model：任一不同都重新探测"""
        fake = _FakeProbeClient(
            {"tools": _tool_calls_response(), "guided_rf": _guided_response()}
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            await so.get_backend_capabilities(_ServiceStub(_make_config()))
            assert len(fake.create_calls) == 2

            # 同 base_url + 不同 model → 重探
            await so.get_backend_capabilities(
                _ServiceStub(_make_config(model="qwen3-other-model"))
            )
            assert len(fake.create_calls) == 4

            # 不同 base_url + 同 model → 重探
            await so.get_backend_capabilities(
                _ServiceStub(
                    _make_config(base_url="http://vllm.example:8000/v1")
                )
            )
            assert len(fake.create_calls) == 6

            # 完全相同 → 命中（尾斜杠归一化不影响）
            await so.get_backend_capabilities(
                _ServiceStub(_make_config(base_url=_BASE_URL + "/"))
            )
            assert len(fake.create_calls) == 6


# ---------------------------------------------------------------------------
# Scenario 2：后端不支持 tools → 能力 False + 降级
# ---------------------------------------------------------------------------


class TestProbeUnsupportedBackend:
    @pytest.mark.asyncio
    async def test_tools_unsupported_guided_ok_partial_capability(self):
        """tools 请求 400、guided 正常 → tools=False、guided_json=True（部分能力）"""
        fake = _FakeProbeClient(
            {
                "tools": _bad_request("tools are not supported"),
                "guided_rf": _guided_response(),
            }
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        assert caps.tools is False
        assert caps.guided_json is True
        assert caps.guided_style == "response_format_json_schema"
        assert caps.probe_error is None

    @pytest.mark.asyncio
    async def test_all_unsupported_degrades_to_text_protocol(self):
        """两项都不支持 → 全 False；StructuredOutputAdapter.is_supported() 为 False
        （Agent 循环据此走 ReAct 文本协议降级路径）"""
        fake = _FakeProbeClient(
            {
                "tools": _bad_request("unsupported"),
                "guided_rf": _bad_request("response_format not supported"),
                "guided_format": _bad_request("format not supported"),
            }
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        assert caps.tools is False
        assert caps.guided_json is False
        assert caps.guided_style is None
        # 4xx 是后端明确应答"不支持"（后端可达）：不算探测失败，走 info 记录路径
        assert caps.probe_error is None
        adapter = so.StructuredOutputAdapter(capabilities=caps)
        assert adapter.is_supported() is False

    @pytest.mark.asyncio
    async def test_tools_returns_content_without_tool_calls_counts_as_unsupported(self):
        """后端接受 tools 参数但模型未发起调用（无 tool_calls）→ tools 能力 False"""
        fake = _FakeProbeClient(
            {
                "tools": _guided_response(content="I cannot call tools."),
                "guided_rf": _guided_response(),
            }
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        assert caps.tools is False
        assert caps.guided_json is True

    @pytest.mark.asyncio
    async def test_ollama_style_format_field_detected(self):
        """OpenAI 风格 response_format 报错、ollama 风格 format（extra_body）返回
        严格 JSON → guided_json=True、guided_style=format_json_schema"""
        fake = _FakeProbeClient(
            {
                "tools": _tool_calls_response(),
                "guided_rf": _bad_request("unknown parameter response_format"),
                "guided_format": _guided_response(),
            }
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(
                "http://ollama.example:11434/v1", "ollama", "qwen3-coder:latest"
            )

        assert caps.tools is True
        assert caps.guided_json is True
        assert caps.guided_style == "format_json_schema"
        # ollama 形态探测请求经 extra_body 透传 format
        format_calls = [
            c for c in fake.create_calls if c.get("extra_body", {}).get("format")
        ]
        assert len(format_calls) == 1


# ---------------------------------------------------------------------------
# Scenario 3：探测超时/异常 → 全 False 不抛出，任务可继续
# ---------------------------------------------------------------------------


class TestProbeFailure:
    @pytest.mark.asyncio
    async def test_timeout_returns_all_false_without_raising(self):
        """两个探测请求均超时 → 全 False、probe_error 记录、异常不向上抛"""
        fake = _FakeProbeClient(
            {"tools": _timeout_error(), "guided_rf": _timeout_error()}
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        assert caps.tools is False
        assert caps.guided_json is False
        assert caps.guided_style is None
        assert caps.probe_error is not None

    @pytest.mark.asyncio
    async def test_get_capabilities_never_raises_and_task_continues(self):
        """get_backend_capabilities 入口在客户端构造即失败时也返回全 False，
        调用方（任务启动流程）无需 try/except 即可继续"""
        with patch("openai.AsyncOpenAI", side_effect=RuntimeError("boom")):
            caps = await so.get_backend_capabilities(_ServiceStub(_make_config()))

        assert caps.tools is False
        assert caps.guided_json is False
        # 降级判定：适配器报告不支持结构化输出
        assert so.StructuredOutputAdapter(capabilities=caps).is_supported() is False

    @pytest.mark.asyncio
    async def test_missing_base_url_all_false_no_network(self):
        """未配置 base_url（litellm 默认端点）→ 不发起任何请求，全 False 降级"""
        config = _make_config(base_url=None)
        with patch("openai.AsyncOpenAI") as mock_client:
            caps = await so.get_backend_capabilities(_ServiceStub(config))

        assert caps.tools is False
        assert caps.guided_json is False
        assert caps.probe_error is not None
        mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# 可追溯 summary 与 LLMService 挂载
# ---------------------------------------------------------------------------


class TestCapabilitiesSummaryAndMount:
    @pytest.mark.asyncio
    async def test_capabilities_summary_shape(self):
        """capabilities_summary 含 Task 9 参数摘要事件所需字段"""
        fake = _FakeProbeClient(
            {"tools": _tool_calls_response(), "guided_rf": _guided_response()}
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(_BASE_URL, "sk-test-key", _MODEL)

        summary = caps.capabilities_summary()
        assert summary["tools"] is True
        assert summary["guided_json"] is True
        assert summary["guided_style"] == "response_format_json_schema"
        assert isinstance(summary["probed_at"], str) and "T" in summary["probed_at"]
        assert summary["probe_error"] is None
        # 事件可观测字段
        assert summary["elapsed_ms"] >= 0
        assert "sglang.example" in summary["base_url_host"]
        assert summary["backend_hint"] == "openai_compatible"

    @pytest.mark.asyncio
    async def test_ollama_backend_hint(self):
        fake = _FakeProbeClient(
            {
                "tools": _tool_calls_response(),
                "guided_rf": _bad_request("x"),
                "guided_format": _guided_response(),
            }
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await so.probe_backend_capabilities(
                "http://127.0.0.1:11434/v1", "ollama", "llama3:latest"
            )
        assert caps.capabilities_summary()["backend_hint"] == "ollama"

    @pytest.mark.asyncio
    async def test_llm_service_mounts_lazy_capabilities(self):
        """LLMService.get_backend_capabilities() 惰性探测并缓存到实例属性；
        backend_capabilities 属性在探测前为 None"""
        service = _make_real_service()
        assert service.backend_capabilities is None

        fake = _FakeProbeClient(
            {"tools": _tool_calls_response(), "guided_rf": _guided_response()}
        )
        with patch("openai.AsyncOpenAI", return_value=fake):
            caps = await service.get_backend_capabilities()
            # 第二次调用：进程缓存命中，不重复探测
            caps2 = await service.get_backend_capabilities()

        assert caps is caps2
        assert service.backend_capabilities is caps
        assert caps.tools is True
        assert len(fake.create_calls) == 2

    @pytest.mark.asyncio
    async def test_is_supported_backward_compatible_static_whitelist(self):
        """向后兼容：未挂载能力探测结果时保留原静态白名单行为"""
        assert so.StructuredOutputAdapter(so.LLMProvider.OPENAI).is_supported() is True
        assert so.StructuredOutputAdapter(so.LLMProvider.OTHER).is_supported() is False
        # 挂载能力后以能力为准（白名单 provider 但探测全 False → 不支持，降级）
        caps = so.BackendCapabilities(tools=False, guided_json=False)
        assert (
            so.StructuredOutputAdapter(so.LLMProvider.OPENAI, capabilities=caps)
            .is_supported()
            is False
        )
