from __future__ import annotations

from typing import Any, Mapping

# confidence 阈值：低于此值的发现视为低置信度，过滤掉
MIN_CONFIDENCE_THRESHOLD: float = 0.7

# 分层候选下界（spec finding-output-floor）：needs_verification=true 的候选
# 在 [0.1, 0.7) 区间放行交沙箱证实/证伪；低于 0.1 视为无依据噪声仍丢弃
MIN_CANDIDATE_CONFIDENCE: float = 0.1


def _is_verification_candidate(finding: Mapping[str, Any], conf_value: float | None) -> bool:
    """分层候选判定：显式 needs_verification 标记且置信度落在候选区间。

    候选豁免 0.7 硬阈值（Analysis 分层候选制——低置信可疑点交沙箱验证，
    而非在归一化/落库闸丢弃）；confidence < 0.1 的纯猜测不豁免。
    """
    if not finding.get("needs_verification"):
        return False
    if conf_value is None:
        return False
    return MIN_CANDIDATE_CONFIDENCE <= conf_value < MIN_CONFIDENCE_THRESHOLD


def _to_int(value: Any) -> int | None:
    """REQ-TH-1: LLM 数值字段归一化——'113'/'113.0'/113 → int；None/''/非法 → None，不抛异常。

    LLM 输出的 line_start 等字段偶发为字符串，直接数值比较/写库会崩溃（生产 c9de9d40）。
    """
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    """REQ-TH-1: LLM 数值字段归一化——'0.85'/0.85 → float；None/''/非法 → None。"""
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


DESCRIPTIVE_PATTERNS = (
    "路由结构清晰",
    "依赖中间件",
    "应用自定义，包含所有路由",
    "fastapi 应用定义",
    "结构清晰",
    "架构清晰",
)


def is_strict_finding(finding: Mapping[str, Any]) -> bool:
    vuln_type = str(finding.get("vulnerability_type") or "").strip()
    if not vuln_type:
        return False

    # confidence 阈值过滤：低于 0.7 的发现不通过 strict 校验；
    # 分层候选（needs_verification=true 且 0.1 ≤ confidence < 0.7）豁免，
    # 交沙箱验证证实/证伪（spec finding-output-floor）；< 0.1 仍不通过
    confidence = finding.get("confidence")
    if confidence is None:
        confidence = finding.get("ai_confidence")
    conf_value: float | None = None
    if confidence is not None:
        try:
            conf_value = float(confidence)
        except (TypeError, ValueError):
            conf_value = None
        if conf_value is not None and conf_value < MIN_CONFIDENCE_THRESHOLD:
            if not _is_verification_candidate(finding, conf_value):
                return False

    file_path = str(finding.get("file_path") or "").strip()
    line_start = _to_int(finding.get("line_start", 0)) or 0

    title = str(finding.get("title") or "").strip()
    description = str(finding.get("description") or "").strip()
    combined = (title + " " + description).lower()

    for pattern in DESCRIPTIVE_PATTERNS:
        if pattern in combined:
            return False

    # 有精确 file_path + line_start 的常规严格 finding
    if file_path and file_path.lower() not in ("unknown", "n/a", "?") and line_start > 0:
        return True

    # REQ-VP-3: 缺精确 file_path/line_start 但 confidence>=0.7 且有 title+description
    # 的理论风险 finding 保留落库（否则整条消失——nginx 生产实证，is_strict_finding 过滤日志铁证）。
    if conf_value is not None and conf_value >= MIN_CONFIDENCE_THRESHOLD and title and description:
        return True

    return False
