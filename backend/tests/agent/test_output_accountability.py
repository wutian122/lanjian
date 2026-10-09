"""P1/P2/P3 根治测试（2026-10-03，双机 tomcat 对照实证四根因）。

根因 A：Flash-Next 关思考下 Analysis"只干活不交卷"——30 轮动作正常但
submit_findings 0 调用、强制总结 0 候选 0 豁免（B 机 eec77e54 / A 机 d865def0
双机一致）→ 全靠 Semgrep 兜底，"8 个漏洞"全是静态候选。
根因 B：验证收口统计漏计 static_confirmed（"0 确认"实为 0 动态+3 静态）。
根因 C：验证判定机间波动（同批候选 A 机 1 静态确认 / B 机 3 个）。
根因 D：llm_health 口径漏 llm_decision 类空响应（B 机 Verification 6 次）。
根因 E：验证成本失控（B 机 8 候选耗 34 万 token，13 轮继续验证）。
"""
import pytest

from app.services.agent.agents.analysis import (
    _FORCED_SUMMARY_PROMPT,
    _should_nudge_submit,
)


class TestSubmitNudgeP1a:
    def test_first_nudge_at_half_way(self):
        # 1-based：30 轮过半后的第一轮是第 16 轮
        assert _should_nudge_submit(16, 30, has_candidates=False) is True

    def test_repeat_every_five_rounds_after_half(self):
        assert _should_nudge_submit(20, 30, has_candidates=False) is True
        assert _should_nudge_submit(25, 30, has_candidates=False) is True

    def test_off_node_not_nudged(self):
        assert _should_nudge_submit(17, 30, has_candidates=False) is False

    def test_before_half_not_nudged(self):
        assert _should_nudge_submit(10, 30, has_candidates=False) is False

    def test_with_candidates_not_nudged(self):
        assert _should_nudge_submit(16, 30, has_candidates=True) is False

    def test_final_rounds_still_nudged(self):
        assert _should_nudge_submit(28, 30, has_candidates=False) is True


class TestForcedSummaryPromptP1b:
    def test_prompt_demands_at_least_one_candidate(self):
        assert "至少 1 条" in _FORCED_SUMMARY_PROMPT

    def test_prompt_has_minimal_example(self):
        assert "最小示例" in _FORCED_SUMMARY_PROMPT
