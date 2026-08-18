from __future__ import annotations

from typing import Any, Mapping

# confidence 阈值：低于此值的发现视为低置信度，过滤掉
MIN_CONFIDENCE_THRESHOLD: float = 0.7

DESCRIPTIVE_PATTERNS = (
    "路由结构清晰",
    "依赖中间件",
    "应用自定义，包含所有路由",
    "fastapi 应用定义",
    "结构清晰",
    "架构清晰",
)


def is_strict_finding(finding: Mapping[str, Any]) -> bool:
    file_path = str(finding.get("file_path") or "").strip()
    if not file_path or file_path.lower() in ("unknown", "n/a", "", "?"):
        return False
    line_start = finding.get("line_start", 0)
    if not line_start or line_start <= 0:
        return False
    vuln_type = str(finding.get("vulnerability_type") or "").strip()
    if not vuln_type:
        return False

    # confidence 阈值过滤：低于 0.7 的发现不通过 strict 校验
    confidence = finding.get("confidence")
    if confidence is None:
        confidence = finding.get("ai_confidence")
    if confidence is not None:
        try:
            if float(confidence) < MIN_CONFIDENCE_THRESHOLD:
                return False
        except (TypeError, ValueError):
            return False

    title = str(finding.get("title") or "").strip()
    description = str(finding.get("description") or "").strip()
    combined = (title + " " + description).lower()

    for pattern in DESCRIPTIVE_PATTERNS:
        if pattern in combined:
            return False

    return True