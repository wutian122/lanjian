"""审计日志查询端点（#2: 解决 audit_logs 只写不读的问题）"""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.api import deps
from app.core.rbac import UserRole
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.user import User

router = APIRouter()


@router.get("")
async def list_audit_logs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    action: str = Query(None, description="按操作类型过滤，如 login_success / task_deleted"),
    target_type: str = Query(None, description="按目标类型过滤，如 user / agent_task"),
) -> Any:
    """查询审计日志（仅超级管理员）

    只读审计记录，不含任何敏感字段的明文（details 中不落 API Key/密码）。
    """
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="仅超级管理员可查看审计日志")

    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if target_type:
        query = query.where(AuditLog.target_type == target_type)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0

    rows = (
        await db.execute(
            query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
        )
    ).scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "actor_id": row.actor_id,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "details": row.details,
                "ip_address": row.ip_address,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }
