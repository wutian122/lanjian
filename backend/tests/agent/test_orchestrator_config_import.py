"""回归：OrchestratorAgent 模块顶层必须导入 get_agent_config。

v6.4.0（v3.0 架构升级，commit 368ef0e）在 orchestrator.py 顶部漏写
``from app.services.agent.config import get_agent_config``，但 ``__init__``
与主循环中存在 8 处裸调用（283/288/294/300/301/302/941/2355 行）。
后果：每次构造 OrchestratorAgent 都在 ``__init__`` 第 283 行抛
``NameError: name 'get_agent_config' is not defined``，所有代码审计任务
在编排器初始化阶段 100% 崩溃（amd64 / arm64 两台生产实证）。

本测试真实构造 OrchestratorAgent，守住“编排器可实例化”这条底线。
"""

from unittest.mock import MagicMock

from app.services.agent.agents.orchestrator import OrchestratorAgent


def test_orchestrator_agent_construction_does_not_raise_name_error():
    """构造编排器不得因缺失顶层导入而抛 NameError。"""
    agent = OrchestratorAgent(
        llm_service=MagicMock(),
        tools={},
        event_emitter=MagicMock(),
        sub_agents={},
    )
    assert agent is not None
    # context_compression_enabled 默认开启，__init__ 会调用 get_agent_config()
    # 并构造 ContextManager；能到达这里即证明裸调用点的名字已正确绑定。
    assert hasattr(agent, "context_manager")


def test_orchestrator_module_exposes_get_agent_config():
    """模块顶层应能直接访问 get_agent_config（裸调用依赖此名字绑定）。"""
    from app.services.agent.agents import orchestrator as orch_module

    assert callable(getattr(orch_module, "get_agent_config", None))
