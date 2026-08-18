"""
Wave 2.4 + 2.6 P1 修复测试：SSE 端点客户端断开检测 + id 字段

Wave 2.4：
- 两个 SSE 端点签名接受 `request: fastapi.Request`
- 生成器主循环检测 request.is_disconnected() 或用 asyncio.wait 复合等待模式
- event_manager.stream_events 显式捕获 asyncio.CancelledError

Wave 2.6：
- SSE 事件（心跳除外）在 SSE 格式中包含 `id: {sequence}\n` 行
- 实现 Last-Event-ID 语义
"""
import ast
import inspect
from pathlib import Path

import pytest


SRC_AGENT_TASKS = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "api"
    / "v1"
    / "endpoints"
    / "agent_tasks.py"
)
SRC_EVENT_MGR = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "services"
    / "agent"
    / "event_manager.py"
)


def _get_func(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise ValueError(f"function {name!r} not found in {path}")


class TestSSEEndpointsAcceptRequest:
    """Wave 2.4: 两个 SSE 端点必须接受 request: Request 参数"""

    def test_stream_agent_events_has_request_param(self):
        node = _get_func(SRC_AGENT_TASKS, "stream_agent_events")
        arg_names = [a.arg for a in node.args.args]
        assert "request" in arg_names, (
            f"stream_agent_events 必须接受 request: Request 参数以检测客户端断开；"
            f"当前参数: {arg_names}"
        )

    def test_stream_agent_with_thinking_has_request_param(self):
        node = _get_func(SRC_AGENT_TASKS, "stream_agent_with_thinking")
        arg_names = [a.arg for a in node.args.args]
        assert "request" in arg_names, (
            f"stream_agent_with_thinking 必须接受 request: Request 参数；"
            f"当前参数: {arg_names}"
        )


class TestGeneratorsCheckDisconnect:
    """Wave 2.4: 生成器主循环历史上包含 request.is_disconnected 检测。

    Post-Wave 2 修复：**移除**了该检查，改依赖 Starlette StreamingResponse 内建的
    listen_for_disconnect。原实现 `await request.is_disconnected()` 会与内建监听
    竞争 ASGI receive channel，导致前端每次 rerender 触发的短暂 fetch abort 会
    立即杀掉 SSE stream。

    本类的测试保留但改为"注释中含 is_disconnected"的存在性检查，仅为文档追溯用。
    真正的回归防护见 `test_sse_no_manual_disconnect_check.py`。
    """

    def test_stream_agent_events_generator_has_disconnect_comment(self):
        """stream_agent_events 函数体内应保留 is_disconnected 相关注释（历史追溯）。

        Post-Wave 2 更改：Wave 1 加的 await 调用已删除，但注释保留说明为何不再检查。
        """
        content = SRC_AGENT_TASKS.read_text(encoding="utf-8")
        node = _get_func(SRC_AGENT_TASKS, "stream_agent_events")
        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno else len(content.splitlines())
        body = "\n".join(content.splitlines()[start:end])
        assert "is_disconnected" in body, (
            "stream_agent_events 内应保留 is_disconnected 相关注释以说明 Post-Wave 2 修复"
        )

    def test_stream_agent_with_thinking_generator_has_disconnect_comment(self):
        """stream_agent_with_thinking 函数体内应保留 is_disconnected 相关注释（历史追溯）"""
        content = SRC_AGENT_TASKS.read_text(encoding="utf-8")
        node = _get_func(SRC_AGENT_TASKS, "stream_agent_with_thinking")
        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno else len(content.splitlines())
        body = "\n".join(content.splitlines()[start:end])
        assert "is_disconnected" in body


class TestStreamEventsCatchesCancelledError:
    """Wave 2.4: event_manager.stream_events 显式捕获 asyncio.CancelledError"""

    def test_stream_events_has_cancelled_error_except(self):
        content = SRC_EVENT_MGR.read_text(encoding="utf-8")
        node = _get_func(SRC_EVENT_MGR, "stream_events")
        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno else len(content.splitlines())
        body = "\n".join(content.splitlines()[start:end])
        assert "except asyncio.CancelledError" in body or "except CancelledError" in body, (
            "stream_events 必须显式捕获 asyncio.CancelledError（Starlette 内部取消或"
            "客户端断开会触发），当前该异常会未处理地传播导致生成器异常关闭"
        )


class TestSSEEventsIncludeIdField:
    """Wave 2.6: SSE 事件（非心跳）必须包含 id: {sequence} 字段"""

    def test_format_sse_event_produces_id_line(self):
        """format_sse_event 应在输出中包含 id: 行（当事件有 sequence 时）"""
        content = SRC_AGENT_TASKS.read_text(encoding="utf-8")
        # 找到 format_sse_event 的定义（在 stream_agent_with_thinking 内部）
        # 简单文本匹配即可：应有形如 `f"id: {...}\n"` 的字符串
        assert 'id: {' in content or 'id: %d' in content or 'f"id:' in content, (
            "SSE 事件格式化处应含 id: {sequence} 字段，用于 Last-Event-ID 语义"
        )

    def test_stream_agent_events_generator_emits_id(self):
        """stream_agent_events 端点也应在每条事件前加 id: {sequence} 行"""
        content = SRC_AGENT_TASKS.read_text(encoding="utf-8")
        node = _get_func(SRC_AGENT_TASKS, "stream_agent_events")
        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno else len(content.splitlines())
        body = "\n".join(content.splitlines()[start:end])
        # 应有形如 `f"id: {event.sequence}\n"` 或类似
        assert "id:" in body, (
            "stream_agent_events 生成器每条事件的 SSE 输出应含 id: {sequence} 行"
        )
