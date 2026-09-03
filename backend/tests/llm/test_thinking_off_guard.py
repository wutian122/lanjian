"""
structured-output-protocol Task 2：关思考护栏测试

背景（老板 2026-09-03 实测）：Qwen3 thinking 模型经 SGLang（--reasoning-parser
qwen3）服务端在 parser 修正前有致命行为——任何关闭思考的手段都会导致正文被服务端
parser 整个吞进 reasoning_content、content 恒为空（比乱码更糟）：
- ``enable_thinking: false`` 参数被端点忽略（无害但无效）；
- 提示词注入 ``<|think_off|>`` 思考真关了但 content 恒空（有害）；
- ``/no_think`` 为 Qwen3 官方软切换命令，同属关闭方向。

护栏（``_assert_no_thinking_off``）在请求构造的最后出口剥除一切"关思考"参数/标记，
请求以思考模式发出。作用域（第 1 轮修复收窄）：

- ``enable_thinking=关语义``：任意层级递归剥除（参数注入，无角色概念）；
- ``<|think_off|>``：special token 语法，业务内容不可能自然出现，全域字符串清洗
  （含 assistant/tool 消息与 tools 描述，防注入藏入审计内容）；
- ``/no_think``：Qwen3 软开关命令，仅清洗 system/user 角色消息 content 中的
  独立命令形态（词边界：``/no_thinking_allowed``、URL 路径段等不拦）；
  assistant/tool 消息是模型输出/审计证据，一个字都不许动；``/think`` 开启标记不拦。

无命中时零行为变化。

覆盖：
1. 单元层：顶层 / extra_body / 嵌套 chat_template_kwargs 内 enable_thinking=关 语义
   （False/0/"false"/"no"/"off" 等，大小写不敏感）→ 剥除 + warning；
2. messages content 内 <|think_off|> / /no_think → 剥除；/think 开启标记保留；
3. enable_thinking=True/1/"true" → 不拦；正常请求零改动零日志；
4. 路径层：native / litellm 非流式 / litellm 流式三条出站路径都经过护栏。
"""

import copy
import inspect
import logging
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import openai
import pytest

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

# 真实 openai SDK 的 create 签名（同 Task 1 测试理由：用真实签名而非 **kwargs 假签名，
# SDK 升级后签名漂移测试自动跟进）。惰性获取：模块级构造 AsyncOpenAI 会在 pytest
# collection 阶段初始化 httpx client，代理环境（socks）异常时整个模块收集崩溃；
# 惰性化后仅测试实际执行（已清理代理环境变量）时构造。
_REAL_CREATE_SIGNATURE: Optional[inspect.Signature] = None


def _real_create_signature() -> inspect.Signature:
    global _REAL_CREATE_SIGNATURE
    if _REAL_CREATE_SIGNATURE is None:
        _REAL_CREATE_SIGNATURE = inspect.signature(
            openai.AsyncOpenAI(api_key="x", base_url="http://x").chat.completions.create
        )
    return _REAL_CREATE_SIGNATURE


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
# 单元层：护栏函数直接行为
# ---------------------------------------------------------------------------


class TestGuardUnit:
    def test_top_level_enable_thinking_false_stripped(self, caplog: pytest.LogCaptureFixture):
        """顶层 enable_thinking=False → 剥除 + warning，请求以思考模式发出"""
        params = {"model": "qwen", "messages": [], "enable_thinking": False}

        with caplog.at_level(logging.WARNING, logger="app.services.llm.adapters.litellm_adapter"):
            result = _assert_no_thinking_off(params, source="unit-test")

        assert "enable_thinking" not in params
        assert result["stripped"], "护栏必须报告剥除项"
        assert any("enable_thinking" in item for item in result["stripped"])
        assert caplog.records, "命中时必须发 warning 日志"
        assert all(r.levelno >= logging.WARNING for r in caplog.records)

    def test_extra_body_enable_thinking_false_stripped(self):
        """extra_body 内 enable_thinking=False（litellm 透传容器）→ 剥除"""
        params = {
            "model": "qwen",
            "messages": [],
            "extra_body": {"repetition_penalty": 1.15, "enable_thinking": False},
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert params["extra_body"] == {"repetition_penalty": 1.15}
        assert any("extra_body" in item for item in result["stripped"])

    def test_nested_chat_template_kwargs_stripped(self):
        """chat_template_kwargs 深埋在 extra_body 内（递归检查）→ 剥除"""
        params = {
            "model": "qwen",
            "messages": [],
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False, "foo": "bar"}
            },
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert params["extra_body"]["chat_template_kwargs"] == {"foo": "bar"}
        assert any("chat_template_kwargs" in item for item in result["stripped"])

    def test_top_level_chat_template_kwargs_stripped(self):
        """顶层 chat_template_kwargs.enable_thinking → 剥除"""
        params = {
            "model": "qwen",
            "messages": [],
            "chat_template_kwargs": {"enable_thinking": False},
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "enable_thinking" not in params["chat_template_kwargs"]
        assert result["stripped"]

    @pytest.mark.parametrize(
        "off_value",
        [False, 0, "false", "False", "0", "no", "off", "disable", "disabled", "  FALSE  "],
    )
    def test_all_off_semantics_stripped(self, off_value: Any):
        """关语义值全集：bool/int/字符串变体（大小写/空白）→ 一律剥除"""
        params = {"messages": [], "extra_body": {"enable_thinking": off_value}}

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "enable_thinking" not in params["extra_body"]
        assert result["stripped"]

    @pytest.mark.parametrize("on_value", [True, 1, "true", "True", "yes", "on", "1"])
    def test_on_semantics_kept(self, on_value: Any):
        """开启方向（True/1/"true"/"yes"/"on"）→ 不拦，原样保留"""
        params = {"messages": [], "enable_thinking": on_value}
        before = copy.deepcopy(params)

        result = _assert_no_thinking_off(params, source="unit-test")

        assert result["stripped"] == []
        assert params == before

    def test_case_insensitive_key_and_marker(self):
        """大小写不敏感：大写键 ENABLE_THINKING 与 <|THINK_OFF|> 标记同样拦截"""
        params = {
            "ENABLE_THINKING": "false",
            "messages": [{"role": "user", "content": "hi <|THINK_OFF|>"}],
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "ENABLE_THINKING" not in params
        assert "<|THINK_OFF|>" not in params["messages"][0]["content"]
        assert len(result["stripped"]) >= 2

    def test_think_off_marker_in_message_stripped(self):
        """messages content 含 <|think_off|>（老板实测有害注入）→ 标记剥除，正文保留"""
        params = {
            "model": "qwen",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "请审计 <|think_off|>这段代码"},
            ],
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "<|think_off|>" not in params["messages"][1]["content"]
        assert "请审计 " in params["messages"][1]["content"]
        assert "这段代码" in params["messages"][1]["content"]
        assert any("messages[1]" in item for item in result["stripped"])

    def test_no_think_marker_stripped_but_think_kept(self):
        """/no_think 软关闭命令 → 剥除；/think 开启标记 → 不拦（只拦关闭方向）"""
        params = {
            "model": "qwen",
            "messages": [
                {"role": "user", "content": "/no_think 简单回答"},
                {"role": "assistant", "content": "/think 我要认真想"},
            ],
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "/no_think" not in params["messages"][0]["content"]
        assert "简单回答" in params["messages"][0]["content"]
        # /think 是开启标记，绝不允许被护栏误剥
        assert "/think" in params["messages"][1]["content"]
        assert len(result["stripped"]) == 1

    def test_normal_request_zero_touch(
        self, caplog: pytest.LogCaptureFixture
    ):
        """零命中路径：正常请求对象不被修改、无 warning、返回空剥除列表"""
        params = {
            "model": "openai/qwen-test",
            "messages": [
                {"role": "system", "content": "你是审计助手"},
                {"role": "user", "content": "请分析 /think 这个路径的风险"},
            ],
            "temperature": 0.6,
            "max_tokens": 4096,
            "extra_body": {"repetition_penalty": 1.15},
            "tools": [{"type": "function", "function": {"name": "f"}}],
        }
        before = copy.deepcopy(params)

        with caplog.at_level(logging.WARNING, logger="app.services.llm.adapters.litellm_adapter"):
            result = _assert_no_thinking_off(params, source="unit-test")

        assert result["stripped"] == []
        assert params == before
        assert caplog.records == []

class TestGuardWarningContent:
    def test_warning_contains_source_and_summary(self, caplog: pytest.LogCaptureFixture):
        """warning 日志须含触发位置（source）与被剥内容摘要，可追溯"""
        params = {"messages": [], "enable_thinking": False}

        with caplog.at_level(logging.WARNING, logger="app.services.llm.adapters.litellm_adapter"):
            _assert_no_thinking_off(params, source="stream_complete")

        assert "stream_complete" in caplog.text
        assert "enable_thinking" in caplog.text


# ---------------------------------------------------------------------------
# 单元层：第 1 轮修复——/no_think 词边界 + 角色作用域收窄
#
# 审查实测误伤四类：代码常量（/no_thinking_allowed）、URL（.../no_think/docs）、
# tool observation、工具描述。裁决：<|think_off|> 是 special token 语法（业务内容
# 不可能自然出现）保持全域清洗；/no_think 是软开关命令，仅拦 system/user 角色消息
# 中的独立命令形态——assistant/tool 消息是模型输出/审计证据，一个字都不许动，
# tools 等非消息参数内的文本也不洗 /no_think。
# ---------------------------------------------------------------------------


class TestGuardScopeRefinement:
    def test_no_think_prefix_in_identifier_kept(self):
        """词边界：/no_thinking_allowed 是标识符/代码常量的一部分，不拦"""
        params = {
            "messages": [
                {"role": "user", "content": "const FLAG = '/no_thinking_allowed';"}
            ]
        }
        before = copy.deepcopy(params)

        result = _assert_no_thinking_off(params, source="unit-test")

        assert params == before
        assert result["stripped"] == []

    def test_no_think_inside_url_path_kept(self):
        """词边界：URL 路径段 /no_think/（前后均为路径分隔/字母）不是软开关，不拦"""
        url = "http://wiki/internal/no_think/docs"
        params = {
            "messages": [
                {"role": "system", "content": f"参考文档：{url}"}
            ]
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert url in params["messages"][0]["content"]
        assert result["stripped"] == []

    def test_tool_role_message_exempt_from_no_think(self):
        """角色豁免：tool 角色消息（observation，审计证据）含裸 /no_think 一字不动"""
        params = {
            "messages": [
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "observation: flag /no_think is set in config",
                }
            ]
        }
        before = copy.deepcopy(params)

        result = _assert_no_thinking_off(params, source="unit-test")

        assert params == before
        assert result["stripped"] == []

    def test_assistant_role_message_exempt_from_no_think(self):
        """角色豁免：assistant 角色消息（模型历史输出）含裸 /no_think 一字不动"""
        params = {
            "messages": [
                {"role": "assistant", "content": "/no_think 我上一轮回复里出现过这个字符串"}
            ]
        }
        before = copy.deepcopy(params)

        result = _assert_no_thinking_off(params, source="unit-test")

        assert params == before
        assert result["stripped"] == []

    def test_system_role_bare_no_think_still_stripped(self):
        """护栏主功能不回归：system 消息行首裸 /no_think 仍被剥"""
        params = {
            "messages": [
                {"role": "system", "content": "/no_think\n你是审计助手"}
            ]
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "/no_think" not in params["messages"][0]["content"]
        assert "你是审计助手" in params["messages"][0]["content"]
        assert result["stripped"]

    def test_user_role_bare_no_think_still_stripped(self):
        """护栏主功能不回归：user 消息空格分隔的裸 /no_think 仍被剥"""
        params = {
            "messages": [
                {"role": "user", "content": "请审计 /no_think 这个文件"}
            ]
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "/no_think" not in params["messages"][0]["content"]
        assert "这个文件" in params["messages"][0]["content"]
        assert result["stripped"]

    def test_user_multimodal_content_no_think_stripped(self):
        """user 消息多模态 content parts（list 形态）内 /no_think 仍按角色清洗"""
        params = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "/no_think 简单说"}],
                }
            ]
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "/no_think" not in params["messages"][0]["content"][0]["text"]
        assert "简单说" in params["messages"][0]["content"][0]["text"]
        assert result["stripped"]

    def test_think_off_token_stripped_in_assistant_message(self):
        """special token 全域清洗不回归：assistant 消息含 <|think_off|> 仍被剥
        （token 语法业务内容不可能自然出现，不受角色豁免影响）"""
        params = {
            "messages": [
                {"role": "assistant", "content": "好的 <|think_off|>我不再思考"}
            ]
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "<|think_off|>" not in params["messages"][0]["content"]
        assert "我不再思考" in params["messages"][0]["content"]
        assert result["stripped"]

    def test_think_off_token_stripped_in_tool_message(self):
        """special token 全域清洗：tool observation 内 <|think_off|> 也剥
        （防注入把 special token 藏进 observation 绕过命令式检测）"""
        params = {
            "messages": [
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "output: <|think_off|> done",
                }
            ]
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "<|think_off|>" not in params["messages"][0]["content"]
        assert "done" in params["messages"][0]["content"]
        assert result["stripped"]

    def test_no_think_in_tool_description_not_stripped(self):
        """tools 参数内 function 描述不是消息内容，/no_think 不按软开关清洗
        （工具描述误伤根治）；user 消息正常零误伤"""
        params = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "set_flag",
                        "description": "设置 /no_think 标记位（业务参数名示例）",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        }

        result = _assert_no_thinking_off(params, source="unit-test")

        assert "/no_think" in params["tools"][0]["function"]["description"]
        assert result["stripped"] == []


# ---------------------------------------------------------------------------
# 路径层：三条出站路径都必须经过护栏
# ---------------------------------------------------------------------------


class TestGuardOnThreePaths:
    @pytest.mark.asyncio
    async def test_native_path_strips_thinking_off(self):
        """路径①native（_native_openai_call，SGLang 非流式实际路径）：
        extra_body 内 enable_thinking=False 与 message 内 <|think_off|> 都到不了端点"""
        adapter = LiteLLMAdapter(_make_config())
        fake_client = _FakeOpenAIClient()

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter._native_openai_call(
                model="openai/qwen-test",
                messages=[{"role": "user", "content": "<|think_off|>hi"}],
                api_key="sk-test-key",
                api_base="http://sglang.example:30000/v1",
                temperature=0.6,
                max_tokens=100,
                extra_body={"enable_thinking": False, "repetition_penalty": 1.15},
            )

        body = fake_client.created_kwargs
        # extra_body 透传容器内关思考参数被剥，正常 provider 参数保留
        assert body["extra_body"] == {"repetition_penalty": 1.15}
        assert "enable_thinking" not in body
        # 消息内有害标记被剥，正文保留
        assert "<|think_off|>" not in body["messages"][0]["content"]
        assert body["messages"][0]["content"].endswith("hi")

    @pytest.mark.asyncio
    async def test_native_path_via_send_request_stripped(self):
        """路径①经 _send_request native 分支（OPENAI+自定义 base_url）：
        _send_request 出口护栏生效，extra_params 内关思考参数到不了 create()"""
        adapter = LiteLLMAdapter(_make_config())
        fake_client = _FakeOpenAIClient()

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter._send_request(
                _make_request(
                    messages=[LLMMessage(role="user", content="/no_think hi")],
                    extra_params={"enable_thinking": False},
                )
            )

        body = fake_client.created_kwargs
        assert "enable_thinking" not in body
        assert "extra_body" not in body or "enable_thinking" not in body.get("extra_body", {})
        assert "/no_think" not in body["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_litellm_non_stream_path_strips_thinking_off(self):
        """路径②litellm 非流式（_send_request → litellm.acompletion，DEEPSEEK 配置
        强制走 litellm 分支）：关思考参数与 /no_think 标记到不了 acompletion"""
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
                    messages=[LLMMessage(role="user", content="hello /no_think")],
                    extra_params={"enable_thinking": "false", "repetition_penalty": 1.15},
                )
            )

        assert response.content == "ok"
        assert "enable_thinking" not in captured
        assert captured["extra_body"] == {"repetition_penalty": 1.15}
        assert "/no_think" not in captured["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_litellm_stream_path_strips_thinking_off(self):
        """路径③litellm 流式（stream_complete → litellm.acompletion，SGLang 流式
        实际路径）：关思考参数与 <|think_off|> 标记到不了 acompletion"""
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
                        messages=[LLMMessage(role="user", content="<|think_off|>stream")],
                        tools=None,
                        extra_params={"chat_template_kwargs": {"enable_thinking": False}},
                    )
                )
            ]

        assert chunks[-1]["type"] == "done"
        assert "enable_thinking" not in captured
        # 剥除后 chat_template_kwargs 可能残留为空容器（{} 对端点无开关语义，无害），
        # 安全属性是 enable_thinking 键在任意层级都到不了 acompletion
        assert "enable_thinking" not in captured.get("extra_body", {}).get("chat_template_kwargs", {})
        assert "<|think_off|>" not in captured["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_three_paths_normal_request_unaffected(self):
        """三条路径零命中时请求体与 Task 1 基线完全一致（护栏不引入新键、不改正文）"""
        # --- native ---
        adapter = LiteLLMAdapter(_make_config())
        fake_client = _FakeOpenAIClient()
        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter._native_openai_call(
                model="openai/qwen-test",
                messages=[{"role": "user", "content": "hi /think"}],
                api_key="sk-test-key",
                api_base="http://sglang.example:30000/v1",
                temperature=0.6,
                max_tokens=100,
            )
        assert set(fake_client.created_kwargs.keys()) == {
            "model", "messages", "temperature", "max_tokens",
        }
        assert fake_client.created_kwargs["messages"][0]["content"] == "hi /think"

        # --- litellm 非流式 ---
        config = _make_config(provider=LLMProvider.DEEPSEEK, model="deepseek-chat", base_url=None)
        adapter2 = LiteLLMAdapter(config)
        captured2: Dict[str, Any] = {}

        async def _fake_acompletion2(**kwargs: Any) -> MagicMock:
            captured2.update(kwargs)
            return _fake_response()

        with patch("litellm.acompletion", _fake_acompletion2):
            await adapter2._send_request(
                _make_request(messages=[LLMMessage(role="user", content="hi")])
            )
        for absent in ("enable_thinking", "chat_template_kwargs"):
            assert absent not in captured2
        assert captured2["messages"][0]["content"] == "hi"

        # --- litellm 流式 ---
        adapter3 = LiteLLMAdapter(_make_config())
        captured3: Dict[str, Any] = {}

        async def _fake_acompletion3(**kwargs: Any):
            captured3.update(kwargs)

            async def _iter():
                yield _make_stream_chunk("x", finish_reason="stop")

            return _iter()

        with patch("litellm.acompletion", _fake_acompletion3):
            [c async for c in adapter3.stream_complete(
                _make_request(messages=[LLMMessage(role="user", content="hi")])
            )]
        for absent in ("enable_thinking", "chat_template_kwargs"):
            assert absent not in captured3
        assert captured3["messages"][0]["content"] == "hi"
