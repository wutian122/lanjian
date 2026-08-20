"""
Semgrep prescan 异步化 TDD 测试。

验证 _run_semgrep_prescan 从同步 subprocess.run 迁移到 asyncio.create_subprocess_exec
后，事件循环不再被冻结，SSE 心跳能正常发送。
"""
import asyncio
import ast
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_minimal_agent():
    """构造最小化 OrchestratorAgent 实例，绕过 __init__ 避免完整依赖。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._runtime_context = {"project_root": "."}
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    # emit_event 继承自 BaseAgent，需要 event_emitter 与 config.name（self.name 属性）
    agent.config = SimpleNamespace(name="Orchestrator")
    return agent


def _fake_subprocess_run_side_effect(cmd, **kwargs):
    """模拟 subprocess.run 返回假结果，防止真实调用 semgrep。"""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    if "--version" in cmd:
        result.stdout = b"1.0.0"
    else:
        result.stdout = '{"results": []}'
    return result


def _make_fake_async_proc(communicate_delay: float = 0.05):
    """构造假异步子进程对象，communicate() 模拟指定耗时。"""
    fake_proc = MagicMock()
    fake_proc.returncode = 0

    async def fake_communicate():
        await asyncio.sleep(communicate_delay)
        return (b'{"results": []}', b"")

    fake_proc.communicate = fake_communicate
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    return fake_proc


@pytest.mark.asyncio
async def test_prescan_does_not_block_event_loop():
    """验证 prescan 期间事件循环不被冻结，心跳协程仍能执行。

    RED 断言：当前同步 subprocess.run 实现会阻塞事件循环，心跳无法发送。
    GREEN 断言：异步 asyncio.create_subprocess_exec 实现释放事件循环，心跳正常。
    """
    agent = _make_minimal_agent()
    fake_proc = _make_fake_async_proc(communicate_delay=0.05)

    async def mock_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    heartbeats = []

    async def heartbeat():
        while True:
            heartbeats.append(time.time())
            await asyncio.sleep(0.02)

    with patch("subprocess.run", side_effect=_fake_subprocess_run_side_effect):
        with patch(
            "app.services.agent.agents.orchestrator.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess_exec,
        ):
            hb_task = asyncio.create_task(heartbeat())
            try:
                result = await agent._run_semgrep_prescan()
            finally:
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

    # 验证返回结构
    assert isinstance(result, dict)
    assert "findings" in result
    assert "hot_files" in result
    assert "scan_success" in result
    assert result["scan_success"] is True

    # 核心断言：prescan 期间事件循环未被冻结，心跳至少记录 3 次
    assert len(heartbeats) >= 3, (
        f"事件循环被冻结，心跳仅记录 {len(heartbeats)} 次（预期 >= 3）。"
        f" 这通常意味着 subprocess.run 未替换为 asyncio.create_subprocess_exec。"
    )


@pytest.mark.asyncio
async def test_prescan_emits_tool_call_events():
    """验证每规则集前后发射 tool_call_start / tool_call_end 事件。

    - 5 个规则集，每个至少 1 次 tool_call_start 和 1 次 tool_call_end
    - tool_call_start 的 metadata.tool.name 以 semgrep_prescan_ 开头
    """
    agent = _make_minimal_agent()
    # 用 AsyncMock 拦截 emit_event 调用
    agent.emit_event = AsyncMock()
    fake_proc = _make_fake_async_proc(communicate_delay=0.01)

    async def mock_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    with patch("subprocess.run", side_effect=_fake_subprocess_run_side_effect):
        with patch(
            "app.services.agent.agents.orchestrator.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess_exec,
        ):
            await agent._run_semgrep_prescan()

    # 收集所有 emit_event 调用
    call_args_list = agent.emit_event.call_args_list

    tool_call_starts = []
    tool_call_ends = []
    for call_obj in call_args_list:
        event_type = call_obj.args[0] if call_obj.args else ""
        if event_type == "tool_call_start":
            tool_call_starts.append(call_obj)
        elif event_type == "tool_call_end":
            tool_call_ends.append(call_obj)

    # 断言：至少 5 次 tool_call_start 和 tool_call_end（5 个规则集）
    assert len(tool_call_starts) >= 5, (
        f"期望 tool_call_start 至少 5 次，实际 {len(tool_call_starts)} 次"
    )
    assert len(tool_call_ends) >= 5, (
        f"期望 tool_call_end 至少 5 次，实际 {len(tool_call_ends)} 次"
    )

    # 断言：tool_call_start 的 metadata.tool.name 以 semgrep_prescan_ 开头
    for call_obj in tool_call_starts:
        kwargs = call_obj.kwargs if hasattr(call_obj, "kwargs") else {}
        metadata = kwargs.get("metadata", {})
        tool = metadata.get("tool", {}) if isinstance(metadata, dict) else {}
        if tool:
            name = tool.get("name", "")
            assert name.startswith("semgrep_prescan_"), (
                f"tool.name 应以 semgrep_prescan_ 开头，实际: {name}"
            )

    # 断言：tool_call_end 的 metadata.tool 含 findings_count
    for call_obj in tool_call_ends:
        kwargs = call_obj.kwargs if hasattr(call_obj, "kwargs") else {}
        metadata = kwargs.get("metadata", {})
        tool = metadata.get("tool", {}) if isinstance(metadata, dict) else {}
        if tool:
            assert "findings_count" in tool, (
                f"tool_call_end 的 metadata.tool 应含 findings_count，实际: {tool}"
            )


def test_prescan_regression_no_subprocess_run():
    """回归防护：_run_semgrep_prescan 函数体内不得存在 subprocess.run(...) 调用。

    使用 AST 静态分析，防止未来 revert 回同步实现。
    """
    # 定位 orchestrator.py 文件
    test_dir = os.path.dirname(os.path.abspath(__file__))
    orchestrator_path = os.path.normpath(
        os.path.join(
            test_dir, "..", "..", "app", "services", "agent", "agents", "orchestrator.py"
        )
    )

    with open(orchestrator_path, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    # 找到 _run_semgrep_prescan 函数定义
    prescan_func = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_run_semgrep_prescan":
                prescan_func = node
                break

    assert prescan_func is not None, "未找到 _run_semgrep_prescan 函数定义"

    # 遍历函数体内所有 Call 节点，检查是否存在 subprocess.run(...) 调用
    subprocess_run_calls = []
    for node in ast.walk(prescan_func):
        if isinstance(node, ast.Call):
            # 检查是否是 subprocess.run 调用
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                    and node.func.attr == "run"
                ):
                    subprocess_run_calls.append(
                        f"line {node.lineno}: subprocess.run(...)"
                    )

    assert len(subprocess_run_calls) == 0, (
        f"_run_semgrep_prescan 中检测到 {len(subprocess_run_calls)} 处 subprocess.run 调用（回归！）:\n"
        + "\n".join(subprocess_run_calls)
        + "\n请使用 asyncio.create_subprocess_exec 替代。"
    )