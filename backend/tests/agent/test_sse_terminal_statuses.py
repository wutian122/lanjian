"""
Wave 0.3 P0 修复测试：_SSE_TERMINAL_STATUSES 补齐 completed_with_gaps

根因：`_SSE_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "paused"}` 缺少
      "completed_with_gaps"。DB 轮询流在观察到 COMPLETED_WITH_GAPS 时不发 task_end，
      前端一直显示"运行中"直到 300s max_idle 才断开。
修复：加入 "completed_with_gaps"；确认 "initializing" 不在集合。

参考：openspec/changes/fix-sse-realtime-stream/specs/sse-realtime-stream/spec.md
      "Requirement: SSE 终态状态集合完整"
"""
import pytest


class TestSSETerminalStatuses:
    """SSE 终态状态集合完整性契约"""

    def test_completed_with_gaps_in_terminal_set(self):
        """completed_with_gaps 必须在终态集合中，否则 DB 轮询流无法识别为已结束"""
        from app.api.v1.endpoints.agent_tasks import _SSE_TERMINAL_STATUSES

        assert "completed_with_gaps" in _SSE_TERMINAL_STATUSES, (
            "completed_with_gaps 必须在 _SSE_TERMINAL_STATUSES 中，"
            "否则覆盖率不足/超时任务完成后前端不会正常关闭 SSE 流"
        )

    def test_all_expected_terminal_statuses_present(self):
        """完整的终态集合：completed / completed_with_gaps / failed / cancelled / paused"""
        from app.api.v1.endpoints.agent_tasks import _SSE_TERMINAL_STATUSES

        expected = {"completed", "completed_with_gaps", "failed", "cancelled", "paused"}
        assert expected.issubset(_SSE_TERMINAL_STATUSES), (
            f"缺失终态: {expected - _SSE_TERMINAL_STATUSES}"
        )

    def test_initializing_not_in_terminal_set(self):
        """initializing 明确不在终态集合（属于运行中，SSE 应保持连接）"""
        from app.api.v1.endpoints.agent_tasks import _SSE_TERMINAL_STATUSES

        assert "initializing" not in _SSE_TERMINAL_STATUSES, (
            "initializing 属于运行中状态，不得触发 SSE 流关闭"
        )

    def test_pending_running_not_in_terminal_set(self):
        """pending 和 running 也不在终态集合"""
        from app.api.v1.endpoints.agent_tasks import _SSE_TERMINAL_STATUSES

        assert "pending" not in _SSE_TERMINAL_STATUSES
        assert "running" not in _SSE_TERMINAL_STATUSES

    def test_terminal_statuses_match_enum_completed_with_gaps(self):
        """字符串与 AgentTaskStatus 常量对齐（AgentTaskStatus 是常量 class 非 Enum）"""
        from app.api.v1.endpoints.agent_tasks import _SSE_TERMINAL_STATUSES
        from app.models.agent_task import AgentTaskStatus

        assert AgentTaskStatus.COMPLETED_WITH_GAPS in _SSE_TERMINAL_STATUSES
        assert AgentTaskStatus.COMPLETED in _SSE_TERMINAL_STATUSES
        assert AgentTaskStatus.FAILED in _SSE_TERMINAL_STATUSES
        assert AgentTaskStatus.CANCELLED in _SSE_TERMINAL_STATUSES
        assert AgentTaskStatus.PAUSED in _SSE_TERMINAL_STATUSES
