"""增量补漏轮次上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .coverage import CoverageReport, CoverageStatus


@dataclass
class RoundContext:
    round_number: int = 1
    covered: dict[str, str] = field(default_factory=dict)
    gaps: dict[str, str] = field(default_factory=dict)
    clean: list[str] = field(default_factory=list)
    hotspots: list[str] = field(default_factory=list)
    files_read: set[str] = field(default_factory=set)
    searches_done: set[str] = field(default_factory=set)
    previous_findings: list[Mapping[str, object]] = field(default_factory=list)

    @classmethod
    def from_coverage(
        cls,
        coverage: CoverageReport,
        previous_findings: list[Mapping[str, object]],
        round_number: int = 2,
    ) -> "RoundContext":
        covered = {
            dimension_id: status.value
            for dimension_id, status in coverage.statuses.items()
            if status == CoverageStatus.COVERED
        }
        gaps = {
            dimension_id: status.value
            for dimension_id, status in coverage.statuses.items()
            if status != CoverageStatus.COVERED
        }
        hotspots = [
            str(finding.get("file_path") or finding.get("title") or "unknown")
            for finding in previous_findings
            if isinstance(finding, Mapping)
        ]
        return cls(
            round_number=round_number,
            covered=covered,
            gaps=gaps,
            hotspots=hotspots,
            previous_findings=previous_findings,
        )

    def to_agent_prompt(self) -> str:
        lines = [
            f"## R{self.round_number} 增量补漏上下文",
            "本轮目标：只补未覆盖/浅覆盖方向，不重复已充分覆盖内容。",
            "",
            "### 已覆盖维度（避免重复）",
        ]
        lines.extend(f"- {key}: {value}" for key, value in self.covered.items())
        lines.append("### 缺口维度（优先分析）")
        lines.extend(f"- {key}: {value}" for key, value in self.gaps.items())
        if self.hotspots:
            lines.append("### 高风险待深入点")
            lines.extend(f"- {item}" for item in self.hotspots[:20])
        if self.files_read:
            lines.append("### 已读文件（非必要不重读）")
            lines.extend(f"- {item}" for item in sorted(self.files_read)[:30])
        if self.searches_done:
            lines.append("### 已执行搜索（禁止重复）")
            lines.extend(f"- {item}" for item in sorted(self.searches_done)[:30])
        return "\n".join(lines)
