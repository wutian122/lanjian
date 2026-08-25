"""REQ-VP-2: 确定性沙箱执行与 sandbox_exec 工具发射 sandbox_* 事件。

生产实测：三个审计任务 0 条 sandbox_* 事件，前端看不到验证过程。
本测试锁定事件发射行为。
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import VerificationAgent
from app.services.agent.tools.base import ToolResult


def _agent() -> VerificationAgent:
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    agent.emit_event = AsyncMock()
    agent._record_sandbox_attempt = MagicMock()
    agent._is_sandbox_success = MagicMock(return_value=True)
    agent._sandbox_exec_calls = 0
    agent._sandbox_exec_attempts = 0
    agent._sandbox_exec_success = 0
    agent._verified_finding_indices = set()
    return agent


def test_deterministic_exec_emits_sandbox_start_and_result():
    """REQ-VP-2: 预生成 PoC 执行前发 sandbox_start，执行后发 sandbox_result 含 finding_id/exit_code。"""
    agent = _agent()
    mgr = MagicMock()
    mgr.execute_with_files = AsyncMock(
        return_value={"exit_code": 0, "stdout": "ok", "stderr": "", "error": None}
    )
    agent._get_sandbox_manager = MagicMock(return_value=mgr)
    cmds = [
        {
            "label": "L1",
            "finding_id": "abc123",
            "input": {"command": "python3 /tmp/poc_0.py", "timeout": 30},
        }
    ]
    asyncio.run(agent._run_deterministic_sandbox_commands(cmds, "/tmp/proj"))
    calls = agent.emit_event.call_args_list
    types = [c.args[0] for c in calls]
    assert "sandbox_start" in types
    assert "sandbox_result" in types
    result_call = [c for c in calls if c.args[0] == "sandbox_result"][0]
    assert result_call.kwargs.get("finding_id") == "abc123"
    assert result_call.kwargs.get("metadata", {}).get("exit_code") == 0


def test_deterministic_exec_exception_emits_failed_result():
    """REQ-VP-2: 执行异常仍发 sandbox_result 标记失败（exit_code -1），不静默吞。"""
    agent = _agent()
    mgr = MagicMock()
    mgr.execute_with_files = AsyncMock(side_effect=RuntimeError("sandbox boom"))
    agent._get_sandbox_manager = MagicMock(return_value=mgr)
    cmds = [
        {
            "label": "L1",
            "finding_id": "abc123",
            "input": {"command": "python3 x.py", "timeout": 30},
        }
    ]
    asyncio.run(agent._run_deterministic_sandbox_commands(cmds, "/tmp/proj"))
    result_calls = [
        c for c in agent.emit_event.call_args_list if c.args[0] == "sandbox_result"
    ]
    assert result_calls
    assert result_calls[0].kwargs.get("metadata", {}).get("exit_code") == -1


def test_execute_tool_sandbox_exec_emits_events():
    """REQ-VP-2: sandbox_exec 工具经 execute_tool 包装层发 sandbox_exec + sandbox_result。"""
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    tool = MagicMock()
    tool.execute = AsyncMock(
        return_value=ToolResult(success=True, data="poc output", metadata={"exit_code": 0})
    )
    agent.tools["sandbox_exec"] = tool
    agent.emit_event = AsyncMock()
    agent.emit_tool_call = AsyncMock()
    agent.emit_tool_result = AsyncMock()
    agent._tool_calls = 0
    agent._timeout_config = {}
    asyncio.run(
        agent.execute_tool("sandbox_exec", {"command": "python3 x.py", "finding_id": "xyz"})
    )
    types = [c.args[0] for c in agent.emit_event.call_args_list]
    assert "sandbox_exec" in types
    assert "sandbox_result" in types
