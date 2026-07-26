"""攻击链分析器 - 评估漏洞组合可能性"""
from __future__ import annotations

from typing import Any


class AttackChainAnalyzer:
    """分析 findings 之间的组合攻击可能性"""

    CHAIN_PATTERNS = [
        {
            "name": "认证绕过 → 功能滥用",
            "precondition": ["auth_bypass"],
            "amplifier": ["privilege_escalation"],
            "impact": "全系统沦陷",
        },
        {
            "name": "认证绕过 → SSRF",
            "precondition": ["auth_bypass"],
            "amplifier": ["ssrf"],
            "impact": "云环境接管",
        },
        {
            "name": "认证绕过 → JDBC/RCE",
            "precondition": ["auth_bypass"],
            "amplifier": ["deserialization", "command_injection", "code_injection"],
            "impact": "服务器 RCE",
        },
        {
            "name": "信息泄露 → 认证绕过",
            "precondition": ["sensitive_data_exposure", "hardcoded_secret"],
            "amplifier": ["auth_bypass"],
            "impact": "全系统访问",
        },
        {
            "name": "IDOR + 弱认证",
            "precondition": ["idor"],
            "amplifier": ["auth_bypass", "weak_crypto"],
            "impact": "未认证数据泄露",
        },
        {
            "name": "路径遍历 + 文件操作",
            "precondition": ["path_traversal"],
            "amplifier": ["file_inclusion"],
            "impact": "任意文件读取/RCE",
        },
    ]

    def analyze(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """分析 findings 之间的组合攻击可能性"""
        chains = []
        finding_types = set()
        for f in findings:
            vt = f.get("vulnerability_type", "")
            if vt:
                finding_types.add(vt)

        for pattern in self.CHAIN_PATTERNS:
            preconditions_met = any(
                pt in finding_types for pt in pattern["precondition"]
            )
            amplifiers_present = any(
                at in finding_types for at in pattern["amplifier"]
            )

            if preconditions_met and amplifiers_present:
                chains.append({
                    "name": pattern["name"],
                    "steps": self._build_chain_steps(findings, pattern),
                    "impact": pattern["impact"],
                    "severity": "critical",
                })

        return chains

    def _build_chain_steps(
        self, findings: list[dict[str, Any]], pattern: dict[str, Any]
    ) -> list[dict[str, Any]]:
        steps = []
        used_finding_ids: set[str] = set()
        step_counter = 0

        for precondition in pattern["precondition"]:
            matching = [f for f in findings if precondition in f.get("vulnerability_type", "")]
            if matching:
                fid = matching[0].get("id", f"finding_{id(matching[0]) & 0xFFFF}")
                if fid not in used_finding_ids:
                    step_counter += 1
                    steps.append({
                        "step": step_counter,
                        "finding_id": fid,
                        "title": matching[0].get("title", ""),
                        "role": "precondition",
                    })
                    used_finding_ids.add(fid)

        for amplifier in pattern["amplifier"]:
            matching = [f for f in findings if amplifier in f.get("vulnerability_type", "")]
            if matching:
                fid = matching[0].get("id", f"finding_{id(matching[0]) & 0xFFFF}")
                if fid not in used_finding_ids:
                    step_counter += 1
                    steps.append({
                        "step": step_counter,
                        "finding_id": fid,
                        "title": matching[0].get("title", ""),
                        "role": "amplifier",
                    })
                    used_finding_ids.add(fid)

        return steps
