"""
Q1 修复测试：任务响应包含 verification_status_breakdown

根因：前端只展示 verified_count（旧口径仅 confirmed），不展示
      static_confirmed/not_reproducible/needs_context/false_positive 分布，
      用户误以为只验证了 2 个。
修复：新增 _get_verification_status_breakdown 聚合 5 类计数，response 输出。

P10-T4（2026-10-09）：breakdown 为详情接口实时聚合，历史任务徽标随新口径
回溯生效；合计（含静态）= confirmed + static_confirmed。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints.agent_tasks import _get_verification_status_breakdown


def _mock_all(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


class TestVerificationStatusBreakdown:
    """验证 verification_status_breakdown 聚合"""

    @pytest.mark.asyncio
    async def test_verification_status_breakdown(self):
        """Q1: breakdown 四字段正确聚合"""
        db = AsyncMock()
        db.execute.return_value = _mock_all([
            ("confirmed", 2),
            ("not_reproducible", 9),
            ("needs_context", 3),
            ("false_positive", 1),
        ])
        breakdown = await _get_verification_status_breakdown(db, "task-1")
        assert breakdown == {
            "confirmed": 2,
            "static_confirmed": 0,
            "not_reproducible": 9,
            "needs_context": 3,
            "false_positive": 1,
        }

    @pytest.mark.asyncio
    async def test_breakdown_zero_for_missing_statuses(self):
        """Q1: 未出现的 status 计数为 0"""
        db = AsyncMock()
        db.execute.return_value = _mock_all([("confirmed", 1)])
        breakdown = await _get_verification_status_breakdown(db, "task-1")
        assert breakdown["confirmed"] == 1
        assert breakdown["static_confirmed"] == 0
        assert breakdown["not_reproducible"] == 0
        assert breakdown["needs_context"] == 0
        assert breakdown["false_positive"] == 0

    @pytest.mark.asyncio
    async def test_breakdown_empty_when_no_findings(self):
        """Q1: 无 finding 时字段全 0"""
        db = AsyncMock()
        db.execute.return_value = _mock_all([])
        breakdown = await _get_verification_status_breakdown(db, "task-1")
        assert breakdown == {
            "confirmed": 0,
            "static_confirmed": 0,
            "not_reproducible": 0,
            "needs_context": 0,
            "false_positive": 0,
        }

    @pytest.mark.asyncio
    async def test_breakdown_ignores_unknown_status(self):
        """Q1: 未知 status 不计入（防御）"""
        db = AsyncMock()
        db.execute.return_value = _mock_all([
            ("confirmed", 2),
            ("unknown_status", 5),  # 未知
        ])
        breakdown = await _get_verification_status_breakdown(db, "task-1")
        assert breakdown["confirmed"] == 2
        # 未知 status 不破坏结构
        assert "unknown_status" not in breakdown
        assert breakdown["not_reproducible"] == 0

    @pytest.mark.asyncio
    async def test_breakdown_total_includes_static_confirmed(self):
        """P10-T4：合计（含静态）= confirmed + static_confirmed，
        前端据此渲染「动态确认 N · 合计 M（含静态）」双标。"""
        from app.models.agent_task import VERIFIED_COUNT_STATUSES

        db = AsyncMock()
        db.execute.return_value = _mock_all([
            ("confirmed", 2),
            ("static_confirmed", 3),
            ("not_reproducible", 1),
        ])
        breakdown = await _get_verification_status_breakdown(db, "task-1")
        total = sum(breakdown[s] for s in VERIFIED_COUNT_STATUSES)
        assert total == 5
        assert breakdown["confirmed"] == 2  # 动态确认单独可读
        assert breakdown["static_confirmed"] == 3
