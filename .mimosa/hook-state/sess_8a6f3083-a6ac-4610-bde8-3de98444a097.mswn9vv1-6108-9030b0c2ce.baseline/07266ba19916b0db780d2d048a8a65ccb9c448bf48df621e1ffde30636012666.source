"""审计记忆系统 —— 项目级跨任务漏洞记忆加载器。

每次新开审计任务时，从同一项目的历史任务中加载已确认的漏洞发现，
聚合成「记忆线索」注入 Agent 上下文，让 AI 优先复查历史问题点。

设计原则：
- 复用现有 ``agent_findings`` 表，不新建表、不做迁移。
- 仅加载已确认（confirmed / static_confirmed）且中危以上的发现，避免噪音。
- 按 fingerprint 去重，跨任务同一漏洞只保留一条。
- 全流程 non-fatal：任何异常仅记日志，返回空列表，绝不阻断审计任务启动。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)


# 仅这些验证状态的发现进入记忆（高可信）
MEMORY_VERIFICATION_STATUSES = ("confirmed", "static_confirmed")

# 仅这些严重级别进入记忆（过滤 low/info 噪音）
MEMORY_SEVERITIES = ("critical", "high", "medium")

# 严重度排序权重（越小越靠前）
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# 严重度对应的展示图标
_SEVERITY_ICON = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "info": "⚪",
}

# 默认注入条数上限
DEFAULT_MEMORY_LIMIT = 30


def _finding_to_memory(finding: Any, task_name: Optional[str]) -> Dict[str, Any]:
    """把一条 AgentFinding ORM 对象转换为精简的记忆条目。

    只保留注入线索所需字段，避免把完整记录塞进上下文浪费 Token。
    """
    return {
        "type": getattr(finding, "vulnerability_type", None),
        "severity": (getattr(finding, "severity", None) or "medium"),
        "file_path": getattr(finding, "file_path", None),
        "line_start": getattr(finding, "line_start", None),
        "function_name": getattr(finding, "function_name", None),
        "title": getattr(finding, "title", None),
        "description": getattr(finding, "description", None),
        "verification_status": getattr(finding, "verification_status", None),
        "fingerprint": getattr(finding, "fingerprint", None),
        "task_name": task_name,
    }


def _dedup_by_fingerprint(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 fingerprint 去重，保留首次出现的条目（已按严重度/时间排序）。

    fingerprint 为空的条目不参与合并（各自保留），避免误判为同一漏洞。
    """
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for e in entries:
        fp = e.get("fingerprint")
        if fp:
            if fp in seen:
                continue
            seen.add(fp)
        out.append(e)
    return out


def _rows_to_memory(rows: List[Any], limit: int) -> List[Dict[str, Any]]:
    """把查询结果行 [(AgentFinding, task_name), ...] 处理为记忆条目列表。

    纯函数：映射字段 → 按严重度排序 → fingerprint 去重 → 截断。
    抽出以便独立单测，与数据库查询解耦。
    """
    entries = [_finding_to_memory(finding, task_name) for finding, task_name in rows]
    entries.sort(key=lambda e: _SEVERITY_ORDER.get(e.get("severity"), 5))
    entries = _dedup_by_fingerprint(entries)
    return entries[:limit]


async def load_project_memory(
    db: Any,
    project_id: str,
    exclude_task_id: Optional[str] = None,
    limit: int = DEFAULT_MEMORY_LIMIT,
) -> List[Dict[str, Any]]:
    """加载同一项目历史任务中已确认的漏洞发现，作为记忆条目返回。

    Args:
        db: AsyncSession 数据库会话。
        project_id: 目标项目 ID。
        exclude_task_id: 当前任务 ID（排除自身，避免把本次发现当历史）。
        limit: 返回的记忆条目上限。

    Returns:
        精简记忆条目列表（去重、按严重度排序）；出错或无数据时返回 []。
    """
    if not project_id:
        return []

    try:
        # 延迟导入，避免模块级循环依赖
        from app.models.agent_task import AgentFinding, AgentTask

        stmt = (
            select(AgentFinding, AgentTask.name)
            .join(AgentTask, AgentFinding.task_id == AgentTask.id)
            .where(AgentTask.project_id == project_id)
            .where(AgentFinding.verification_status.in_(MEMORY_VERIFICATION_STATUSES))
            .where(AgentFinding.severity.in_(MEMORY_SEVERITIES))
        )
        if exclude_task_id:
            stmt = stmt.where(AgentFinding.task_id != exclude_task_id)

        result = await db.execute(stmt)
        rows = result.all()
        return _rows_to_memory(rows, limit)
    except Exception as e:  # noqa: BLE001 —— non-fatal，绝不阻断任务
        logger.warning(f"[AuditMemory] 加载项目记忆失败（非致命）: {e}")
        return []


def format_memory_lead(memory: List[Dict[str, Any]]) -> str:
    """把记忆条目格式化为注入 Agent 上下文的 Markdown 线索文本。

    空输入返回空字符串（调用方据此决定是否注入）。
    """
    if not memory:
        return ""

    total = len(memory)
    lines: List[str] = [
        "## 🧠 历史审计记忆 —— 同项目往次已确认漏洞",
        "",
        f"本项目历史审计已确认 **{total}** 处漏洞，以下为需重点复查的问题点：",
        "",
    ]

    # 按严重度分组展示
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for m in memory:
        grouped.setdefault(m.get("severity") or "medium", []).append(m)

    for sev in ("critical", "high", "medium", "low", "info"):
        items = grouped.get(sev)
        if not items:
            continue
        icon = _SEVERITY_ICON.get(sev, "⚪")
        lines.append(f"### {icon} {sev.upper()}")
        for m in items:
            loc = m.get("file_path") or "unknown"
            line_no = m.get("line_start")
            if line_no:
                loc = f"{loc}:{line_no}"
            vtype = m.get("type") or "unknown"
            status = m.get("verification_status") or ""
            task_name = m.get("task_name") or ""
            src = f"（来自任务: {task_name}）" if task_name else ""
            lines.append(f"- [{vtype}] @ {loc} — {status} {src}".rstrip())
            desc = (m.get("description") or "").strip()
            if desc:
                snippet = desc[:120] + ("…" if len(desc) > 120 else "")
                lines.append(f"  > {snippet}")
        lines.append("")

    lines.append(
        "**重要**：以上为往次审计**已确认**的发现。请优先复查这些位置是否仍存在漏洞或已修复，"
        "并留意相邻代码中的同类模式。这些是线索而非结论，仍需本次独立验证，不可直接采信。"
    )
    return "\n".join(lines)
