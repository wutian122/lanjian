"""
Wave 2.2 P1 修复测试：任务超时路径使用正确的 emitter 方法

根因：`agent_tasks.py:736` 使用 `event_emitter.emit_event('warning', ...)`，但
      `AgentEventEmitter` 类（event_manager.py:46）没有 `emit_event` 方法。
      调用会抛 AttributeError，被外层 except Exception 捕获，任务被误判为 FAILED
      且不发终态事件。
修复：改为 `event_emitter.emit_warning(...)`。

参考：openspec/changes/fix-sse-realtime-stream/specs/audit-engine/spec.md
      "Requirement: 超时保护路径使用正确的 emitter 方法"
"""
import inspect

import pytest

from app.services.agent.event_manager import AgentEventEmitter


class TestEmitterHasNoEmitEventMethod:
    """静态契约：AgentEventEmitter 没有 `emit_event` 方法，任何调用它的路径都是 bug"""

    def test_emit_event_method_does_not_exist(self):
        """AgentEventEmitter 类无 emit_event 方法（应用 emit_warning/emit_info/...）"""
        assert not hasattr(AgentEventEmitter, "emit_event"), (
            "AgentEventEmitter 不应有 emit_event 方法；请使用 emit_warning / emit_info / emit_error 等"
        )

    def test_emit_warning_method_exists_and_is_async(self):
        """emit_warning 存在且是 async 方法"""
        assert hasattr(AgentEventEmitter, "emit_warning")
        assert inspect.iscoroutinefunction(AgentEventEmitter.emit_warning)


class TestNoStaleEmitEventUsage:
    """回归防护：agent_tasks.py 中不得再有 event_emitter.emit_event(...) 调用"""

    def test_agent_tasks_has_no_emit_event_call(self):
        """agent_tasks.py 源码中不得含 `event_emitter.emit_event(` 调用"""
        from pathlib import Path

        src = Path(
            __file__
        ).resolve().parents[2] / "app" / "api" / "v1" / "endpoints" / "agent_tasks.py"
        content = src.read_text(encoding="utf-8")
        # 找所有 event_emitter.emit_event(...) 调用（不包括注释）
        offending_lines = []
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "event_emitter.emit_event(" in line:
                offending_lines.append((i, stripped))
        assert not offending_lines, (
            f"agent_tasks.py 仍含 event_emitter.emit_event(...) 调用（应为 emit_warning 等）:\n"
            + "\n".join(f"  L{ln}: {code}" for ln, code in offending_lines)
        )
