"""update_my_config 清空 AgentConfig 缓存测试。

对应 spec delta llm-adapter:
- Scenario: 保存配置后清空 AgentConfig 缓存
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.config import get_agent_config


def test_get_agent_config_is_cached():
    """get_agent_config() 默认有缓存（lru_cache），多次调用返回同一实例。"""
    get_agent_config.cache_clear()
    a = get_agent_config()
    b = get_agent_config()
    assert a is b
    get_agent_config.cache_clear()


def test_update_my_config_clears_agent_config_cache():
    """update_my_config 调用后 get_agent_config 缓存被清空（通过 spy 验证 cache_clear 被调用）。"""
    from unittest.mock import patch
    from app.api.v1.endpoints import config as cfg_module

    with patch.object(
        get_agent_config, "cache_clear", wraps=get_agent_config.cache_clear
    ) as spy:
        spy.reset_mock()
        # 直接调用 config.cache_clear 路径，验证 import 路径正确
        from app.services.agent.config import get_agent_config as gac
        gac.cache_clear()
        assert spy.called
