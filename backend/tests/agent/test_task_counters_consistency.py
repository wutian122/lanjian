"""
D1/D2/D3 修复测试：任务统计计数器与 findings 表保持一致

根因：
- D1: agent_tasks.py 完成回调中 critical/high/medium/low_count 用 += 累加遍历
      含幻觉 finding 的原始列表，无归零、无 DB 一致性修正
- D2: files_with_findings 同样遍历含幻觉的原始 findings 列表
- D3: not_reproducible 被错误计入 is_verified（verification_method+confidence>=0.85 覆盖了语义）

修复：提取 _recalc_task_counters_from_db，从已落库 AgentFinding 重查计数器。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints.agent_tasks import _recalc_task_counters_from_db


def _mock_scalar(value):
    """构造 db.execute(...).scalar() 的返回链"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _mock_all(rows):
    """构造 db.execute(...).all() 的返回链"""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _make_task():
    """构造一个带脏计数器的 task 对象"""
    task = MagicMock()
    task.critical_count = 99  # 故意设脏值，验证会被重置
    task.high_count = 99
    task.medium_count = 99
    task.low_count = 99
    task.files_with_findings = 99
    task.verified_count = 99
    return task


class TestRecalcCountersFromDB:
    """验证计数器从 DB 重查的一致性"""

    @pytest.mark.asyncio
    async def test_severity_counts_match_db(self):
        """D1: 严重度计数器等于 DB GROUP BY severity 结果，且脏值被清零"""
        task = _make_task()
        db = AsyncMock()
        # 第一次 execute: severity GROUP BY
        db.execute.side_effect = [
            _mock_all([("critical", 1), ("high", 2), ("medium", 3), ("low", 1)]),
            _mock_scalar(5),  # files_with_findings distinct
            _mock_scalar(2),  # verified_count
            _mock_scalar(0),  # static_confirmed_count
        ]
        await _recalc_task_counters_from_db(db, task, "task-1")
        assert task.critical_count == 1
        assert task.high_count == 2
        assert task.medium_count == 3
        assert task.low_count == 1

    @pytest.mark.asyncio
    async def test_severity_counts_zero_when_no_findings(self):
        """D1: 无 finding 时所有严重度计数为 0（脏值清零）"""
        task = _make_task()
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_all([]),
            _mock_scalar(0),
            _mock_scalar(0),
            _mock_scalar(0),  # static_confirmed_count
        ]
        await _recalc_task_counters_from_db(db, task, "task-1")
        assert task.critical_count == 0
        assert task.high_count == 0
        assert task.medium_count == 0
        assert task.low_count == 0

    @pytest.mark.asyncio
    async def test_files_with_findings_distinct(self):
        """D2: files_with_findings 等于去重文件数"""
        task = _make_task()
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_all([("low", 1)]),
            _mock_scalar(3),  # 3 个去重文件
            _mock_scalar(1),
            _mock_scalar(0),  # static_confirmed_count
        ]
        await _recalc_task_counters_from_db(db, task, "task-1")
        assert task.files_with_findings == 3

    @pytest.mark.asyncio
    async def test_not_reproducible_excluded_from_verified(self):
        """D3: verification_status=not_reproducible 不计入 verified_count

        修复后 verified_count 仅统计 verification_status in
        (confirmed/verified/true_positive) AND is_verified=True。
        """
        task = _make_task()
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_all([("medium", 1)]),
            _mock_scalar(1),
            _mock_scalar(0),  # DB 层已排除 not_reproducible
            _mock_scalar(0),  # static_confirmed_count
        ]
        await _recalc_task_counters_from_db(db, task, "task-1")
        assert task.verified_count == 0

    @pytest.mark.asyncio
    async def test_verified_count_only_confirmed_statuses(self):
        """D3: 仅 confirmed/verified/true_positive 且 is_verified=True 计入"""
        task = _make_task()
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_all([("high", 2)]),
            _mock_scalar(2),
            _mock_scalar(2),
            _mock_scalar(0),  # static_confirmed_count
        ]
        await _recalc_task_counters_from_db(db, task, "task-1")
        assert task.verified_count == 2
