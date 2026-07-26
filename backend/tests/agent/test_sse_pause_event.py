"""SSE 暂停事件测试。

对应 spec delta audit-engine:
- Scenario: 暂停状态触发 SSE task_end 事件

验证：SSE 流终态判断逻辑把 paused 纳入 _SSE_TERMINAL_STATUSES，
使暂停后 SSE 流推送 task_end 并断开。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.endpoints import agent_tasks as module


def test_sse_terminal_statuses_includes_paused():
    """_SSE_TERMINAL_STATUSES 必须包含 paused，使暂停后 SSE 流断开。"""
    assert "paused" in module._SSE_TERMINAL_STATUSES
    assert "completed" in module._SSE_TERMINAL_STATUSES
    assert "failed" in module._SSE_TERMINAL_STATUSES
    assert "cancelled" in module._SSE_TERMINAL_STATUSES


def test_sse_terminal_statuses_are_strings():
    """所有终态必须是字符串（与 str(task_status) 比较）。"""
    for s in module._SSE_TERMINAL_STATUSES:
        assert isinstance(s, str)
