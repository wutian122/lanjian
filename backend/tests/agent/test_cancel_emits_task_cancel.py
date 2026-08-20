"""
Wave 2.3 P1 修复测试：取消路径发出终态事件

根因：`cancel_agent_task` 端点和 `_execute_agent_task` 的 CancelledError 分支
      只更新 DB status = CANCELLED，不发出 SSE 事件。stream_events 的实时循环
      在等待 task_cancel 终态事件，永远收不到 → 前端要等 15 秒心跳超时才感知取消。
修复：两处都在更新 status 后调用 event_emitter.emit_task_cancelled(...)。

参考：openspec/changes/fix-sse-realtime-stream/specs/audit-engine/spec.md
      "Requirement: 任务取消路径发出终态事件"
"""
import inspect
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints" / "agent_tasks.py"


def _extract_function_body(source: str, func_name: str) -> str:
    """从 python 源码中提取指定函数的完整函数体（含 def 行）"""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            start = node.lineno - 1
            end = node.end_lineno if node.end_lineno else len(source.splitlines())
            return "\n".join(source.splitlines()[start:end])
    raise ValueError(f"function {func_name!r} not found")


class TestCancelPathEmitsTaskCancel:
    """契约：取消路径必须调用 emit_task_cancelled"""

    def test_cancel_endpoint_calls_emit_task_cancelled(self):
        """cancel_agent_task 端点的函数体应含 emit_task_cancelled 调用"""
        content = SRC.read_text(encoding="utf-8")
        body = _extract_function_body(content, "cancel_agent_task")
        assert "emit_task_cancelled" in body, (
            "cancel_agent_task 端点应调用 event_emitter.emit_task_cancelled 通知 SSE 流"
        )

    def test_execute_agent_task_cancelled_error_branch_emits(self):
        """_execute_agent_task 的主任务 CancelledError 分支应含 emit_task_cancelled 调用"""
        content = SRC.read_text(encoding="utf-8")
        body = _extract_function_body(content, "_execute_agent_task")

        # 函数内存在多个 `except asyncio.CancelledError`（嵌套的早心跳辅助函数也有），
        # 需定位"某个"在后续窗口内调用 emit_task_cancelled 的分支（真实主循环分支在 1044 行附近）。
        lines = body.splitlines()
        found = False
        for i, line in enumerate(lines):
            if "except asyncio.CancelledError" in line:
                window = "\n".join(lines[i:i + 40])
                if "emit_task_cancelled" in window:
                    found = True
                    break
        assert found, "_execute_agent_task 的 CancelledError 分支应调用 emit_task_cancelled"


class TestEmitTaskCancelledMethodExists:
    """契约：AgentEventEmitter.emit_task_cancelled 是可用的 async 方法"""

    def test_method_present(self):
        from app.services.agent.event_manager import AgentEventEmitter

        assert hasattr(AgentEventEmitter, "emit_task_cancelled")
        assert inspect.iscoroutinefunction(AgentEventEmitter.emit_task_cancelled)
