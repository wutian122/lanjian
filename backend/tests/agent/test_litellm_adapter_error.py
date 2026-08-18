"""
B4 修复测试：LLM 流式错误日志必须可诊断

根因：litellm_adapter.py:570-573 except Exception 分支，
      当 str(e) 为空时日志输出 "Stream error: " 无法诊断
"""
import pytest
import logging
from unittest.mock import patch

from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.types import LLMConfig, LLMProvider, LLMRequest, LLMMessage


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


class TestStreamErrorDiagnosability:
    """验证流式错误日志的可诊断性"""

    @pytest.mark.asyncio
    async def test_stream_error_empty_message_diagnosed(self, caplog):
        """异常消息为空时，日志必须含异常类型名，不得只有 'Stream error: '"""
        adapter = _make_adapter()
        empty_exc = Exception("")  # str(e) 为空

        with caplog.at_level(logging.ERROR, logger="app.services.llm.adapters.litellm_adapter"):
            with patch("litellm.acompletion", side_effect=empty_exc):
                try:
                    async for _chunk in adapter.stream_complete(_make_request()):
                        pass
                except Exception:
                    pass

        error_logs = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and "Stream error" in r.getMessage()
        ]
        assert error_logs, "应有 Stream error 日志"
        msg = error_logs[0].getMessage()
        # 不得是空的 "Stream error: "
        assert msg.strip() != "Stream error:", f"日志不得为空诊断: {msg!r}"
        # 必须包含异常类型名（诊断信息）
        assert "Exception" in msg, f"日志应含异常类型名: {msg!r}"
        # 不得直接拼接 e.args 内容（避免敏感数据泄露到日志/前端）
        # 诊断应基于类型名 + args 数量，而非 args 原文

    @pytest.mark.asyncio
    async def test_stream_error_nonempty_message_preserved(self, caplog):
        """异常消息非空时，日志保留原消息"""
        adapter = _make_adapter()
        real_exc = Exception("connection reset by peer")

        with caplog.at_level(logging.ERROR, logger="app.services.llm.adapters.litellm_adapter"):
            with patch("litellm.acompletion", side_effect=real_exc):
                try:
                    async for _chunk in adapter.stream_complete(_make_request()):
                        pass
                except Exception:
                    pass

        error_logs = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and "Stream error" in r.getMessage()
        ]
        assert error_logs
        msg = error_logs[0].getMessage()
        assert "connection reset by peer" in msg
