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

    def test_exhaustive_status_partition(self):
        """穷举划分：遍历 AgentTaskStatus 所有常量，逐一确认终态/非终态归属。

        若未来在 AgentTaskStatus 新增常量，本测试必须失败（强制维护者显式
        决定新状态是否属于终态），防止 _SSE_TERMINAL_STATUSES 遗漏。
        """
        from app.api.v1.endpoints.agent_tasks import _SSE_TERMINAL_STATUSES
        from app.models.agent_task import AgentTaskStatus

        # 期望的完整分区，覆盖 AgentTaskStatus 所有当前常量
        expected_terminal = {
            "completed",
            "completed_with_gaps",
            "failed",
            "cancelled",
            "paused",
        }
        expected_non_terminal = {
            "pending",
            "initializing",
            "running",
            "planning",
            "indexing",
            "analyzing",
            "verifying",
            "reporting",
        }
        expected_all = expected_terminal | expected_non_terminal

        # 通过反射收集 AgentTaskStatus 全部 str 常量
        actual_all = {
            v for k, v in vars(AgentTaskStatus).items()
            if not k.startswith("_") and isinstance(v, str)
        }

        # 若新增了常量，这里会失败并给出提示
        missing = actual_all - expected_all
        extra = expected_all - actual_all
        assert not missing, (
            f"AgentTaskStatus 新增了未在测试预期分区中的常量: {sorted(missing)}。"
            f"请显式决定这些新状态是否属于终态并同步更新 _SSE_TERMINAL_STATUSES 与本测试。"
        )
        assert not extra, (
            f"测试预期的常量在 AgentTaskStatus 中不存在（可能已被删除）: {sorted(extra)}"
        )

        # 逐一断言分区正确性
        for status in expected_terminal:
            assert status in _SSE_TERMINAL_STATUSES, (
                f"终态 {status!r} 缺失于 _SSE_TERMINAL_STATUSES"
            )
        for status in expected_non_terminal:
            assert status not in _SSE_TERMINAL_STATUSES, (
                f"非终态 {status!r} 错误地包含在 _SSE_TERMINAL_STATUSES 中"
            )
