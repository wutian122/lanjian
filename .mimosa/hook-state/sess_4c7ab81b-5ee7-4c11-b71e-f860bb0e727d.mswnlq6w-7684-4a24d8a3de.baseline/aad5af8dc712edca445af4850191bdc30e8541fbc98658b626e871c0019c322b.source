"""
B6 修复测试：流式超时配置回退链一致

根因分析修正：get_agent_timeout_config() 代码语义正确（用户配置 > 全局 120），
日志中 60s 来自 DB 用户配置 llmFirstTokenTimeout=60 / llmStreamTimeout=60。
本测试验证回退语义正确，确保无用户配置时取全局 120s。
实际修复 = 修正 DB 用户配置（60→120），代码无需改动。
"""
import pytest
from unittest.mock import patch

from app.services.llm.service import LLMService


class TestTimeoutConfigFallback:
    """验证 get_agent_timeout_config 回退链"""

    def test_timeout_config_falls_back_to_global(self):
        """无用户自定义超时时，回退到全局 settings（120s）"""
        with patch("app.services.llm.service.settings") as mock_settings:
            mock_settings.LLM_FIRST_TOKEN_TIMEOUT = 120
            mock_settings.LLM_STREAM_TIMEOUT = 120
            mock_settings.AGENT_TIMEOUT_SECONDS = 1800
            mock_settings.SUB_AGENT_TIMEOUT_SECONDS = 600
            mock_settings.TOOL_TIMEOUT_SECONDS = 60

            service = LLMService(user_config={})  # 无 llmConfig
            config = service.get_agent_timeout_config()

        assert config["llm_first_token_timeout"] == 120
        assert config["llm_stream_timeout"] == 120

    def test_timeout_config_user_override_priority(self):
        """用户自定义超时优先于全局配置"""
        with patch("app.services.llm.service.settings") as mock_settings:
            mock_settings.LLM_FIRST_TOKEN_TIMEOUT = 120
            mock_settings.LLM_STREAM_TIMEOUT = 120
            mock_settings.AGENT_TIMEOUT_SECONDS = 1800
            mock_settings.SUB_AGENT_TIMEOUT_SECONDS = 600
            mock_settings.TOOL_TIMEOUT_SECONDS = 60

            service = LLMService(user_config={
                "llmConfig": {
                    "llmFirstTokenTimeout": 90,
                    "llmStreamTimeout": 90,
                }
            })
            config = service.get_agent_timeout_config()

        assert config["llm_first_token_timeout"] == 90
        assert config["llm_stream_timeout"] == 90
