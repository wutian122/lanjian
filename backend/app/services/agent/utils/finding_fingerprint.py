"""P9-9：finding 指纹（agent_findings.fingerprint 接线）。

口径（2026-10-08 裁决 D11/D13）：
- fingerprint = sha1(规范化 file_path + vulnerability_type + title 前 60 字符)[:16]；
- 空身份（无可用 file_path）不参与（返回 None）；
- 同 task 内同 fingerprint 的后到条目不静默删除——标注 duplicate_of
  （同时写入 finding_metadata），由报告/前端分组展示；
- 仅对新 finding 计算，历史数据不回填（D13，落库前调用天然满足）。

时序：全部对齐/绑定完成后、落库前统一计算（见 _save_findings）。
"""
from __future__ import annotations

import hashlib
from typing import Any

_UNUSABLE_PATHS = ("unknown", "?", "n/a", "none", "null")


def _normalize_file_path(file_path: Any) -> str:
    fp = str(file_path or "").strip().lower()
    return "" if fp in _UNUSABLE_PATHS else fp


def _normalize_vuln_type(vuln_type: Any) -> str:
    return (
        str(vuln_type or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def compute_finding_fingerprint(
    file_path: Any, vuln_type: Any, title: Any
) -> str | None:
    """计算 finding 指纹；空身份（无可用 file_path）返回 None。"""
    fp = _normalize_file_path(file_path)
    if not fp:
        return None
    vt = _normalize_vuln_type(vuln_type)
    title_prefix = str(title or "").strip()[:60]
    raw = f"{fp}|{vt}|{title_prefix}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _fingerprint_source(finding: dict[str, Any]) -> Any:
    """vuln_type 的字段兼容（vulnerability_type/type/vuln_type）。"""
    return (
        finding.get("vulnerability_type")
        or finding.get("vuln_type")
        or finding.get("type")
    )


def assign_fingerprints(findings: list[dict[str, Any]]) -> dict[str, int]:
    """落库前统一计算指纹并标注同根因副本。

    返回 {"unique": 指纹数, "duplicates": 被标注 duplicate_of 的条目数}。
    """
    first_by_fp: dict[str, str] = {}
    duplicates = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        fp = compute_finding_fingerprint(
            finding.get("file_path"),
            _fingerprint_source(finding),
            finding.get("title"),
        )
        if not fp:
            continue
        finding["fingerprint"] = fp
        if fp in first_by_fp:
            # 语义安全选择：保留后到条目（可能是同文件不同行的另一实例），
            # 仅标注 duplicate_of 指向先到条目，不静默删除、不合并丢证据
            finding["duplicate_of"] = first_by_fp[fp]
            metadata = dict(finding.get("finding_metadata") or {})
            metadata["duplicate_of"] = first_by_fp[fp]
            finding["finding_metadata"] = metadata
            duplicates += 1
        else:
            first_by_fp[fp] = fp
    return {"unique": len(first_by_fp), "duplicates": duplicates}
