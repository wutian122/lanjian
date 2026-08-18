from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import delete as sql_delete
from pydantic import BaseModel
from datetime import datetime, timezone

from app.api import deps
from app.core.rbac import get_subordinate_user_ids, UserRole
from app.db.session import get_db
from app.models.audit import AuditTask, AuditIssue
from app.models.project import Project
from app.models.user import User
from app.services.scanner import task_control

router = APIRouter()


# Schemas
class AuditIssueSchema(BaseModel):
    id: str
    task_id: str
    file_path: str
    line_number: Optional[int] = None
    column_number: Optional[int] = None
    issue_type: str
    severity: str
    title: Optional[str] = None
    message: Optional[str] = None
    description: Optional[str] = None
    suggestion: Optional[str] = None
    code_snippet: Optional[str] = None
    ai_explanation: Optional[str] = None
    status: str
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class IssueUpdateSchema(BaseModel):
    status: Optional[str] = None


class ProjectSchema(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    source_type: Optional[str] = None
    repository_url: Optional[str] = None
    repository_type: Optional[str] = None
    default_branch: Optional[str] = None
    programming_languages: Optional[str] = None
    owner_id: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AuditTaskSchema(BaseModel):
    id: str
    project_id: str
    task_type: str
    status: str
    branch_name: Optional[str] = None
    exclude_patterns: Optional[str] = None
    scan_config: Optional[str] = None
    total_files: int = 0
    scanned_files: int = 0
    total_lines: int = 0
    issues_count: int = 0
    quality_score: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by: str
    created_at: datetime
    project: Optional[ProjectSchema] = None

    class Config:
        from_attributes = True


# ============ 辅助函数：检查任务访问权限 ============

async def check_task_access(db: AsyncSession, task_id: str, current_user: User) -> AuditTask:
    """检查用户是否有权访问任务"""
    result = await db.execute(
        select(AuditTask)
        .options(selectinload(AuditTask.project))
        .where(AuditTask.id == task_id)
    )
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 超级管理员可以访问所有任务
    if current_user.role == UserRole.SUPER_ADMIN:
        return task

    # 管理员可以访问自己和下辖用户的任务
    if current_user.role == UserRole.ADMIN:
        subordinate_ids = await get_subordinate_user_ids(db, current_user.id)
        allowed_ids = [current_user.id] + subordinate_ids
        if task.created_by not in allowed_ids:
            raise HTTPException(status_code=403, detail="无权访问此任务")
        return task

    # 普通用户只能访问自己的任务
    if task.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问此任务")

    return task


@router.get("/", response_model=List[AuditTaskSchema])
async def list_tasks(
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    List tasks (with RBAC data isolation).
    """
    query = select(AuditTask).options(selectinload(AuditTask.project))

    # 数据范围过滤（基于 created_by）
    if current_user.role == UserRole.SUPER_ADMIN:
        pass  # 全部
    elif current_user.role == UserRole.ADMIN:
        subordinate_ids = await get_subordinate_user_ids(db, current_user.id)
        ids = [current_user.id] + subordinate_ids
        query = query.where(AuditTask.created_by.in_(ids))
    else:
        query = query.where(AuditTask.created_by == current_user.id)

    if project_id:
        query = query.where(AuditTask.project_id == project_id)
    query = query.order_by(AuditTask.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{id}", response_model=AuditTaskSchema)
async def read_task(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Get task status by ID (with RBAC).
    """
    task = await check_task_access(db, id, current_user)
    return task


@router.post("/{id}/cancel")
async def cancel_task(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Cancel a running task (with RBAC).
    """
    task = await check_task_access(db, id, current_user)

    # 只有任务创建者或超级管理员可以取消
    if task.created_by != current_user.id and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="无权取消此任务")

    if task.status not in ["pending", "running"]:
        raise HTTPException(status_code=400, detail="只能取消待处理或运行中的任务")

    # 标记任务为取消
    task_control.cancel_task(id)

    # 更新数据库状态
    task.status = "cancelled"
    task.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return {"message": "任务已取消", "task_id": id}


@router.delete("/{id}")
async def delete_task(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """删除普通审计任务"""
    task = await check_task_access(db, id, current_user)

    if task.status in ["pending", "running"]:
        raise HTTPException(status_code=400, detail="运行中的任务不能直接删除，请先取消任务")

    try:
        # 级联删除关联 issues（避免 async lazy-load 失败）
        await db.execute(sql_delete(AuditIssue).where(AuditIssue.task_id == id))
        await db.delete(task)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="删除任务失败，请稍后重试")

    return {
        "taskId": id,
        "deleted": True,
    }


@router.get("/{id}/issues", response_model=List[AuditIssueSchema])
async def read_task_issues(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Get issues for a specific task (with RBAC).
    """
    # 先检查任务访问权限
    await check_task_access(db, id, current_user)

    result = await db.execute(
        select(AuditIssue)
        .where(AuditIssue.task_id == id)
        .order_by(
            # 按严重程度排序
            AuditIssue.severity.desc(),
            AuditIssue.created_at.desc()
        )
    )
    return result.scalars().all()


@router.patch("/{task_id}/issues/{issue_id}", response_model=AuditIssueSchema)
async def update_issue(
    task_id: str,
    issue_id: str,
    issue_update: IssueUpdateSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Update issue status (with RBAC).
    """
    # 先检查任务访问权限
    await check_task_access(db, task_id, current_user)

    result = await db.execute(
        select(AuditIssue)
        .where(AuditIssue.id == issue_id, AuditIssue.task_id == task_id)
    )
    issue = result.scalars().first()
    if not issue:
        raise HTTPException(status_code=404, detail="问题不存在")

    if issue_update.status:
        issue.status = issue_update.status
        if issue_update.status == "resolved":
            issue.resolved_by = current_user.id
            issue.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(issue)
    return issue


@router.get("/{id}/report/pdf")
async def export_task_report_pdf(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Export task audit report as PDF (with RBAC).
    """
    from fastapi.responses import Response
    from app.services.report_generator import ReportGenerator

    # 获取任务
    task = await check_task_access(db, id, current_user)

    # 获取问题列表
    issues_result = await db.execute(
        select(AuditIssue)
        .where(AuditIssue.task_id == id)
        .order_by(AuditIssue.severity.desc(), AuditIssue.created_at.desc())
    )
    issues = issues_result.scalars().all()

    # 转换为字典
    task_dict = {
        'id': task.id,
        'status': task.status,
        'branch_name': task.branch_name,
        'total_files': task.total_files,
        'scanned_files': task.scanned_files,
        'total_lines': task.total_lines,
        'issues_count': task.issues_count,
        'quality_score': task.quality_score,
        'created_at': task.created_at.isoformat() if task.created_at else None,
        'completed_at': task.completed_at.isoformat() if task.completed_at else None,
    }

    issues_list = [
        {
            'title': issue.title,
            'description': issue.description,
            'severity': issue.severity,
            'issue_type': issue.issue_type,
            'file_path': issue.file_path,
            'line_number': issue.line_number,
            'column_number': issue.column_number,
            'code_snippet': issue.code_snippet,
            'suggestion': issue.suggestion,
        }
        for issue in issues
    ]

    project_name = task.project.name if task.project else "Unknown Project"

    # 生成 PDF
    pdf_bytes = ReportGenerator.generate_task_report(task_dict, issues_list, project_name)

    # 返回 PDF 文件
    filename = f"audit-report-{task.id[:8]}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )
