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
