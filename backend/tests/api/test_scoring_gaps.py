"""评分语义修复（2026-09-29 层 5a）：审计失败状态不得打出满分。

背景：任务 c6d6cd09 / 327b6430 双机 0 finding + completed_with_gaps，
却显示 security_score=100 / quality_score=100——"审计没跑通"被呈现为
"项目非常安全"，直接误导放行决策。

契约：
- 无 findings + gaps=True → 二者均返回 None（"无法评分"）；
- 无 findings + gaps=False（正常完成且确实无发现）→ 维持 100；
- 有 findings → 算法不变（security 扣分制 / quality 三因子）。
"""
import pytest

from app.api.v1.endpoints.agent_tasks import (
    _calculate_quality_score,
    _calculate_security_score,
)


class TestScoringGapsSemantics:
    def test_security_no_findings_gaps_returns_none(self):
        assert _calculate_security_score([], gaps=True) is None

    def test_security_no_findings_normal_returns_100(self):
        assert _calculate_security_score([], gaps=False) == 100.0

    def test_security_with_findings_unchanged(self):
        score = _calculate_security_score([{"severity": "critical"}], gaps=True)
        assert score == 75.0

    def test_quality_no_findings_gaps_returns_none(self):
        assert _calculate_quality_score(
            [], verified_count=0, coverage_covered=0, coverage_total=10,
            gaps=True) is None

    def test_quality_no_findings_normal_returns_100(self):
        assert _calculate_quality_score(
            [], verified_count=0, coverage_covered=0, coverage_total=10,
            gaps=False) == 100.0


class TestScoringUsesSavedFindingsP6:
    """P6（2026-10-06）：评分输入必须用落库口径。

    生产实证（B 机任务 5f6487a4）：原始列表 17 项（13 项被幻觉过滤后落库
    4 项），评分用原始列表扣分 → 0 分（应 66 分）。quality 同被污染
    （42 分偏低）。评分输入必须与 _recalc_task_counters_from_db 同口径
    （落库后的 AgentFinding），而不是 orchestrator 返回的原始列表。
    """

    def test_security_score_saved口径_66分(self):
        from app.api.v1.endpoints.agent_tasks import _calculate_security_score

        saved = [{"severity": "high"}, {"severity": "medium"},
                 {"severity": "medium"}, {"severity": "low"}]
        assert _calculate_security_score(saved, gaps=True) == 66.0

    async def test_load_saved_findings_for_scoring(self):
        """新增 helper：从 DB 加载落库 findings（id/severity/verification_status/
        ai_confidence），供评分与原始列表解耦。"""
        from unittest.mock import AsyncMock, MagicMock
        from app.api.v1.endpoints.agent_tasks import _load_saved_findings_for_scoring

        row = MagicMock()
        row.severity = "high"
        row.verification_status = "confirmed"
        row.ai_confidence = 0.9
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[row])))
        out = await _load_saved_findings_for_scoring(db, "t-1")
        assert len(out) == 1
        assert out[0]["severity"] == "high"
        assert out[0]["verification_status"] == "confirmed"
        assert out[0]["ai_confidence"] == 0.9
