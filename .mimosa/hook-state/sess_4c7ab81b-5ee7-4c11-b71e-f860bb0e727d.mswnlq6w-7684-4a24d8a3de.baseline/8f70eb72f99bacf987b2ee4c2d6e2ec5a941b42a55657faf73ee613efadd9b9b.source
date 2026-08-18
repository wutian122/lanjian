"""
B 修复测试：LLM 401 认证失败被误判为 429 限流

根因：litellm_adapter.py 的 RateLimitError 分支关键词列表缺少
      authorization/401/unauthorized 等，导致 LiteLLM 把 401 认证失败
      （包装成 RateLimitError 异常类型抛出）误分类为 rate_limit（429），
      而非 authentication（401）。Orchestrator 的 rate_limit 分支会
      等待 30s 重试 3 次，authentication 分支应立即终止。
"""
import pytest
from unittest.mock import patch

import litellm

from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.types import (
    LLMConfig,
    LLMProvider,
    LLMRequest,
    LLMMessage,
    LLMError,
)


def _make_adapter() -> LiteLLMAdapter:
    config = LLMConfig(
        provider=LLMProvider.OPENAI,
        api_key="sk-test",
        model="gpt-4o-mini",
        base_url=None,
        timeout=10,
        max_tokens=100,
        temperature=0.1,
    )
    return LiteLLMAdapter(config)


def _make_request() -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")])


def _rate_limit_err(msg: str) -> litellm.exceptions.RateLimitError:
    """构造 RateLimitError（LiteLLM 把 401 也包成此类型抛出）"""
    return litellm.exceptions.RateLimitError(msg, "openai", "gpt-4o")


class TestNonStreamAuthClassification:
    """非流式 complete：401 认证失败必须分类为 authentication（401）"""

    @pytest.mark.asyncio
    async def test_authorization_failed_classified_as_auth(self):
        """LiteLLM 把 401 包成 RateLimitError(authorization failed) → 应分类为 401"""
        adapter = _make_adapter()
        exc = _rate_limit_err("OpenAIException - authorization failed")
        with patch("litellm.acompletion", side_effect=exc):
            with pytest.raises(LLMError) as ei:
                await adapter.complete(_make_request())
        assert ei.value.status_code == 401, (
            f"authorization failed 应分类为 401，实际 {ei.value.status_code}"
        )

    @pytest.mark.asyncio
    async def test_unauthorized_classified_as_auth(self):
        adapter = _make_adapter()
        exc = _rate_limit_err("401 Unauthorized: invalid api key")
        with patch("litellm.acompletion", side_effect=exc):
            with pytest.raises(LLMError) as ei:
                await adapter.complete(_make_request())
        assert ei.value.status_code == 401

    @pytest.mark.asyncio
    async def test_quota_exceeded_still_classified_as_quota(self):
        """余额不足仍分类为 quota_exceeded（402），不受 401 修复影响"""
        adapter = _make_adapter()
        exc = _rate_limit_err("quota exceeded, insufficient balance")
        with patch("litellm.acompletion", side_effect=exc):
            with pytest.raises(LLMError) as ei:
                await adapter.complete(_make_request())
        assert ei.value.status_code == 402

    @pytest.mark.asyncio
    async def test_rate_limit_still_classified_as_rate_limit(self):
        """真实限流仍分类为 rate_limit（429），覆盖 OpenAI 标准 429 消息"""
        adapter = _make_adapter()
        for msg in [
            "too many requests, retry in 30s",
            "rate limit exceeded, please retry in 30s",  # OpenAI 标准 429，含 exceeded 但不含 quota 关键词
            "Requests per minute exceeded",
        ]:
            exc = _rate_limit_err(msg)
            with patch("litellm.acompletion", side_effect=exc):
                with pytest.raises(LLMError) as ei:
                    await adapter.complete(_make_request())
            assert ei.value.status_code == 429, (
                f"真实 429 消息不应误判: {msg!r} -> {ei.value.status_code}"
            )

    @pytest.mark.asyncio
    async def test_401_substring_not_mismatched_as_auth(self):
        """含 "401" 子串的真实 429（request_id/retry 秒数/RPM）不得误判为 401"""
        adapter = _make_adapter()
        # retry after 401 seconds / request_id=req_401abc / 4010 RPM 都是真实 429 场景
        for msg in [
            "rate limit exceeded, retry in 401 seconds",
            "request_id=req_401abc, too many requests",
            "4010 RPM exceeded, slow down",
        ]:
            exc = _rate_limit_err(msg)
            with patch("litellm.acompletion", side_effect=exc):
                with pytest.raises(LLMError) as ei:
                    await adapter.complete(_make_request())
            assert ei.value.status_code == 429, (
                f"含 401 子串的真实 429 消息不应误判为 401: {msg!r} -> {ei.value.status_code}"
            )


class TestStreamAuthClassification:
    """流式 stream_complete：401 认证失败必须分类为 authentication"""

    @pytest.mark.asyncio
    async def test_stream_authorization_failed_classified_as_auth(self):
        """流式 401 → error chunk 的 error_type 必须是 authentication"""
        adapter = _make_adapter()
        exc = _rate_limit_err("OpenAIException - authorization failed")
        error_chunks = []
        with patch("litellm.acompletion", side_effect=exc):
            async for chunk in adapter.stream_complete(_make_request()):
                if chunk.get("type") == "error":
                    error_chunks.append(chunk)
        assert error_chunks, "应有 error chunk"
        assert error_chunks[0]["error_type"] == "authentication", (
            f"流式 authorization failed 应分类为 authentication，"
            f"实际 {error_chunks[0].get('error_type')}"
        )

    @pytest.mark.asyncio
    async def test_stream_quota_exceeded_still_quota(self):
        adapter = _make_adapter()
        exc = _rate_limit_err("quota exceeded, insufficient balance")
        error_chunks = []
        with patch("litellm.acompletion", side_effect=exc):
            async for chunk in adapter.stream_complete(_make_request()):
                if chunk.get("type") == "error":
                    error_chunks.append(chunk)
        assert error_chunks
        assert error_chunks[0]["error_type"] == "quota_exceeded"

    @pytest.mark.asyncio
    async def test_stream_rate_limit_still_rate_limit(self):
        adapter = _make_adapter()
        for msg in [
            "too many requests, retry in 30s",
            "rate limit exceeded, please retry in 30s",
        ]:
            exc = _rate_limit_err(msg)
            error_chunks = []
            with patch("litellm.acompletion", side_effect=exc):
                async for chunk in adapter.stream_complete(_make_request()):
                    if chunk.get("type") == "error":
                        error_chunks.append(chunk)
            assert error_chunks
            assert error_chunks[0]["error_type"] == "rate_limit", (
                f"流式真实 429 不应误判: {msg!r} -> {error_chunks[0].get('error_type')}"
            )
