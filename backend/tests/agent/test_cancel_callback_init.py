"""复现 _cancel_callback 未初始化 bug 的最小测试。

根因：BaseAgent.__init__ 未初始化 self._cancel_callback，
is_cancelled property 访问时抛 AttributeError。
生产路径因 agent_tasks.py 总会调 set_cancel_callback 而不触发，
但测试 fixture 和动态子 Agent 不调 set_cancel_callback 时会崩。
"""
import pytest
from app.services.agent.agents.recon import ReconAgent


def test_base_agent_cancel_callback_initialized():
    """__init__ 必须初始化 _cancel_callback，否则 is_cancelled 崩溃。"""
    from unittest.mock import MagicMock, AsyncMock
    agent = ReconAgent(
        llm_service=MagicMock(),
        tools={},
        event_emitter=MagicMock(),
    )
    # is_cancelled property 不应抛 AttributeError
    # 当前 bug：_cancel_callback 未初始化 -> AttributeError
    assert agent.is_cancelled is False
    # _cancel_callback 应初始化为 None
    assert agent._cancel_callback is None
