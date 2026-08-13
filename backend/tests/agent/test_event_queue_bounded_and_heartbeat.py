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


# ============ Runtime integration tests (Wave 2 Review Finding 4) ============

import asyncio
import pytest


class TestGradedDropRuntime:
    """运行时验证 add_event 分级丢弃语义（Wave 2 Review Finding 4）"""

    @pytest.mark.asyncio
    async def test_thinking_token_dropped_when_full(self):
        """队列满时 thinking_token 被丢弃且计数递增，add_event 不抛异常"""
        from app.services.agent.event_manager import EventManager

        # 小容量队列便于测试
        mgr = EventManager(db_session_factory=None)
        mgr.QUEUE_MAX_SIZE = 3  # override class attr on instance
        # 创建队列时使用 override 值
        task_id = "t_drop"
        # 直接构造 3 容量队列，覆盖 create_queue 的默认
        mgr._event_queues[task_id] = asyncio.Queue(maxsize=3)
        # 填满队列
        q = mgr._event_queues[task_id]
        for _ in range(3):
            q.put_nowait({"filler": True})
        assert q.full()

        # 再发 5 个 thinking_token：应被全部丢弃，不阻塞不抛错
        for i in range(5):
            await asyncio.wait_for(
                mgr.add_event(task_id, "thinking_token", sequence=i),
                timeout=1.0,
            )

        # 5 个都被丢弃
        assert mgr.dropped_thinking_tokens.get(task_id, 0) == 5, (
            f"预期丢弃 5 个 thinking_token，实际 {mgr.dropped_thinking_tokens.get(task_id, 0)}"
        )
        # 队列大小未变（仍是 3）
        assert q.qsize() == 3

    @pytest.mark.asyncio
    async def test_important_event_gives_up_after_timeout(self):
        """重要事件（非终态非丢弃）在队列满时等待后放弃"""
        from app.services.agent.event_manager import EventManager

        mgr = EventManager(db_session_factory=None)
        task_id = "t_imp"
        mgr._event_queues[task_id] = asyncio.Queue(maxsize=2)
        q = mgr._event_queues[task_id]
        for _ in range(2):
            q.put_nowait({"filler": True})
        assert q.full()

        # 缩短超时便于测试
        mgr.IMPORTANT_PUT_TIMEOUT_SECONDS = 0.5

        start = asyncio.get_event_loop().time()
        await mgr.add_event(task_id, "tool_call", sequence=1)
        elapsed = asyncio.get_event_loop().time() - start

        # 大约等待 0.5 秒后放弃（允许 0.4-1.0 秒容差）
        assert 0.4 < elapsed < 1.5, f"预期 ~0.5s 后放弃，实际 {elapsed:.2f}s"
        # 队列未变
        assert q.qsize() == 2

    @pytest.mark.asyncio
    async def test_terminal_event_times_out_and_survives(self):
        """终态事件（Finding 1 修复验证）：消费者永久离线时终态事件在超时后放弃，
        Orchestrator 不会因此挂死。事件已落 DB（DB session factory 时）。"""
        from app.services.agent.event_manager import EventManager

        mgr = EventManager(db_session_factory=None)
        task_id = "t_term"
        mgr._event_queues[task_id] = asyncio.Queue(maxsize=1)
        q = mgr._event_queues[task_id]
        q.put_nowait({"filler": True})
        assert q.full()

        # 缩短终态超时便于测试
        mgr.TERMINAL_PUT_TIMEOUT_SECONDS = 0.5

        start = asyncio.get_event_loop().time()
        await mgr.add_event(task_id, "task_complete", sequence=1)
        elapsed = asyncio.get_event_loop().time() - start

        # 大约等待 0.5 秒后放弃（Wave 2 Review Finding 1 关键：不会永久挂死）
        assert 0.4 < elapsed < 1.5, (
            f"预期终态事件超时 ~0.5s 后放弃，实际 {elapsed:.2f}s；"
            "若 >1.5s 说明 Orchestrator 可能挂死"
        )

    @pytest.mark.asyncio
    async def test_terminal_event_enqueues_when_space_available(self):
        """终态事件在队列有空间时正常入队（正常路径）"""
        from app.services.agent.event_manager import EventManager

        mgr = EventManager(db_session_factory=None)
        task_id = "t_term_ok"
        mgr._event_queues[task_id] = asyncio.Queue(maxsize=5)
        q = mgr._event_queues[task_id]

        await mgr.add_event(task_id, "task_complete", sequence=1)
        assert q.qsize() == 1
        got = q.get_nowait()
        assert got["event_type"] == "task_complete"
