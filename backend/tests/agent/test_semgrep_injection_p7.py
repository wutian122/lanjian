"""P7-3 Semgrep 注入通道修复（2026-10-07 工作流实证）。

证据（B 机 c8686a20）：注入 orchestrator 的仅前 20 个**路径**（无规则号/
行号）；analysis 渲染读键 rule_id/check_id 而预扫实际存 semgrep_rule_id
→ 规则列恒"?"；15 条截断无排序（低价值在前则高价值丢失）。

契约：
- `_render_semgrep_section`（analysis）：读 semgrep_rule_id 键、显示
  severity、按 severity 降序排序取前 15；
- `_build_semgrep_hot_lead`（orchestrator）：热点注入带 path:line [rule]
  (severity)，按 severity 排序取前 20。
"""
import pytest


class TestAnalysisRenderP7:
    def test_rule_id_key_resolved_and_severity_sorted(self):
        from app.services.agent.agents.analysis import _render_semgrep_section

        findings = [
            {"file_path": "low.java", "line_start": 5, "semgrep_rule_id": "rule.low",
             "severity": "low", "description": "d"},
            {"file_path": "crit.java", "line_start": 9, "semgrep_rule_id": "rule.crit",
             "severity": "critical", "description": "d"},
            {"file_path": "high.java", "line_start": 7, "semgrep_rule_id": "rule.high",
             "severity": "high", "description": "d"},
        ]
        text = _render_semgrep_section(findings)
        assert "rule.crit" in text and "rule.high" in text and "rule.low" in text
        # 排序：critical 在 high 前，high 在 low 前
        assert text.index("rule.crit") < text.index("rule.high") < text.index("rule.low")

    def test_limit_15_keeps_highest_severity(self):
        from app.services.agent.agents.analysis import _render_semgrep_section

        findings = [
            {"file_path": f"f{i}.java", "line_start": i, "semgrep_rule_id": f"r{i}",
             "severity": "low", "description": "d"} for i in range(20)
        ]
        findings.append({"file_path": "vip.java", "line_start": 1,
                         "semgrep_rule_id": "rule.vip", "severity": "critical",
                         "description": "d"})
        text = _render_semgrep_section(findings)
        assert "rule.vip" in text, "高 severity 必须保留在截断内"
        assert text.count("- `") <= 15


class TestOrchestratorLeadP7:
    def test_hot_lead_includes_rule_and_line(self):
        from app.services.agent.agents.orchestrator import _build_semgrep_hot_lead

        findings = [
            {"file_path": "a.java", "line_start": 10, "semgrep_rule_id": "rule.x",
             "severity": "high"},
            {"file_path": "b.java", "line_start": 20, "semgrep_rule_id": "rule.y",
             "severity": "critical"},
        ]
        lead = _build_semgrep_hot_lead(findings)
        assert "b.java:20" in lead and "[rule.y]" in lead
        assert "(critical)" in lead
        assert lead.index("rule.y") < lead.index("rule.x")
