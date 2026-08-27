from __future__ import annotations

import os
import shutil
from typing import Any, Dict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.agent_task import AgentCheckpoint, AgentEvent, AgentFinding, AgentTask, AgentTreeNode
from app.services.rag import CodeIndexer


async def cleanup_agent_task_resources(db: AsyncSession, task: AgentTask) -> Dict[str, Any]:
    cleaned_events = await _delete_rows(db, AgentEvent, AgentEvent.task_id == task.id)
    cleaned_findings = await _delete_rows(db, AgentFinding, AgentFinding.task_id == task.id)
    cleaned_checkpoints = await _delete_rows(db, AgentCheckpoint, AgentCheckpoint.task_id == task.id)
    cleaned_tree_nodes = await _delete_rows(db, AgentTreeNode, AgentTreeNode.task_id == task.id)

    # REQ-CLEAN-3: 删除任务时同步清理临时源码目录（幂等，容忍目录已不存在）
    cleaned_files: list[str] = []
    tmp_dir = f"/tmp/lanjian/{task.id}"
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        cleaned_files.append(tmp_dir)

    cleaned_indexes: list[str] = []
    warnings: list[str] = []

    remaining_tasks_result = await db.execute(
        select(AgentTask.id).where(
            AgentTask.project_id == task.project_id,
            AgentTask.id != task.id,
        )
    )
    remaining_task_ids = remaining_tasks_result.scalars().all()

    if not remaining_task_ids:
        try:
            collection_name = f"project_{task.project_id}"
            indexer = CodeIndexer(collection_name=collection_name, persist_directory=settings.VECTOR_DB_PATH)
            await indexer.clear()
            cleaned_indexes.append(collection_name)
        except Exception as exc:  # pragma: no cover
            warnings.append(f"向量索引清理失败: {exc}")

    await db.delete(task)
    
    # 🔥 FIX: 保护历史数据 — 在物理删除前记录审计摘要
    try:
        from app.models.audit_log import AuditLog
        summary = AuditLog(
            action="task_deleted",
            actor_id="system",
            target_type="agent_task",
            target_id=str(task.id),
            details={
                "name": task.name,
                "findings_count": task.findings_count or 0,
                "verified_count": task.verified_count or 0,
                "status": task.status.value if task.status else "unknown",
                "cleaned_events": cleaned_events,
                "cleaned_findings": cleaned_findings,
            },
            ip_address="system",
        )
        db.add(summary)
    except Exception:
        pass
    
    await db.commit()

    return {
        "taskId": task.id,
        "deleted": True,
        "cleanedEvents": cleaned_events,
        "cleanedFindings": cleaned_findings,
        "cleanedCheckpoints": cleaned_checkpoints,
        "cleanedTreeNodes": cleaned_tree_nodes,
        "cleanedFiles": cleaned_files,
        "cleanedIndexes": cleaned_indexes,
        "warnings": warnings,
    }


async def _delete_rows(db: AsyncSession, model: Any, condition: Any) -> int:
    result = await db.execute(delete(model).where(condition))
    return int(result.rowcount or 0)
