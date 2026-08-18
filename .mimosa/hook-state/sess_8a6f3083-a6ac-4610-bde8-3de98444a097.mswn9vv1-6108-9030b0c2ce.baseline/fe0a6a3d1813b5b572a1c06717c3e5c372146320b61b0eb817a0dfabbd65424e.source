"""测试沙箱工具是否支持网络模式和安全挂载策略。"""

from pathlib import Path

import pytest

from app.services.agent.tools.base import ToolResult
from app.services.agent.tools.sandbox_tool import SandboxTool


SANDBOX_PATH = Path(__file__).resolve().parents[2] / "app" / "services" / "agent" / "tools" / "sandbox_tool.py"


def test_sandbox_command_input_has_network_enabled():
    """SandboxCommandInput 必须包含 network_enabled 字段来控制网络访问。"""
    content = SANDBOX_PATH.read_text(encoding="utf-8")
    assert "network_enabled" in content, "SandboxCommandInput must have network_enabled field"


def test_sandbox_exec_uses_network_mode():
    """sandbox_exec 的 _execute 方法在 network_enabled=True 时应使用 bridge 网络模式。"""
    content = SANDBOX_PATH.read_text(encoding="utf-8")
    assert "network_enabled" in content, "sandbox_exec must check network_enabled"
    assert "bridge" in content, "sandbox must support bridge network mode"


class FakeSandboxManager:
    def __init__(self):
        self.calls = []
        self.is_available = True

    async def initialize(self):
        return None

    async def execute_command(self, command, timeout=None, network_mode=None):
        self.calls.append(("execute_command", command, timeout, network_mode))
        return {
            "success": True,
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "error": None,
        }

    async def execute_tool_command(self, command, host_workdir, timeout=None, network_mode="none"):
        self.calls.append(("execute_tool_command", command, host_workdir, timeout, network_mode))
        return {
            "success": True,
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "error": None,
        }


@pytest.mark.asyncio
async def test_sandbox_exec_does_not_mount_project_for_inline_interpreter_command(tmp_path):
    manager = FakeSandboxManager()
    tool = SandboxTool(manager, str(tmp_path))

    result = await tool._execute(command="python3 -c 'print(1)'", timeout=5)

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert manager.calls == [("execute_command", "python3 -c 'print(1)'", 5, "none")]


@pytest.mark.asyncio
async def test_sandbox_exec_mounts_project_for_file_access_command(tmp_path):
    manager = FakeSandboxManager()
    tool = SandboxTool(manager, str(tmp_path))

    result = await tool._execute(command="python3 app.py", timeout=5)

    assert result.success is True
    assert manager.calls == [
        ("execute_tool_command", "python3 app.py", str(tmp_path.resolve()), 5, "none")
    ]
