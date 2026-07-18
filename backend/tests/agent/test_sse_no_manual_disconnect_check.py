"""
Post-Wave 2 关键回归测试：SSE stream 稳定性

根因（Wave 1 §2.4 引入）：`agent_tasks.py` 两个 SSE endpoint 里 `async for`
循环内每次事件后 `await request.is_disconnected()`。
- 该方法内部 `await self._receive()`，会消费 ASGI receive channel
- 与 Starlette StreamingResponse 内建 listen_for_disconnect 竞争
- 前端每次 rerender 触发的短暂 fetch abort 会立即杀掉 SSE stream

修复（Post-Wave 2）：删除三处 is_disconnected 手动检查。Starlette 内建的
listen_for_disconnect 已自动检测客户端断开并 cancel body_iterator。
stream_events 仍捕获 CancelledError 兜底。

参考：openspec/changes/fix-sse-realtime-stream/design.md D6（修订后）
"""
from pathlib import Path

SRC = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "api"
    / "v1"
    / "endpoints"
    / "agent_tasks.py"
)


class TestNoIsDisconnectedCalls:
    """回归防护：SSE endpoint 内 async for 循环中不得再次引入 request.is_disconnected() 调用"""

    def test_no_is_disconnected_call_in_source(self):
        """agent_tasks.py 源码中不得再有 await request.is_disconnected() 调用。

        允许留下变量名 / 注释中的字符串（`Wave 1 §2.4: 用于 request.is_disconnected()`
        这种注释可以保留作为历史）。
        只禁止**实际调用**这个方法。
        """
        content = SRC.read_text(encoding="utf-8")
        offending_lines = []
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # 跳过注释行
            if stripped.startswith("#"):
                continue
            # 检查实际调用（await request.is_disconnected()）
            if "await request.is_disconnected(" in line:
                offending_lines.append((i, stripped))
        assert not offending_lines, (
            "agent_tasks.py 仍含 `await request.is_disconnected(...)` 调用。这个调用会消费 "
            "ASGI receive channel 与 Starlette StreamingResponse 内建 listen_for_disconnect "
            "竞争，导致前端每次 rerender 触发的短暂 fetch abort 会立即杀掉 SSE stream。\n"
            + "\n".join(f"  L{ln}: {code}" for ln, code in offending_lines)
        )

    def test_sse_endpoints_still_accept_request_param(self):
        """两个 SSE endpoint 应继续接受 request: Request 参数（保留供 Starlette 内建监听使用）"""
        import ast

        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        endpoints_seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in {
                "stream_agent_events",
                "stream_agent_with_thinking",
            }:
                endpoints_seen.add(node.name)
                arg_names = [a.arg for a in node.args.args]
                assert "request" in arg_names, (
                    f"{node.name} 必须继续接受 request 参数，Starlette 内建的 "
                    f"listen_for_disconnect 通过它检测客户端断开。当前参数: {arg_names}"
                )
        assert {"stream_agent_events", "stream_agent_with_thinking"}.issubset(
            endpoints_seen
        ), f"SSE endpoint 缺失: {endpoints_seen}"
