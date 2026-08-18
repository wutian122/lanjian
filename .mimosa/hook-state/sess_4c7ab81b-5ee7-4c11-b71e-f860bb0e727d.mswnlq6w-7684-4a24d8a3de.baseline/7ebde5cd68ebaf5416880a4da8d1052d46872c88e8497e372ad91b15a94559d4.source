import pytest
from app.services.agent.core.attack_chain import AttackChainAnalyzer


class TestAttackChainAnalyzer:
    def test_auth_bypass_plus_ssrf(self):
        analyzer = AttackChainAnalyzer()
        findings = [
            {"id": "1", "vulnerability_type": "auth_bypass", "title": "JWT forge", "severity": "high"},
            {"id": "2", "vulnerability_type": "ssrf", "title": "SSRF in import", "severity": "medium"},
        ]
        chains = analyzer.analyze(findings)
        assert len(chains) >= 1
        assert any("SSRF" in c["name"] for c in chains)

    def test_no_chain_when_only_medium(self):
        analyzer = AttackChainAnalyzer()
        findings = [
            {"id": "1", "vulnerability_type": "xss", "title": "Reflected XSS", "severity": "medium"},
        ]
        chains = analyzer.analyze(findings)
        assert len(chains) == 0

    def test_info_leak_to_auth_bypass(self):
        analyzer = AttackChainAnalyzer()
        findings = [
            {"id": "1", "vulnerability_type": "hardcoded_secret", "title": "API key in config", "severity": "high"},
            {"id": "2", "vulnerability_type": "auth_bypass", "title": "JWT no verify", "severity": "critical"},
        ]
        chains = analyzer.analyze(findings)
        assert len(chains) >= 1
        assert any("信息泄露" in c["name"] for c in chains)

    def test_chain_steps_reference_finding_ids(self):
        analyzer = AttackChainAnalyzer()
        findings = [
            {"id": "f1", "vulnerability_type": "auth_bypass", "title": "Auth bypass", "severity": "critical"},
            {"id": "f2", "vulnerability_type": "ssrf", "title": "SSRF", "severity": "high"},
        ]
        chains = analyzer.analyze(findings)
        for chain in chains:
            for step in chain["steps"]:
                assert step["finding_id"] in ["f1", "f2"]

    def test_auth_bypass_alone_does_not_trigger_function_abuse_chain(self):
        """认证绕过单独存在不应触发'认证绕过→功能滥用'链"""
        analyzer = AttackChainAnalyzer()
        findings = [
            {"id": "1", "vulnerability_type": "auth_bypass", "title": "JWT forge", "severity": "high"},
        ]
        chains = analyzer.analyze(findings)
        assert not any(c["name"] == "认证绕过 → 功能滥用" for c in chains)

    def test_auth_bypass_with_high_privilege_triggers_function_abuse(self):
        """认证绕过 + 高权限端点 → 触发'认证绕过→功能滥用'链"""
        analyzer = AttackChainAnalyzer()
        findings = [
            {"id": "1", "vulnerability_type": "auth_bypass", "title": "JWT forge", "severity": "high"},
            {"id": "2", "vulnerability_type": "privilege_escalation", "title": "Admin API exposed", "severity": "high"},
        ]
        chains = analyzer.analyze(findings)
        assert any(c["name"] == "认证绕过 → 功能滥用" for c in chains)

    def test_finding_without_id_field_does_not_crash(self):
        """finding 缺少 id 字段不应导致 KeyError"""
        analyzer = AttackChainAnalyzer()
        findings = [
            {"vulnerability_type": "auth_bypass", "title": "JWT forge", "severity": "high"},
            {"vulnerability_type": "ssrf", "title": "SSRF", "severity": "high"},
        ]
        chains = analyzer.analyze(findings)
        for chain in chains:
            for step in chain["steps"]:
                assert "finding_id" in step

    def test_no_duplicate_steps_in_chain(self):
        """同一 finding 不应在 steps 中重复出现"""
        analyzer = AttackChainAnalyzer()
        findings = [
            {"id": "f1", "vulnerability_type": "auth_bypass", "title": "Auth bypass", "severity": "critical"},
            {"id": "f2", "vulnerability_type": "ssrf", "title": "SSRF", "severity": "high"},
        ]
        chains = analyzer.analyze(findings)
        for chain in chains:
            finding_ids = [s["finding_id"] for s in chain["steps"]]
            assert len(finding_ids) == len(set(finding_ids)), f"Duplicate finding_id in chain: {chain['name']}"
