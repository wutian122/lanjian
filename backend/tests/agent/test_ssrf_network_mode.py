"""Task 3 (sandbox-verification-hard-gate): SSRF 确定性 PoC 网络参数传递。

缺陷：SSRF 确定性模板生成 network_enabled=True 的命令（容器内真实探测
169.254.169.254 云元数据端点），但 _run_deterministic_sandbox_commands 调
execute_with_files 时丢弃 network_mode（落形参默认 "none"），metadata 探测
永远 blocked，PoC 永远走 degraded 分支 → 无动态证据 → not_reproducible。

修复契约（spec Requirement: SSRF 确定性 PoC 网络参数 SHALL 传递到容器）：
- network_enabled=True 且 SANDBOX_NETWORK_ENABLED=True → network_mode="bridge"
- 开关关闭 → 显式 "none"（行为不变；且必须显式传值——None 会被 Docker SDK
  当作默认网络即 bridge，反而开网）
- 未请求网络的命令即使开关开 → "none"（最小权限）
- LLM 入口（SandboxTool._execute）：network_enabled=true 必须 AND 全局开关，
  LLM 不得绕过管理员 kill-switch 自授网络
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.services.agent.agents.verification import VerificationAgent
from app.services.agent.tools.sandbox_tool import SandboxTool

OK_RESULT = {
    "success": True,
    "stdout": "ok\n",
    "stderr": "",
    "exit_code": 0,
    "error": None,
}


def _ssrf_command() -> dict:
    return {
        "label": "发现1: ssrf (app.py:10)",
        "input": {
            "command": (
                "cat > /tmp/poc_0.py << 'POC_EOF'\n"
                "import urllib.request\n"
                "urllib.request.urlopen('http://169.254.169.254/', timeout=5)\n"
                "POC_EOF\n"
                "python3 /tmp/poc_0.py"
            ),
            "timeout": 30,
            "network_enabled": True,
        },
        "finding_id": "fid-ssrf-0",
        "vuln_type": "ssrf",
        "file_path": "app.py",
    }


def _sqli_command() -> dict:
    """非网络型模板：input 无 network_enabled 字段。"""
    return {
        "label": "发现2: sql_injection (db.py:20)",
        "input": {"command": "echo sqlipoc", "timeout": 30},
        "finding_id": "fid-sqli-1",
        "vuln_type": "sql_injection",
        "file_path": "db.py",
    }


def _mock_sandbox_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.execute_with_files = AsyncMock(return_value=OK_RESULT)
    return mgr


def _make_verification_agent(sandbox_mgr: MagicMock) -> VerificationAgent:
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    # _get_sandbox_manager 遍历 tools 找带 sandbox_manager 属性的工具
    fake_tool = MagicMock()
    fake_tool.sandbox_manager = sandbox_mgr
    agent.tools = {"sandbox_exec": fake_tool}
    # 计数器/索引在 run() 流程初始化；直调确定性执行器前补齐
    agent._sandbox_exec_calls = 0
    agent._sandbox_exec_attempts = 0
    agent._sandbox_exec_success = 0
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}
    agent._verified_finding_indices = set()
    return agent


# ---------- 确定性路径（_run_deterministic_sandbox_commands） ----------

@pytest.mark.asyncio
async def test_deterministic_ssrf_poc_gets_bridge_when_switch_on(tmp_path, monkeypatch):
    """Scenario 1: SSRF 模板 network_enabled=true 且系统允许联网 → 容器以 bridge 创建。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    mgr = _mock_sandbox_manager()
    agent = _make_verification_agent(mgr)

    await agent._run_deterministic_sandbox_commands([_ssrf_command()], str(tmp_path))

    mgr.execute_with_files.assert_awaited_once()
    assert mgr.execute_with_files.await_args.kwargs.get("network_mode") == "bridge"


@pytest.mark.asyncio
async def test_deterministic_ssrf_poc_stays_none_when_switch_off(tmp_path, monkeypatch):
    """Scenario 2: SANDBOX_NETWORK_ENABLED=false → 容器仍 none（degraded 为现状预期）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    mgr = _mock_sandbox_manager()
    agent = _make_verification_agent(mgr)

    await agent._run_deterministic_sandbox_commands([_ssrf_command()], str(tmp_path))

    mgr.execute_with_files.assert_awaited_once()
    # 必须显式 "none"：传 None/不传会落 Docker SDK 默认网络（bridge）反而开网
    assert mgr.execute_with_files.await_args.kwargs.get("network_mode") == "none"


@pytest.mark.asyncio
async def test_deterministic_non_network_command_stays_none_even_switch_on(
    tmp_path, monkeypatch
):
    """最小权限：未请求网络的 PoC（如 sql_injection 内存 SQLite）即使全局开关开也不给网络。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    mgr = _mock_sandbox_manager()
    agent = _make_verification_agent(mgr)

    await agent._run_deterministic_sandbox_commands([_sqli_command()], str(tmp_path))

    mgr.execute_with_files.assert_awaited_once()
    assert mgr.execute_with_files.await_args.kwargs.get("network_mode") == "none"


def test_network_mode_helper_mapping(monkeypatch):
    """network_enabled × 全局开关 真值表（兜底路径与确定性路径共用同一映射）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    assert VerificationAgent._network_mode_for_command({"network_enabled": True}) == "bridge"
    assert VerificationAgent._network_mode_for_command({"network_enabled": False}) == "none"
    assert VerificationAgent._network_mode_for_command({}) == "none"
    assert VerificationAgent._network_mode_for_command(None) == "none"

    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    assert VerificationAgent._network_mode_for_command({"network_enabled": True}) == "none"
    assert VerificationAgent._network_mode_for_command({}) == "none"


# ---------- LLM 路径（SandboxTool._execute 的 sandbox_exec） ----------

def _mock_llm_sandbox_manager(switch_on: bool) -> MagicMock:
    mgr = MagicMock()
    mgr.config = SimpleNamespace(
        network_enabled=switch_on,
        network_mode="bridge" if switch_on else "none",
    )
    mgr.is_available = True
    mgr.initialize = AsyncMock()
    mgr.execute_tool_command = AsyncMock(return_value=OK_RESULT)
    mgr.execute_command = AsyncMock(return_value=OK_RESULT)
    return mgr


@pytest.mark.asyncio
async def test_llm_sandbox_exec_bridge_when_requested_and_switch_on(tmp_path):
    """LLM 请求网络且全局开关开 → bridge（正常放行，AND 门禁不误伤合法使用）。"""
    mgr = _mock_llm_sandbox_manager(switch_on=True)
    tool = SandboxTool(mgr, str(tmp_path))

    result = await tool._execute(command="python3 app.py", timeout=5, network_enabled=True)

    assert result.success is True
    mgr.execute_tool_command.assert_awaited_once()
    assert mgr.execute_tool_command.await_args.kwargs.get("network_mode") == "bridge"
    assert result.metadata["network_mode"] == "bridge"


@pytest.mark.asyncio
async def test_llm_sandbox_exec_cannot_bypass_switch_off(tmp_path):
    """安全边界：LLM 传 network_enabled=true 但全局开关关 → 容器仍 none。

    管理员 kill-switch（SANDBOX_NETWORK_ENABLED）不可被模型参数绕过。
    """
    mgr = _mock_llm_sandbox_manager(switch_on=False)
    tool = SandboxTool(mgr, str(tmp_path))

    result = await tool._execute(command="python3 app.py", timeout=5, network_enabled=True)

    assert result.success is True
    mgr.execute_tool_command.assert_awaited_once()
    assert mgr.execute_tool_command.await_args.kwargs.get("network_mode") == "none"
    assert result.metadata["network_mode"] == "none"


@pytest.mark.asyncio
async def test_llm_sandbox_exec_no_request_stays_none_even_switch_on(tmp_path):
    """LLM 未请求网络 → 即使全局开关开也保持 none（最小权限，与确定性路径一致）。"""
    mgr = _mock_llm_sandbox_manager(switch_on=True)
    tool = SandboxTool(mgr, str(tmp_path))

    result = await tool._execute(command="python3 app.py", timeout=5, network_enabled=False)

    mgr.execute_tool_command.assert_awaited_once()
    assert mgr.execute_tool_command.await_args.kwargs.get("network_mode") == "none"
    assert result.metadata["network_mode"] == "none"
