"""P2/P3 验证治理测试（2026-10-03）。"""
import pytest

from app.services.agent.agents.verification import (
    _count_verification_outcomes,
    _elastic_budget,
)


def _f(status):
    return {"verification_status": status}


class TestOutcomeCountingP2a:
    def test_counts_all_five_states(self):
        findings = (
            [_f("static_confirmed")] * 3
            + [_f("not_reproducible")] * 5
        )
        counts = _count_verification_outcomes(findings)
        assert counts["static_confirmed"] == 3
        assert counts["not_reproducible"] == 5
        assert counts["confirmed"] == 0

    def test_summary_line_contains_static_confirmed(self):
        findings = [_f("static_confirmed")] * 3 + [_f("not_reproducible")] * 5
        counts = _count_verification_outcomes(findings)
        line = (
            f"{counts['confirmed']} 确认, {counts['static_confirmed']} 静态确认, "
            f"{counts['false_positive']} 误报, {counts['not_reproducible']} 无法复现, "
            f"{counts['needs_context']} 需上下文"
        )
        assert "3 静态确认" in line


class TestElasticBudgetP3a:
    def test_small_batch_keeps_per_finding_8(self):
        assert _elastic_budget(3, 8) == min(8 * 3 + 20, 160)

    def test_large_batch_halves_per_finding(self):
        # 8 候选 > 5 → per_finding 降 4：4*8+20=52（原 8*8+20=84）
        assert _elastic_budget(8, 8) == 52

    def test_budget_capped_at_160(self):
        assert _elastic_budget(40, 8) == 160


class TestVerificationTemperatureP3b:
    def test_temperature_is_documented(self):
        # 集成断言在 stream 调用处（monkeypatch 捕获 kwargs）
        from app.services.agent.agents.verification import VERIFICATION_TEMPERATURE
        assert VERIFICATION_TEMPERATURE == 0.2
