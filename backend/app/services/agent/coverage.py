"""D1-D10 security dimension coverage matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping


class CoverageStatus(str, Enum):
    COVERED = "covered"
    SHALLOW = "shallow"
    UNCOVERED = "uncovered"


@dataclass(frozen=True)
class CoverageDimension:
    id: str
    name: str
    vulnerability_types: tuple[str, ...]
    keywords: tuple[str, ...]
    required: bool = False


COVERAGE_DIMENSIONS: tuple[CoverageDimension, ...] = (
    CoverageDimension(
        "D1",
        "injection",
        ("sql_injection", "command_injection", "code_injection"),
        ("sql", "inject", "exec", "eval"),
        True,
    ),
    CoverageDimension(
        "D2",
        "auth",
        ("auth_bypass",),
        ("auth", "login", "jwt", "token"),
        True,
    ),
    CoverageDimension(
        "D3",
        "authz",
        ("idor", "auth_bypass"),
        ("permission", "authorize", "owner", "idor"),
        True,
    ),
    CoverageDimension(
        "D4",
        "deserialization",
        ("deserialization", "xxe"),
        ("deserialize", "pickle", "yaml.load"),
    ),
    CoverageDimension(
        "D5",
        "file",
        ("path_traversal", "file_inclusion"),
        ("upload", "download", "path", "open("),
    ),
    CoverageDimension(
        "D6",
        "ssrf",
        ("ssrf",),
        ("ssrf", "requests.get", "fetch", "http.request", "url"),
    ),
    CoverageDimension(
        "D7",
        "crypto",
        ("weak_crypto", "hardcoded_secret"),
        ("secret", "crypto", "md5", "sha1", "key"),
    ),
    CoverageDimension(
        "D8",
        "config",
        ("sensitive_data_exposure", "security_misconfiguration", "config", "misconfiguration"),
        ("cors", "debug", "config", "allow_origins"),
    ),
    CoverageDimension(
        "D9",
        "business_logic",
        ("business_logic", "race_condition"),
        ("business", "race", "transaction"),
    ),
    CoverageDimension(
        "D10",
        "supply_chain",
        ("dependency", "supply_chain", "outdated_dependency", "vulnerable_dependency"),
        ("package.json", "requirements.txt", "pom.xml", "npm_audit", "safety", "osv"),
    ),
)


@dataclass
class CoverageReport:
    statuses: dict[str, CoverageStatus]
    evidence: dict[str, list[str]] = field(default_factory=dict)
    dimension_counts: dict[str, int] = field(default_factory=dict)

    @property
    def covered_count(self) -> int:
        return sum(1 for status in self.statuses.values() if status == CoverageStatus.COVERED)

    @property
    def required_covered(self) -> bool:
        return all(
            self.statuses.get(dimension.id) == CoverageStatus.COVERED
            for dimension in COVERAGE_DIMENSIONS
        )

    @property
    def is_sufficient(self) -> bool:
        # covered >= 8 and D1/D2/D3 required dimensions all covered
        return self.covered_count >= 8 and self.required_covered

    @property
    def gaps(self) -> list[str]:
        return [
            f"{dimension.id} {dimension.name}: "
            f"{self.statuses.get(dimension.id, CoverageStatus.UNCOVERED).value}"
            for dimension in COVERAGE_DIMENSIONS
            if self.statuses.get(dimension.id, CoverageStatus.UNCOVERED) != CoverageStatus.COVERED
        ]

    def to_prompt(self) -> str:
        lines = [
            "## D1-D10 coverage self-check not met",
            f"Current deep coverage: {self.covered_count}/10. Required D1-D3: {'yes' if self.required_covered else 'no'}.",
            "Please supplement uncovered or shallow dimensions. Do not repeat already covered areas.",
        ]
        lines.extend(f"- {gap}" for gap in self.gaps)
        return "\n".join(lines)



def evaluate_coverage(
    findings: Iterable[Mapping[str, object]],
    text_evidence: Iterable[str],
) -> CoverageReport:
    joined_text = "\n".join(text_evidence).lower()
    statuses: dict[str, CoverageStatus] = {}
    evidence: dict[str, list[str]] = {}

    finding_types = {
        str(finding.get("vulnerability_type") or "").lower()
        for finding in findings
        if isinstance(finding, Mapping)
    }

    for dimension in COVERAGE_DIMENSIONS:
        dimension_evidence: list[str] = []
        if any(vuln_type in finding_types for vuln_type in dimension.vulnerability_types):
            statuses[dimension.id] = CoverageStatus.COVERED
            dimension_evidence.append("finding")
        elif any(keyword.lower() in joined_text for keyword in dimension.keywords):
            statuses[dimension.id] = CoverageStatus.SHALLOW
            dimension_evidence.append("keyword")
        else:
            statuses[dimension.id] = CoverageStatus.UNCOVERED
        evidence[dimension.id] = dimension_evidence

    return CoverageReport(statuses=statuses, evidence=evidence)
