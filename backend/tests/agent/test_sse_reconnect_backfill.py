"""
SSE 重连 DB 回补分页测试

验证 stream_events 的 DB 回补路径支持游标分页，修复单次 limit=500 导致
超过 500 条事件永久丢失的 Bug。

参考 spec:
- Requirement: DB 回补支持游标分页
- Scenario: 断连期间 5000 条事件全部回补
- Scenario: 达到保护上限时截断
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services.agent.event_manager import EventManager


def _make_mock_db_events(count: int, task_id: str = "test-task"):
    """构造 mock DB 事件列表，模拟 AgentEvent.to_sse_dict() 返回格式。

    to_sse_dict() 返回 'type' 字段（非 'event_type'），
    stream_events 的 DB 回补段落会将其转换为 'event_type'。
    """
    events = []
    for i in range(1, count + 1):
        events.append({
            "id": f"evt-{task_id}-{i}",
            "type": "thinking",
            "phase": None,
            "message": f"Mock event #{i}",
            "tool_name": None,
            "tool_input": None,
            "tool_output": None,
            "tool_duration_ms": None,
            "finding_id": None,
            "tokens_used": 0,
            "metadata": None,
            "sequence": i,
            "timestamp": datetime(2026, 1, 1, 0, 0, i % 60, tzinfo=timezone.utc).isoformat(),
        })
    return events


def _make_mock_get_events(all_events: list):
    """返回一个 mock get_events 函数，模拟按 after_sequence + limit 分页返回。

    用于 patch EventManager.get_events，使 DB 回补循环能正确推进游标。
    """

    async def mock_get_events(task_id, after_sequence=0, limit=100):
        filtered = [e for e in all_events if e["sequence"] > after_sequence]
        return filtered[:limit]

    return mock_get_events


class TestSSEReconnectBackfill:
    """测试 SSE 重连时 DB 回补分页功能"""

    @pytest.mark.asyncio
    async def test_backfill_paginates_beyond_500(self):
        """构造 1500 条 DB 事件，断言 stream_events 全部回补且顺序正确。

        当前 Bug：单次 get_events(limit=500)，超过 500 条的事件永久丢失。
        RED 断言：预期 1500 条，实际仅 500 条（当前上限）。
        """
        task_id = "test-backfill-1500"
        all_db_events = _make_mock_db_events(1500, task_id)

        manager = EventManager()
        manager.db_session_factory = MagicMock()  # 使 DB 回补分支可达
        manager.create_queue(task_id)

        mock_get = _make_mock_get_events(all_db_events)

        with patch.object(manager, "get_events", side_effect=mock_get):
            queue = manager._event_queues[task_id]

            # 在排空阶段结束后，向队列放入终端事件以终止实时循环
            async def _put_terminal():
                await asyncio.sleep(0.3)
                await queue.put({
                    "event_type": "task_complete",
                    "sequence": 999999,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            asyncio.create_task(_put_terminal())

            # 收集 stream_events 产出的所有事件
            yielded = []
            async for evt in manager.stream_events(task_id, after_sequence=0):
                yielded.append(evt)

        # 过滤出 DB 回补的事件（sequence <= 1500）
        db_yielded = [e for e in yielded if e.get("sequence", 0) <= 1500]

        # RED 断言：当前实现仅回补 500 条，预期 1500 条
        assert len(db_yielded) == 1500, (
            f"Bug: 预期回补 1500 条事件，实际仅 {len(db_yielded)} 条。"
            f" 当前单次 limit=500 导致超过 500 条的事件丢失。"
        )

        # 断言按 sequence 递增顺序
        sequences = [e["sequence"] for e in db_yielded]
        assert sequences == list(range(1, 1501)), (
            f"回补事件顺序错误：期望 1-1500 递增，实际首 10 个：{sequences[:10]}"
        )

    @pytest.mark.asyncio
    async def test_backfill_respects_max_events_cap(self):
        """构造 25000 条 DB 事件，断言最多回补 20000 条且有截断通知。

        RED 断言：
        - 预期最多 20000 条 DB 事件
        - 预期包含 notice/backfill_truncated 事件
        - 当前实现仅回补 500 条，两条断言均失败
        """
        task_id = "test-backfill-cap"
        all_db_events = _make_mock_db_events(25000, task_id)

        manager = EventManager()
        manager.db_session_factory = MagicMock()
        manager.create_queue(task_id)

        mock_get = _make_mock_get_events(all_db_events)

        with patch.object(manager, "get_events", side_effect=mock_get):
            queue = manager._event_queues[task_id]

            async def _put_terminal():
                await asyncio.sleep(0.3)
                await queue.put({
                    "event_type": "task_complete",
                    "sequence": 25001,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            asyncio.create_task(_put_terminal())

            yielded = []
            async for evt in manager.stream_events(task_id, after_sequence=0):
                yielded.append(evt)

        # 过滤出 DB 回补的事件（sequence <= 25000 且非 notice；notice 是截断通知
        # 不计入 DB 事件计数）
        db_yielded = [
            e for e in yielded
            if e.get("sequence", 0) <= 25000
            and e.get("event_type") != "notice"
        ]

        # 找出 notice 事件
        notice_events = [
            e for e in yielded
            if e.get("event_type") == "notice"
            and isinstance(e.get("metadata"), dict)
            and e["metadata"].get("kind") == "backfill_truncated"
        ]

        # RED 断言：当前实现仅回补 500 条，远小于 20000 上限
        assert len(db_yielded) <= 20000, (
            f"Bug: 预期最多回补 20000 条，实际 {len(db_yielded)} 条。"
            f" 当前缺少上限保护。"
        )

        # RED 断言：当前实现不会发送 notice 事件
        assert len(notice_events) >= 1, (
            "Bug: 预期回补截断时发送 notice/backfill_truncated 事件，"
            " 当前实现未发送。"
        )

        # 额外验证：notice 事件包含 dropped 字段
        if notice_events:
            notice = notice_events[0]
            assert "dropped" in notice.get("metadata", {}), (
                "notice 事件 metadata 应包含 dropped 字段"
            )
            assert notice["metadata"]["dropped"] > 0, (
                "dropped 数量应大于 0"
            )