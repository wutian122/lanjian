"""P7-5 五态口径统一（2026-10-07 工作流实证 L6 口径分裂）。

证据：Unknown Finding 的 PoC 输出 'VULNERABILITY_CONFIRMED: false_positive'
被 verification 计为 1 confirmed（trace 实证），落库时被 is_strict_finding
过滤 → 用户侧 0 confirmed；两个口径不一致，且"验证确认但落库丢弃"的项
零记录（用户无法解释数量差异）。

契约：
- verification 收口五态计数提取 static_confirmed 变量（handoff 用）；
- 落库过滤发生时，被过滤项（verification 判 confirmed/static_confirmed）
  必须写入 task.observations 条目 'filtered_confirmed_finding'，含 title/
  status/过滤原因——数量差异可追溯。
"""
import pytest

from app.api.v1.endpoints.agent_tasks import _build_filtered_finding_observation


class TestFilteredConfirmedObservationP7:
    def test_confirmed_finding_filtered_gets_observation(self):
        finding = {
            "title": "Unknown Finding",
            "verification_status": "confirmed",
            "file_path": "x.java",
        }
        obs = _build_filtered_finding_observation(
            finding, reason="is_strict_finding: title/type 不合规"
        )
        assert obs is not None
        assert obs["gate"] == "filtered_confirmed_finding"
        assert obs["verification_status"] == "confirmed"
        assert obs["title"] == "Unknown Finding"
        assert "is_strict_finding" in obs["reason"]

    def test_static_confirmed_filtered_gets_observation(self):
        obs = _build_filtered_finding_observation(
            {"title": "t", "verification_status": "static_confirmed",
             "file_path": "a.java"},
            reason="is_strict_finding: line 缺失",
        )
        assert obs["gate"] == "filtered_confirmed_finding"

    def test_unverified_finding_filtered_no_special_obs(self):
        # needs_context/not_reproducible 被过滤属正常质量门，不需专门留痕
        obs = _build_filtered_finding_observation(
            {"title": "t", "verification_status": "needs_context",
             "file_path": "a.java"},
            reason="is_strict_finding",
        )
        assert obs is None


class TestVerificationCounterP7:
    def test_static_confirmed_variable_extracted(self):
        from app.services.agent.agents.verification import _count_verification_outcomes

        counts = _count_verification_outcomes([
            {"verification_status": "static_confirmed"},
            {"verification_status": "confirmed"},
        ])
        assert counts["static_confirmed"] == 1
        assert counts["confirmed"] == 1
