from typing import Any, List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, or_, delete, and_

from app.api import deps
from app.core import security
from app.core.rbac import (
    require_role, require_permission, has_permission,
    get_subordinate_user_ids, build_user_filter,
    UserRole, Permission
)
from app.db.session import get_db
from app.models.user import User
from app.models.project import Project, ProjectMember
from app.models.audit import AuditTask, AuditIssue
from app.models.agent_task import AgentTask
from app.models.analysis import InstantAnalysis
from app.models.user_config import UserConfig
from app.models.audit_rule import AuditRuleSet
from app.models.prompt_template import PromptTemplate
from app.schemas.user import User as UserSchema, UserCreate, UserUpdate, UserListResponse

router = APIRouter()


@router.get("/", response_model=UserListResponse)
async def read_users(
    db: AsyncSession = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索关键词"),
    role: Optional[str] = Query(None, description="角色筛选"),
    is_active: Optional[bool] = Query(None, description="状态筛选"),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取用户列表（支持分页、搜索、筛选，带RBAC数据隔离）
    """
    # 权限检查
    if not has_permission(current_user, Permission.USER_READ):
        raise HTTPException(status_code=403, detail="权限不足")

    query = select(User)
    count_query = select(func.count(User.id))

    # 数据范围过滤
    subordinate_ids = []
    if current_user.role == UserRole.ADMIN:
        subordinate_ids = await get_subordinate_user_ids(db, current_user.id)

    user_filter = build_user_filter(current_user, subordinate_ids)
    if user_filter is not None:
        query = query.where(user_filter)
        count_query = count_query.where(user_filter)

    # 搜索条件
    if search:
        search_filter = or_(
            User.email.ilike(f"%{search}%"),
            User.username.ilike(f"%{search}%"),
            User.full_name.ilike(f"%{search}%"),
            User.department.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # 角色筛选
    if role:
        query = query.where(User.role == role)
        count_query = count_query.where(User.role == role)

    # 状态筛选
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)

    # 获取总数
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # 分页查询
    query = query.order_by(User.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    return {
        "users": users,
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.post("/", response_model=UserSchema)
async def create_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    创建新用户（RBAC控制）
    - 超级管理员：可创建管理员和普通用户
    - 管理员：仅可创建普通用户
    - 普通用户：无权限
    """
    # 权限检查
    if not has_permission(current_user, Permission.USER_CREATE):
        raise HTTPException(status_code=403, detail="权限不足，无法创建用户")

    # 角色限制检查
    if current_user.role == UserRole.ADMIN:
        # 管理员只能创建普通用户
        if user_in.role != UserRole.USER:
            raise HTTPException(status_code=403, detail="管理员只能创建普通用户")
    elif current_user.role == UserRole.SUPER_ADMIN:
        # 超级管理员不能创建另一个超级管理员
        if user_in.role == UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="系统中只能存在一个超级管理员")
    else:
        raise HTTPException(status_code=403, detail="权限不足")

    # Check username uniqueness
    result = await db.execute(select(User).where(User.username == user_in.username))
    user = result.scalars().first()
    if user:
        raise HTTPException(
            status_code=400,
            detail="该用户名已被使用",
        )

    # Check email uniqueness if provided
    if user_in.email:
        result = await db.execute(select(User).where(User.email == user_in.email))
        user = result.scalars().first()
        if user:
            raise HTTPException(
                status_code=400,
                detail="该邮箱已被注册",
            )

    # 设置 parent_admin_id
    # #5-C: 杜绝 admin→admin 多层链 —— 管理员不允许设置上级（父级只能为 None），
    # 否则 get_subordinate_user_ids 仅查一层会导致"孙级用户对上级不可见"。
    parent_admin_id = None
    if current_user.role == UserRole.ADMIN and user_in.role == UserRole.USER:
        parent_admin_id = current_user.id
    elif current_user.role == UserRole.SUPER_ADMIN and user_in.role == UserRole.USER:
        # 超级管理员创建普通用户时，parent_admin_id 可选（或默认为超级管理员）
        parent_admin_id = user_in.parent_admin_id or current_user.id
    elif current_user.role == UserRole.SUPER_ADMIN and user_in.role == UserRole.ADMIN:
        # 管理员不设上级（与现有"超管建 admin 时 parent=None"行为一致）
        parent_admin_id = None

    # #5-C: parent 指向的用户必须是 admin 或 super_admin（不能指向普通用户）
    if parent_admin_id:
        parent = await db.get(User, parent_admin_id)
        if not parent or parent.role == UserRole.USER:
            raise HTTPException(
                status_code=400,
                detail="parent_admin_id 必须指向管理员或超级管理员",
            )

    db_user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=security.get_password_hash(user_in.password),
        full_name=user_in.full_name,
        department=user_in.department,
        phone=user_in.phone,
        role=user_in.role,
        parent_admin_id=parent_admin_id,
        is_active=user_in.is_active if user_in.is_active is not None else True,
        is_superuser=user_in.is_superuser if user_in.is_superuser is not None else False,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.get("/me", response_model=UserSchema)
async def read_user_me(
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取当前用户信息
    """
    return current_user


@router.put("/me", response_model=UserSchema)
async def update_user_me(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    更新当前用户信息
    """
    update_data = user_in.model_dump(exclude_unset=True)

    # 普通用户不能修改自己的角色和超级管理员状态
    update_data.pop('role', None)
    update_data.pop('is_superuser', None)
    update_data.pop('is_active', None)
    update_data.pop('parent_admin_id', None)

    # 如果更新密码
    if 'password' in update_data and update_data['password']:
        update_data['hashed_password'] = security.get_password_hash(update_data['password'])
    update_data.pop('password', None)

    for field, value in update_data.items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.get("/{user_id}", response_model=UserSchema)
async def read_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取指定用户信息（带RBAC数据隔离）
    """
    # 权限检查
    if not has_permission(current_user, Permission.USER_READ):
        raise HTTPException(status_code=403, detail="权限不足")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 数据范围检查
    if current_user.role == UserRole.ADMIN:
        # 管理员只能查看自己和下辖用户
        subordinate_ids = await get_subordinate_user_ids(db, current_user.id)
        if user_id != current_user.id and user_id not in subordinate_ids:
            raise HTTPException(status_code=403, detail="无权查看此用户信息")
    elif current_user.role == UserRole.USER:
        # 普通用户只能查看自己
        if user_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权查看此用户信息")

    return user


@router.put("/{user_id}", response_model=UserSchema)
async def update_user(
    user_id: str,
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    更新用户信息（带RBAC权限控制）
    """
    # 权限检查
    if not has_permission(current_user, Permission.USER_UPDATE):
        raise HTTPException(status_code=403, detail="权限不足")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 数据范围检查
    if current_user.role == UserRole.ADMIN:
        subordinate_ids = await get_subordinate_user_ids(db, current_user.id)
        if user_id != current_user.id and user_id not in subordinate_ids:
            raise HTTPException(status_code=403, detail="无权修改此用户")
        # 管理员不能修改超级管理员
        if user.role == UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="无权修改超级管理员")
    elif current_user.role == UserRole.USER:
        raise HTTPException(status_code=403, detail="无权修改其他用户")

    update_data = user_in.model_dump(exclude_unset=True)

    # 非超级管理员不能修改角色为超级管理员
    if current_user.role != UserRole.SUPER_ADMIN:
        if update_data.get('role') == UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="无权设置超级管理员角色")

    # 如果更新密码
    if 'password' in update_data and update_data['password']:
        update_data['hashed_password'] = security.get_password_hash(update_data['password'])
    update_data.pop('password', None)

    # #5-C: 杜绝 admin→admin 多层链（与 create_user 同规则）
    final_role = user.role
    if 'role' in update_data:
        final_role = update_data['role']
    if final_role == UserRole.ADMIN:
        # 目标最终是管理员时，禁止设置任何上级
        update_data.pop('parent_admin_id', None)
    elif update_data.get('parent_admin_id'):
        # 明确设置 parent 时，校验其指向的用户是 admin 或 super_admin
        parent = await db.get(User, update_data['parent_admin_id'])
        if not parent or parent.role == UserRole.USER:
            raise HTTPException(
                status_code=400,
                detail="parent_admin_id 必须指向管理员或超级管理员",
            )

    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    删除用户及其关联数据（带RBAC权限控制）
    """
    # 权限检查
    if not has_permission(current_user, Permission.USER_DELETE):
        raise HTTPException(status_code=403, detail="权限不足")

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账户")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不能删除超级管理员
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="超级管理员不可删除")

    # 数据范围检查
    if current_user.role == UserRole.ADMIN:
        subordinate_ids = await get_subordinate_user_ids(db, current_user.id)
        if user_id not in subordinate_ids:
            raise HTTPException(status_code=403, detail="无权删除此用户")
    elif current_user.role == UserRole.USER:
        raise HTTPException(status_code=403, detail="无权删除用户")

    # 级联删除关联数据（按依赖顺序）
    # 1. 删除审计问题（按 task_id 和 resolved_by）
    subq = select(AuditTask.id).where(AuditTask.created_by == user_id)
    result = await db.execute(subq)
    task_ids = [row[0] for row in result.all()]
    if task_ids:
        await db.execute(delete(AuditIssue).where(AuditIssue.task_id.in_(task_ids)))
    await db.execute(delete(AuditIssue).where(AuditIssue.resolved_by == user_id))
    # 2. 删除审计任务
    await db.execute(delete(AuditTask).where(AuditTask.created_by == user_id))
    # 3. 删除 Agent 任务
    await db.execute(delete(AgentTask).where(AgentTask.created_by == user_id))
    # 4. 删除即时分析
    await db.execute(delete(InstantAnalysis).where(InstantAnalysis.user_id == user_id))
    # 5. 删除规则集
    await db.execute(delete(AuditRuleSet).where(AuditRuleSet.created_by == user_id))
    # 6. 删除提示模板
    await db.execute(delete(PromptTemplate).where(PromptTemplate.created_by == user_id))
    # 7. 删除项目成员
    await db.execute(delete(ProjectMember).where(ProjectMember.user_id == user_id))
    # 8. 删除项目
    await db.execute(delete(Project).where(Project.owner_id == user_id))
    # 9. 删除用户配置
    await db.execute(delete(UserConfig).where(UserConfig.user_id == user_id))

    # 最后删除用户
    await db.delete(user)
    await db.commit()
    return {"message": "用户已删除"}


@router.post("/{user_id}/toggle-status", response_model=UserSchema)
async def toggle_user_status(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    切换用户状态（启用/禁用，带RBAC权限控制）
    """
    # 权限检查
    if not has_permission(current_user, Permission.USER_UPDATE):
        raise HTTPException(status_code=403, detail="权限不足")

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能禁用自己的账户")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不能禁用超级管理员
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="不能禁用超级管理员")

    # 数据范围检查
    if current_user.role == UserRole.ADMIN:
        subordinate_ids = await get_subordinate_user_ids(db, current_user.id)
        if user_id not in subordinate_ids:
            raise HTTPException(status_code=403, detail="无权修改此用户状态")
    elif current_user.role == UserRole.USER:
        raise HTTPException(status_code=403, detail="无权修改用户状态")

    user.is_active = not user.is_active
    await db.commit()
    await db.refresh(user)
    return user
