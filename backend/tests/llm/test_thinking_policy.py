"""思考策略倒转（2026-09-29 层 2c）：思考默认强制关闭，显式双开关才放行。

背景：实验实测 10.129.2.101（SGLang 部署 Qwen3_5 魔改权重）在 enable_thinking
开启时 2048/8192 tokens 思考均不收敛、正文 0 字、finish_reason=length；而
enable_thinking=False 时正文完整、自然停。旧策略"默认跟随服务端（开思考）"
在本服务端上等于默认自毁，故倒转为默认强制关闭。

契约：
- 默认（LLM_ENABLE_THINKING/LLM_THINKING_SEPARATE_BUDGET 均 False）→ 出站
  参数强制 chat_template_kwargs={"enable_thinking": False}，请求级显式 True
  一并覆盖（setdefault 逆转为强制赋值）；
- LLM_ENABLE_THINKING=True 但无独立预算开关 → 仍强制 False（单预算 + 思考
  的组合在服务端无分离预算支持时禁止出站）；
- 双开关均 True（服务端已支持思考/正文预算分离）→ 请求级 True 合法放行、
  不注入 False；
- 兼容旧开关 LLM_DISABLE_THINKING：无论新旧开关语义，出站结果一致。
"""
import os
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.llm.adapters.litellm_adapter import (
    LiteLLMAdapter,
    _thinking_off_allowed,
)
from app.services.llm.types import LLMConfig, LLMProvider

DUMMY_API_KEY = os.environ.get("LANJIAN_TEST_DUMMY_KEY", "unit-test-dummy-key")


def _make_adapter() -> LiteLLMAdapter:
    return LiteLLMAdapter(LLMConfig(
        provider=LLMProvider.OPENAI,
        api_key=DUMMY_API_KEY,
        model="Qwen3.8-27B",
        base_url="http://10.129.2.101:8001/v1",
        timeout=10, max_tokens=100, temperature=0.1,
    ))


class TestThinkingPolicyReversed:
    def test_default_policy_enforces_off(self):
        with patch.object(settings, "LLM_ENABLE_THINKING", False), \
             patch.object(settings, "LLM_THINKING_SEPARATE_BUDGET", False):
            assert _thinking_off_allowed() is True

    def test_enable_requires_separate_budget_flag(self):
        with patch.object(settings, "LLM_ENABLE_THINKING", True), \
             patch.object(settings, "LLM_THINKING_SEPARATE_BUDGET", False):
            assert _thinking_off_allowed() is True

    def test_enable_with_separate_budget_allows_thinking(self):
        with patch.object(settings, "LLM_ENABLE_THINKING", True), \
             patch.object(settings, "LLM_THINKING_SEPARATE_BUDGET", True):
            assert _thinking_off_allowed() is False

    def test_merge_overrides_request_level_true_by_default(self):
        with patch.object(settings, "LLM_ENABLE_THINKING", False), \
             patch.object(settings, "LLM_THINKING_SEPARATE_BUDGET", False):
            merged = _make_adapter()._merge_config_sampling_params(
                {"chat_template_kwargs": {"enable_thinking": True}})
            assert merged["chat_template_kwargs"]["enable_thinking"] is False

    def test_merge_injects_off_when_no_extra_params(self):
        with patch.object(settings, "LLM_ENABLE_THINKING", False), \
             patch.object(settings, "LLM_THINKING_SEPARATE_BUDGET", False):
            merged = _make_adapter()._merge_config_sampling_params(None)
            assert merged.get("chat_template_kwargs", {}).get("enable_thinking") is False

    def test_merge_respects_true_when_both_flags(self):
        with patch.object(settings, "LLM_ENABLE_THINKING", True), \
             patch.object(settings, "LLM_THINKING_SEPARATE_BUDGET", True):
            merged = _make_adapter()._merge_config_sampling_params(
                {"chat_template_kwargs": {"enable_thinking": True}})
            assert merged["chat_template_kwargs"]["enable_thinking"] is True

    def test_legacy_disable_flag_same_result(self):
        """旧开关 LLM_DISABLE_THINKING=true（存量部署环境）出站结果一致。"""
        with patch.object(settings, "LLM_DISABLE_THINKING", True):
            off_old = _make_adapter()._merge_config_sampling_params(None)
        assert off_old["chat_template_kwargs"]["enable_thinking"] is False
