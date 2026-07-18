"""10 维度覆盖率矩阵和自检逻辑"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 维度定义
DIMENSIONS = [
    "D1_injection",
    "D2_auth",
    "D3_authz",
    "D4_deserialization",
    "D5_file",
    "D6_ssrf",
    "D7_crypto",
    "D8_config",
    "D9_business_logic",
    "D10_supply_chain",
]

# vulnerability_type → 维度映射
_VULN_TYPE_MAP = {
    "sql_injection": "D1_injection",
    "nosql_injection": "D1_injection",
    "xss": "D1_injection",
    "command_injection": "D1_injection",
    "code_injection": "D1_injection",
    "ssti": "D1_injection",
    "ldap_injection": "D1_injection",
    "auth_bypass": "D2_auth",
    "weak_crypto": "D2_auth",
    "idor": "D3_authz",
    "business_logic": "D9_business_logic",
    "race_condition": "D9_business_logic",
    "deserialization": "D4_deserialization",
    "path_traversal": "D5_file",
    "file_inclusion": "D5_file",
    "ssrf": "D6_ssrf",
    "xxe": "D4_deserialization",
    "hardcoded_secret": "D7_crypto",
    "sensitive_data_exposure": "D7_crypto",
    "memory_corruption": "D4_deserialization",
    "csrf": "D3_authz",
    "open_redirect": "D8_config",
    "unvalidated_redirect": "D8_config",
    "security_misconfiguration": "D8_config",
    "privilege_escalation": "D3_authz",
    "insecure_deserialization": "D4_deserialization",
    "broken_access_control": "D3_authz",
    "other": "D9_business_logic",
    # Fix: 补充 Analysis Agent 实际产出的漏洞类型映射
    "supply_chain": "D10_supply_chain",
    "outdated_dependency": "D10_supply_chain",
    "vulnerable_dependency": "D10_supply_chain",
    "config": "D8_config",
    "misconfiguration": "D8_config",
    "prototype_pollution": "D1_injection",
    "graphql_injection": "D1_injection",
}

# grep pattern → 维度映射（关键词 → 维度）
_PATTERN_MAP = {
    "JWT": "D2_auth",
    "Token": "D2_auth",
    "Session": "D2_auth",
    "auth": "D2_auth",
    "login": "D2_auth",
    "password": "D2_auth",
    "execute": "D1_injection",
    "query": "D1_injection",
    "cursor": "D1_injection",
    "Statement": "D1_injection",
    "SELECT": "D1_injection",
    "Permission": "D3_authz",
    "authorize": "D3_authz",
    "role": "D3_authz",
    "pickle": "D4_deserialization",
    "marshal": "D4_deserialization",
    "yaml.load": "D4_deserialization",
    "upload": "D5_file",
    "open(": "D5_file",
    "read(": "D5_file",
    "write(": "D5_file",
    "FileInputStream": "D5_file",
    "requests.get": "D6_ssrf",
    "urllib": "D6_ssrf",
    "HttpClient": "D6_ssrf",
    "URL": "D6_ssrf",
    "secret": "D7_crypto",
    "key": "D7_crypto",
    "encrypt": "D7_crypto",
    "hash": "D7_crypto",
    "AES": "D7_crypto",
    "config": "D8_config",
    "env": "D8_config",
    "debug": "D8_config",
    "CORS": "D8_config",
    "npm audit": "D10_supply_chain",
    "safety": "D10_supply_chain",
    "CVE": "D10_supply_chain",
}


@dataclass
class CoverageReport:
    """覆盖率报告"""
    matrix: dict[str, dict] = field(default_factory=dict)
    covered_count: int = 0
    shallow_count: int = 0
    uncovered_count: int = 10

    @property
    def is_sufficient(self) -> bool:
        """覆盖率是否达标：covered >= 8 且 D1/D2/D3 全部 covered"""
        if self.covered_count < 8:
            return False
        for required in ["D1_injection", "D2_auth", "D3_authz"]:
            if self.matrix.get(required, {}).get("status") != "covered":
                return False
        return True

    def gaps(self) -> list[str]:
        """返回未覆盖的维度列表"""
        return [d for d, s in self.matrix.items() if s.get("status") == "unknown"]

    def to_dict(self) -> dict:
        return {
            "matrix": self.matrix,
            "covered_count": self.covered_count,
            "shallow_count": self.shallow_count,
            "uncovered_count": self.uncovered_count,
            "is_sufficient": self.is_sufficient,
            "gaps": self.gaps(),
        }


class CoverageMatrix:
    """10 维度覆盖率矩阵"""

    def __init__(self) -> None:
        self._matrix: dict[str, dict] = {
            dim: {"status": "unknown", "findings": 0, "evidence": ""}
            for dim in DIMENSIONS
        }

    def mark_covered(self, dimension: str, evidence: str = "") -> None:
        """标记维度为已覆盖（有深度分析）"""
        if dimension in self._matrix:
            self._matrix[dimension]["status"] = "covered"
            self._matrix[dimension]["findings"] += 1
            if evidence:
                self._matrix[dimension]["evidence"] = evidence

    def mark_shallow(self, dimension: str, evidence: str = "") -> None:
        """标记维度为浅覆盖（仅 Grep 搜索过）"""
        if dimension in self._matrix and self._matrix[dimension]["status"] == "unknown":
            self._matrix[dimension]["status"] = "shallow"
            if evidence:
                self._matrix[dimension]["evidence"] = evidence

    @staticmethod
    def map_finding_to_dimension(vulnerability_type: str) -> Optional[str]:
        """将漏洞类型映射到维度"""
        return _VULN_TYPE_MAP.get(vulnerability_type)

    @staticmethod
    def map_pattern_to_dimension(pattern: str) -> Optional[str]:
        """将 Grep 模式映射到维度（使用智能词边界匹配避免误匹配）"""
        import re
        for keyword, dim in _PATTERN_MAP.items():
            # 关键词末尾是单词字符时使用双侧词边界；末尾是非单词字符（如 open(）只在开头加词边界
            if keyword[-1].isalnum() or keyword[-1] == '_':
                regex = r'\b' + re.escape(keyword) + r'\b'
            else:
                regex = r'\b' + re.escape(keyword)
            if re.search(regex, pattern, re.IGNORECASE):
                return dim
        return None

    def to_report(self) -> CoverageReport:
        """生成覆盖率报告"""
        covered = sum(1 for d in self._matrix.values() if d["status"] == "covered")
        shallow = sum(1 for d in self._matrix.values() if d["status"] == "shallow")
        uncovered = sum(1 for d in self._matrix.values() if d["status"] == "unknown")
        return CoverageReport(
            matrix=dict(self._matrix),
            covered_count=covered,
            shallow_count=shallow,
            uncovered_count=uncovered,
        )
