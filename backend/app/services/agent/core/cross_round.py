"""跨轮传递数据结构"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class CrossRoundContext:
    """跨轮传递结构 - R1 产出，R2 必须遵循"""

    covered: dict[str, str] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    clean: list[str] = field(default_factory=list)
    hotspots: list[dict] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    grep_done: list[str] = field(default_factory=list)

    MAX_PROMPT_LENGTH = 8000

    @staticmethod
    def _sanitize(text: str) -> str:
        """清理文本中的潜在注入字符"""
        if not text:
            return text
        # 移除明显的 prompt 注入模式
        text = re.sub(r";\s*(Action|Thought|Observation)\s*:", "", text, flags=re.IGNORECASE)
        # 移除 SQL 注入模式
        text = re.sub(r"'\s*;\s*(DROP|DELETE|INSERT|UPDATE|SELECT)\b", "", text, flags=re.IGNORECASE)
        # 移除 shell 注入模式
        text = re.sub(r"[;|`$]\s*(echo|rm|cat|bash|sh|python|node)\b", "", text, flags=re.IGNORECASE)
        # 截断过长文本
        if len(text) > 200:
            text = text[:200] + "..."
        return text

    def to_prompt(self) -> str:
        """生成注入到 R2 Agent prompt 的文本"""
        lines = ["## 跨轮传递上下文（R1 产出，R2 必须遵循）\n"]

        lines.append("### 已覆盖维度")
        for dim, status in self.covered.items():
            lines.append(f"- {self._sanitize(dim)}: {self._sanitize(status)}")

        lines.append("\n### 未覆盖维度（R2 必须补充）")
        for gap in self.gaps:
            lines.append(f"- {self._sanitize(gap)}")

        if self.clean:
            lines.append("\n### 已确认干净的攻击面（R2 禁止重复搜索）")
            for c in self.clean[:20]:
                lines.append(f"- {self._sanitize(c)}")

        if self.hotspots:
            lines.append("\n### 高风险热点（R2 优先深入）")
            for h in self.hotspots[:15]:
                f = self._sanitize(h.get('file', '?'))
                ln = h.get('line', '?')
                desc = self._sanitize(h.get('description', ''))
                lines.append(f"- {f}:{ln} - {desc}")

        if self.files_read:
            lines.append("\n### R1 已读文件（R2 禁止重读）")
            for f in self.files_read[:50]:
                lines.append(f"- {self._sanitize(f)}")

        if self.grep_done:
            lines.append("\n### R1 已执行搜索（R2 禁止重复）")
            for g in self.grep_done[:30]:
                lines.append(f"- {self._sanitize(g)}")

        result = "\n".join(lines)

        # 长度上限保护
        if len(result) > self.MAX_PROMPT_LENGTH:
            result = result[:self.MAX_PROMPT_LENGTH] + "\n\n[跨轮上下文已截断]"

        return result

    def to_dict(self) -> dict:
        return {
            "covered": self.covered,
            "gaps": self.gaps,
            "clean": self.clean,
            "hotspots": self.hotspots,
            "files_read_count": len(self.files_read),
            "grep_done_count": len(self.grep_done),
        }
