"""
RBAC (Role-Based Access Control) 权限控制模块
实现角色权限校验和数据范围隔离
"""

from typing import List, Optional
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_

from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole


# ============ 角色权限常量 ============

class Permission:
    """权限常量"""
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    PROJECT_CREATE = "project:create"
    PROJECT_READ = "project:read"
    PROJECT_UPDATE = "project:update"
    PROJECT_DELETE = "project:delete"
    TASK_CREATE = "task:create"
    TASK_READ = "task:read"
    TASK_UPDATE = "task:update"
    TASK_DELETE = "task:delete"
    PROMPT_CREATE = "prompt:create"
    PROMPT_READ = "prompt:read"
    PROMPT_UPDATE = "prompt:update"
    PROMPT_DELETE = "prompt:delete"
    DASHBOARD_READ = "dashboard:read"
    CONFIG_READ = "config:read"
    CONFIG_UPDATE = "config:update"


# ============ 角色权限映射 ============

ROLE_PERMISSIONS = {
    UserRole.SUPER_ADMIN: [
        Permission.USER_CREATE, Permission.USER_READ, Permission.USER_UPDATE, Permission.USER_DELETE,
        Permission.PROJECT_CREATE, Permission.PROJECT_READ, Permission.PROJECT_UPDATE, Permission.PROJECT_DELETE,
        Permission.TASK_CREATE, Permission.TASK_READ, Permission.TASK_UPDATE, Permission.TASK_DELETE,
        Permission.PROMPT_CREATE, Permission.PROMPT_READ, Permission.PROMPT_UPDATE, Permission.PROMPT_DELETE,
        Permission.DASHBOARD_READ,
        Permission.CONFIG_READ, Permission.CONFIG_UPDATE,
    ],
    UserRole.ADMIN: [
        Permission.USER_CREATE,  # 仅可创建普通用户
        Permission.USER_READ,    # 仅可查看自己及下辖用户
        Permission.USER_UPDATE,  # 仅可编辑下辖用户
        Permission.USER_DELETE,  # 仅可删除下辖用户
        Permission.PROJECT_CREATE, Permission.PROJECT_READ, Permission.PROJECT_UPDATE, Permission.PROJECT_DELETE,
        Permission.TASK_CREATE, Permission.TASK_READ, Permission.TASK_UPDATE, Permission.TASK_DELETE,
        Permission.PROMPT_CREATE, Permission.PROMPT_READ, Permission.PROMPT_UPDATE, Permission.PROMPT_DELETE,
        Permission.DASHBOARD_READ,
    ],
    UserRole.USER: [
        Permission.PROJECT_CREATE, Permission.PROJECT_READ, Permission.PROJECT_UPDATE, Permission.PROJECT_DELETE,
        Permission.TASK_CREATE, Permission.TASK_READ, Permission.TASK_UPDATE, Permission.TASK_DELETE,
        Permission.PROMPT_CREATE, Permission.PROMPT_READ, Permission.PROMPT_UPDATE, Permission.PROMPT_DELETE,
        Permission.DASHBOARD_READ,
    ],
}


# ============ 权限检查函数 ============

def has_permission(user: User, permission: str) -> bool:
    """检查用户是否拥有指定权限"""
    if not user:
        return False
    role = getattr(user, "role", None)
    if not role:
        return False
    permissions = ROLE_PERMISSIONS.get(role, [])
    return permission in permissions


def require_permission(permission: str):
    """FastAPI依赖：要求指定权限"""
    async def permission_checker(
        current_user: User = Depends(deps.get_current_user),
    ) -> User:
        if not has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足，无法执行此操作"
            )
        return current_user
    return permission_checker


def require_role(roles: List[str]):
    """FastAPI依赖：要求指定角色之一"""
    async def role_checker(
        current_user: User = Depends(deps.get_current_user),
    ) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {', '.join(roles)}"
            )
        return current_user
    return role_checker


# ============ 数据范围过滤器 ============

class DataScope:
    """数据范围常量"""
    ALL = "all"           # 全部数据
    SELF_AND_SUB = "self_and_subordinate"  # 自己及下属
    SELF_ONLY = "self_only"  # 仅自己


def get_user_data_scope(user: User) -> str:
    """根据角色获取数据范围"""
    if user.role == UserRole.SUPER_ADMIN:
        return DataScope.ALL
    elif user.role == UserRole.ADMIN:
        return DataScope.SELF_AND_SUB
    else:
        return DataScope.SELF_ONLY


async def get_subordinate_user_ids(db: AsyncSession, admin_id: str) -> List[str]:
    """获取管理员下辖的所有普通用户ID（递归）"""
    result = await db.execute(
        select(User.id).where(User.parent_admin_id == admin_id)
    )
    return [row[0] for row in result.all()]


def build_user_filter(user: User, user_ids: Optional[List[str]] = None):
    """
    构建用户查询过滤条件

    Args:
        user: 当前用户
        user_ids: 管理员下辖用户ID列表（预查询）

    Returns:
        SQLAlchemy where 条件
    """
    if user.role == UserRole.SUPER_ADMIN:
        return None  # 无过滤，返回全部
    elif user.role == UserRole.ADMIN:
        ids = [user.id]
        if user_ids:
            ids.extend(user_ids)
        return User.id.in_(ids)
    else:
        return User.id == user.id


def build_project_filter(user: User, user_ids: Optional[List[str]] = None):
    """
    构建项目查询过滤条件

    项目owner_id必须在当前用户的数据范围内
    """
    from app.models.project import Project

    if user.role == UserRole.SUPER_ADMIN:
        return None
    elif user.role == UserRole.ADMIN:
        ids = [user.id]
        if user_ids:
            ids.extend(user_ids)
        return Project.owner_id.in_(ids)
    else:
        return Project.owner_id == user.id


def build_task_filter(user: User, user_ids: Optional[List[str]] = None):
    """
    构建审计任务查询过滤条件

    任务created_by必须在当前用户的数据范围内
    """
    from app.models.audit import AuditTask

    if user.role == UserRole.SUPER_ADMIN:
        return None
    elif user.role == UserRole.ADMIN:
        ids = [user.id]
        if user_ids:
            ids.extend(user_ids)
        return AuditTask.created_by.in_(ids)
    else:
        return AuditTask.created_by == user.id


def build_prompt_filter(user: User, user_ids: Optional[List[str]] = None):
    """
    构建提示词查询过滤条件

    系统提示词 + 当前用户数据范围内的用户创建的提示词
    """
    from app.models.prompt_template import PromptTemplate

    if user.role == UserRole.SUPER_ADMIN:
        return None
    elif user.role == UserRole.ADMIN:
        ids = [user.id]
        if user_ids:
            ids.extend(user_ids)
        return or_(
            PromptTemplate.is_system == True,
            PromptTemplate.created_by.in_(ids)
        )
    else:
        return or_(
            PromptTemplate.is_system == True,
            PromptTemplate.created_by == user.id
        )


def build_agent_task_filter(user: User, user_ids: Optional[List[str]] = None):
    """
    构建Agent任务查询过滤条件
    """
    from app.models.agent_task import AgentTask

    if user.role == UserRole.SUPER_ADMIN:
        return None
    elif user.role == UserRole.ADMIN:
        ids = [user.id]
        if user_ids:
            ids.extend(user_ids)
        return AgentTask.created_by.in_(ids)
    else:
        return AgentTask.created_by == user.id


# ============ 便捷依赖 ============

require_super_admin = require_role([UserRole.SUPER_ADMIN])
require_admin_or_above = require_role([UserRole.SUPER_ADMIN, UserRole.ADMIN])
require_any_role = require_role([UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.USER])
