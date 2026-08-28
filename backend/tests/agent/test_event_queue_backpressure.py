"""#1 修复回归测试：EventQueue 生产端零阻塞 + thinking_token 聚合降频。

E2E 实证（A 机 backend 日志）：1493 条重要事件各等满 5s 入队超时（≈124 分钟纯阻塞，
4 文件项目审计 50 分钟的直接原因）+ 324 条 thinking_token 丢弃警告（≈3.2 万条事件）。
根因：
1. 重要事件 ``await wait_for(q.put, 5s)`` 在 orchestrator 主协程同步等待；
2. thinking_token 每 token 一条事件、无聚合，唯一消费者（SSE 订阅者）缺席时队列必然满。

修复语义：
- 重要事件非阻塞入队（put_nowait），队列满跳过（事件已落 DB，重连时 DB 回补）；
- thinking_token 按时间窗/字符增量聚合，减少 90%+ 无用事件；
- 终态事件保持阻塞入队（30s 保护）。
"""
import asyncio
import time

from app.services.agent.event_manager import EventManager


def _fill_queue(mgr: EventManager, task_id: str) -> None:
    q = mgr.create_queue(task_id)
    for i in range(EventManager.QUEUE_MAX_SIZE):
        q.put_nowait({"sequence": i, "event_type": "thinking_token"})


class TestImportantEventNonBlocking:
    def test_important_event_does_not_block_when_queue_full(self):
        mgr = EventManager(db_session_factory=None)
        _fill_queue(mgr, "t1")

        async def run():
            t0 = time.monotonic()
            await mgr.add_event("t1", "tool_result", sequence=20000, message="x")
            return time.monotonic() - t0

        elapsed = asyncio.run(run())
        # 旧实现阻塞 5s；新实现必须瞬时返回
        assert elapsed < 1.0
        assert mgr.dropped_important_events.get("t1", 0) == 1

    def test_important_event_enqueued_when_space_available(self):
        mgr = EventManager(db_session_factory=None)
        mgr.create_queue("t2")

        async def run():
            await mgr.add_event("t2", "tool_result", sequence=1, message="ok")

        asyncio.run(run())
        assert mgr.dropped_important_events.get("t2", 0) == 0
        assert mgr._event_queues["t2"].qsize() == 1

    def test_terminal_event_still_enqueued(self):
        mgr = EventManager(db_session_factory=None)
        mgr.create_queue("t3")

        async def run():
            await mgr.add_event("t3", "task_complete", sequence=9, message="done")

        asyncio.run(run())
        assert mgr._event_queues["t3"].qsize() == 1


class TestThinkingTokenCoalescing:
    def test_tokens_coalesced_within_window(self):
        mgr = EventManager(db_session_factory=None)
        mgr.create_queue("t4")

        async def run():
            for i in range(50):
                await mgr.add_event(
                    "t4",
                    "thinking_token",
                    sequence=i,
                    metadata={"token": chr(97 + i % 26), "accumulated": "x" * i},
                )

        asyncio.run(run())
        # 50 条 token 在聚合窗口内 → 入队条数远小于 50
        assert mgr._event_queues["t4"].qsize() <= 5

    def test_token_after_window_emits(self):
        mgr = EventManager(db_session_factory=None)
        mgr.create_queue("t5")

        async def run():
            await mgr.add_event(
                "t5", "thinking_token", sequence=1,
                metadata={"token": "a", "accumulated": "a"},
            )
            await asyncio.sleep(0.2)  # 超过聚合窗口
            await mgr.add_event(
                "t5", "thinking_token", sequence=2,
                metadata={"token": "b", "accumulated": "ab"},
            )

        asyncio.run(run())
        assert mgr._event_queues["t5"].qsize() >= 2

    def test_large_accumulated_growth_forces_emit(self):
        mgr = EventManager(db_session_factory=None)
        mgr.create_queue("t6")

        async def run():
            # 单条 token 携带 80 字符增量 → 即使同一窗口也必须发射
            await mgr.add_event(
                "t6", "thinking_token", sequence=1,
                metadata={"token": "a", "accumulated": "a"},
            )
            await mgr.add_event(
                "t6", "thinking_token", sequence=2,
                metadata={"token": "b", "accumulated": "a" + "x" * 80},
            )

        asyncio.run(run())
        assert mgr._event_queues["t6"].qsize() >= 2

    def test_coalescing_state_per_task(self):
        mgr = EventManager(db_session_factory=None)
        mgr.create_queue("t7")
        mgr.create_queue("t8")

        async def run():
            await mgr.add_event(
                "t7", "thinking_token", sequence=1,
                metadata={"token": "a", "accumulated": "a"},
            )
            await mgr.add_event(
                "t8", "thinking_token", sequence=1,
                metadata={"token": "z", "accumulated": "z"},
            )

        asyncio.run(run())
        assert mgr._event_queues["t7"].qsize() == 1
        assert mgr._event_queues["t8"].qsize() == 1
