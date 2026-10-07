"""P7-2 Semgrep 命中全量留痕（2026-10-07 工作流实证）。

证据（B 机 c8686a20）：预扫 129 命中 → 去重 82 → 46 热点 → 30 注入上下文
→ 采纳 2 → **80 条未采纳在正常路径零记录**（observations 无 semgrep、
报告段 0 触发——守卫 orchestrator.py:841 在 Analysis 有产出时整条短路）。
兜底通道（gate observation + 报告段）已存在但只在"Analysis 0 产出"时触发。

契约：新增 `_register_semgrep_leads_observation`（幂等）——**无论 Analysis
是否有产出**，收口时把预扫命中的统计+明细写入 gate observation
"semgrep_leads"（含 per-命中明细与采纳/未采纳计数），报告段复用渲染。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent(semgrep_findings, all_findings):
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.config = MagicMock()
    agent.config.name = "Orchestrator"
    agent._semgrep_findings = semgrep_findings
    agent._all_findings = all_findings
    agent._gate_observations = []
    agent._semgrep_leads_registered = False
    return agent


class TestSemgrepLeadsObservation:
    def test_registers_all_hits_with_adoption_counts(self):
        semgrep = [
            {"file_path": "a.java", "line_start": 10, "semgrep_rule_id": "rule.x",
             "description": "d1", "vulnerability_type": "sql_injection"},
            {"file_path": "b.java", "line_start": 20, "semgrep_rule_id": "rule.y",
             "description": "d2", "vulnerability_type": "xss"},
            {"file_path": "c.java", "line_start": 30, "semgrep_rule_id": "rule.z",
             "description": "d3", "vulnerability_type": "other"},
        ]
        adopted = [{"file_path": "a.java", "line_start": 12, "title": "SQLi"}]
        agent = _make_agent(semgrep, adopted)
        agent._register_semgrep_leads_observation()
        obs = agent._gate_observations
        assert len(obs) == 1
        entry = obs[0]
        assert entry["gate"] == "semgrep_leads"
        assert entry["counts"]["hits"] == 3
        assert entry["counts"]["adopted"] == 1
        assert entry["counts"]["unadopted"] == 2
        cands = entry["candidates"]
        assert len(cands) == 3
        assert any(c["file_path"] == "c.java" for c in cands)

    def test_idempotent(self):
        agent = _make_agent(
            [{"file_path": "a.java", "line_start": 1, "semgrep_rule_id": "r",
              "description": "d", "vulnerability_type": "other"}], [])
        agent._register_semgrep_leads_observation()
        agent._register_semgrep_leads_observation()
        assert len(agent._gate_observations) == 1

    def test_no_hits_no_observation(self):
        agent = _make_agent([], [])
        agent._register_semgrep_leads_observation()
        assert agent._gate_observations == []


class TestReportSectionIncludesLeads:
    def test_semgrep_leads_gate_rendered(self):
        from app.api.v1.endpoints.agent_tasks import _build_unverified_static_leads_section

        obs = [{
            "gate": "semgrep_leads",
            "reason": "3 条预扫命中：1 采纳 / 2 未采纳",
            "candidates": [
                {"file_path": "c.java", "line": 30, "title": "rule.z", "type": "other"},
            ],
        }]
        lines = _build_unverified_static_leads_section(obs)
        text = "\n".join(lines)
        assert "c.java:30" in text
        assert "未验证静态线索" in text
