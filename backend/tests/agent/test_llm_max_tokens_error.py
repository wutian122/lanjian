"""
D 修复测试：LLM max_tokens 超限返回含限制值的友好提示

根因：用户配置 llmMaxTokens=128000 超过服务商限制（如讯飞 MaaS 32768），
      LLM 返回 400，adapter 只抛通用 'API 服务异常 (400)'，用户不知如何调整。
修复：解析 max_tokens 超限错误，提取限制值，返回友好提示。
"""
import pytest
from unittest.mock import patch

import openai
import litellm

from app.services.llm.adapters.litellm_adapter import (
    LiteLLMAdapter,
    _detect_max_tokens_error,
)
from app.services.llm.types import LLMConfig, LLMProvider, LLMRequest, LLMMessage, LLMError


def _make_adapter(base_url=None):
    config = LLMConfig(
        provider=LLMProvider.OPENAI,
        api_key="sk-test",
        model="gpt-4o-mini",
        base_url=base_url,
        timeout=10,
        max_tokens=100,
        temperature=0.1,
    )
    return LiteLLMAdapter(config)


def _make_request():
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")])


def _make_openai_bad_request(msg, status_code=400):
    """构造 openai.BadRequestError 实例（跳过需 response 的 __init__）"""
    e = openai.BadRequestError.__new__(openai.BadRequestError)
    Exception.__init__(e, msg)
    e.status_code = status_code
    return e


def _make_litellm_api_error(msg, status_code=400):
    """构造 litellm.exceptions.APIError 实例（真实 __init__ 保证属性完整）"""
    return litellm.exceptions.APIError(status_code, msg, "openai", "gpt-4o")


class TestDetectMaxTokensError:
    """helper 正则匹配各种服务商文案"""

    def test_xunfei_less_or_equal_pattern(self):
        result = _detect_max_tokens_error(
            "'$.parameter.cbm.max_tokens' value must be less or equal than 32768"
        )
        assert result is not None
        limit, msg = result
        assert limit == "32768"
        assert "32768" in msg

    def test_less_than_pattern(self):
        result = _detect_max_tokens_error("max_tokens must be less than 16384")
        assert result is not None
        assert result[0] == "16384"

    def test_at_most_pattern(self):
        result = _detect_max_tokens_error("max_tokens at most 8192")
        assert result is not None
        assert result[0] == "8192"

    def test_no_match_for_unrelated_error(self):
        assert _detect_max_tokens_error("invalid model: foo") is None
        assert _detect_max_tokens_error("connection reset") is None


class TestNonStreamMaxTokensError:
    """非流式 complete：max_tokens 超限返回含限制值的友好提示"""

    @pytest.mark.asyncio
    async def test_openai_bad_request_max_tokens_friendly(self):
        """openai 路径（_native_openai_call）：400 max_tokens 错误含限制值"""
        adapter = _make_adapter(base_url="https://api.example.com/v1")
        exc = _make_openai_bad_request(
            "Error code: 400 - max_tokens value must be less or equal than 32768"
        )
        with patch.object(adapter, "_native_openai_call", side_effect=exc):
            with pytest.raises(LLMError) as ei:
                await adapter.complete(_make_request())
        assert ei.value.status_code == 400
        assert "32768" in str(ei.value), (
            f"应含限制值 32768，实际: {ei.value}"
        )

    @pytest.mark.asyncio
    async def test_litellm_api_error_max_tokens_friendly(self):
        """litellm 路径（acompletion）：400 max_tokens 错误含限制值"""
        adapter = _make_adapter(base_url=None)
        exc = _make_litellm_api_error("max_tokens must be less than 32768")
        with patch("litellm.acompletion", side_effect=exc):
            with pytest.raises(LLMError) as ei:
                await adapter.complete(_make_request())
        assert ei.value.status_code == 400
        assert "32768" in str(ei.value)

    @pytest.mark.asyncio
    async def test_bad_request_non_max_tokens_generic(self):
        """非 max_tokens 的 400 错误返回通用提示，不含限制值"""
        adapter = _make_adapter(base_url="https://api.example.com/v1")
        exc = _make_openai_bad_request("invalid model: foo-bar")
        with patch.object(adapter, "_native_openai_call", side_effect=exc):
            with pytest.raises(LLMError) as ei:
                await adapter.complete(_make_request())
        assert "32768" not in str(ei.value)


class TestStreamMaxTokensError:
    """流式 stream_complete：max_tokens 超限返回含限制值的友好提示"""

    @pytest.mark.asyncio
    async def test_stream_max_tokens_friendly(self):
        """流式路径 generic Exception 分支：max_tokens 错误含限制值"""
        adapter = _make_adapter(base_url=None)
        exc = _make_litellm_api_error("max_tokens must be less than 32768")
        error_chunks = []
        with patch("litellm.acompletion", side_effect=exc):
            async for chunk in adapter.stream_complete(_make_request()):
                if chunk.get("type") == "error":
                    error_chunks.append(chunk)
        assert error_chunks, "应有 error chunk"
        assert error_chunks[0]["error_type"] == "bad_request", (
            f"max_tokens 错误应分类为 bad_request，实际 {error_chunks[0].get('error_type')}"
        )
        assert "32768" in error_chunks[0]["user_message"], (
            f"user_message 应含限制值 32768，实际 {error_chunks[0].get('user_message')}"
        )
