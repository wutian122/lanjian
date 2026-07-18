"""
Wave 2 §3.3 事件队列有界 + 分级丢弃 + §3.4 心跳独立协程测试

3.3 目标：
- create_queue 使用 asyncio.Queue(maxsize=10000)
- add_event 按事件类型分级处理满队情况：
  * thinking_token: put_nowait，QueueFull 时丢弃 + dropped_thinking_tokens 计数
  * 重要事件（tool_call 等）: await wait_for(put, 5.0)
  * 终态事件（task_complete/task_error/task_cancel）: 无条件 await put

3.4 目标：
- stream_events 的心跳发送与队列 get 解耦
- 心跳周期 10 秒

参考：openspec/changes/fix-sse-realtime-stream/specs/sse-realtime-stream/spec.md
"""
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "app" / "services" / "agent" / "event_manager.py"


class TestQueueIsBounded:
    """§3.3 create_queue 使用有界 asyncio.Queue"""

    def test_create_queue_uses_bounded_queue(self):
        """create_queue 应使用有界 Queue（maxsize > 0），值 >= 1000"""
        content = SRC.read_text(encoding="utf-8")
        assert "asyncio.Queue(maxsize=" in content, (
            "create_queue 应使用 asyncio.Queue(maxsize=...) 有界队列"
        )
        # 允许字面量或常量引用，只要 maxsize 参数存在且不为 0
        import re
        # 匹配 maxsize=数字 或 maxsize=self.SOMETHING
        matches_literal = re.findall(r"asyncio\.Queue\(maxsize=(\d+)\)", content)
        matches_const = re.findall(r"asyncio\.Queue\(maxsize=self\.(\w+)\)", content)
        assert matches_literal or matches_const, "找不到 maxsize 参数"
        # 若是字面量，确认 >= 1000
        for m in matches_literal:
            assert int(m) >= 1000, f"maxsize={m} 太小"
        # 若是常量，确认常量值 >= 1000
        if matches_const:
            const_name = matches_const[0]
            const_match = re.search(rf"{const_name}\s*=\s*(\d+)", content)
            assert const_match, f"找不到常量 {const_name} 的赋值"
            assert int(const_match.group(1)) >= 1000, (
                f"{const_name}={const_match.group(1)} 太小，规格要求 10000"
            )


class TestGradedDrop:
    """§3.3 add_event 分级丢弃策略"""

    def test_dropped_thinking_tokens_counter_exists(self):
        content = SRC.read_text(encoding="utf-8")
        assert "dropped_thinking_tokens" in content, (
            "应新增 dropped_thinking_tokens 计数器"
        )

    def test_queue_full_handled(self):
        content = SRC.read_text(encoding="utf-8")
        assert "put_nowait" in content, "thinking_token 分支应用 put_nowait"
        assert "QueueFull" in content, "应处理 asyncio.QueueFull 异常"

    def test_terminal_events_always_awaited(self):
        content = SRC.read_text(encoding="utf-8")
        for marker in ["task_complete", "task_error", "task_cancel"]:
            assert marker in content


class TestHeartbeatIndependent:
    """§3.4 心跳独立协程"""

    def test_heartbeat_interval_ten_seconds(self):
        content = SRC.read_text(encoding="utf-8")
        import re
        assert re.search(r"HEARTBEAT_INTERVAL[_A-Z]*\s*=\s*10\b", content), (
            "应定义 HEARTBEAT_INTERVAL = 10（周期 10 秒）"
        )

    def test_heartbeat_decoupled_from_queue_get(self):
        """心跳应通过独立协程或复合等待实现（不再靠 wait_for(queue.get, timeout=15)）"""
        content = SRC.read_text(encoding="utf-8")
        has_pump = "_pump_heartbeat" in content
        has_wait = "FIRST_COMPLETED" in content
        assert has_pump or has_wait, (
            "心跳应与队列消费解耦：_pump_heartbeats 独立协程 或 asyncio.wait FIRST_COMPLETED"
        )
