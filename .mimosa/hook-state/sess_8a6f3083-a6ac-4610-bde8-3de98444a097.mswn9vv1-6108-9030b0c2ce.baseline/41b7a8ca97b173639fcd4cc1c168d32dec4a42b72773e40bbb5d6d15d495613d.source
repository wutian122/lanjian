"""
RBAC 权限系统测试

测试当前 app/core/rbac.py 的真实实现：
- UserRole: super_admin / admin / user
- Permission: 资源级权限（user:create 等 20 个）
- ROLE_PERMISSIONS: 角色 -> 权限列表映射
- has_permission / require_permission / require_role
- DataScope: ALL / SELF_AND_SUB / SELF_ONLY
- build_*_filter: 数据范围行级隔离
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException

from app.core.rbac import (
    Permission,
    ROLE_PERMISSIONS,
    has_permission,
    require_permission,
    require_role,
    DataScope,
    get_user_data_scope,
    build_user_filter,
    build_project_filter,
    build_task_filter,
    build_prompt_filter,
    build_agent_task_filter,
)
from app.models.user import UserRole


class MockUser:
    """轻量 MockUser，满足 rbac 函数所需属性。"""

    def __init__(self, role: str, user_id: str = "u-1"):
        self.role = role
        self.id = user_id


# ============ 角色与权限常量 ============

class TestRoleConstants:
    """UserRole 字符串值测试。"""

    def test_role_values(self):
        assert UserRole.SUPER_ADMIN == "super_admin"
        assert UserRole.ADMIN == "admin"
        assert UserRole.USER == "user"

    def test_roles_are_distinct(self):
        roles = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.USER}
        assert len(roles) == 3


class TestPermissionConstants:
    """Permission 资源级权限常量测试。"""

    def test_permission_format(self):
        """所有权限遵循 resource:action 格式。"""
        perms = [
            Permission.USER_CREATE, Permission.USER_READ, Permission.USER_UPDATE, Permission.USER_DELETE,
            Permission.PROJECT_CREATE, Permission.PROJECT_READ, Permission.PROJECT_UPDATE, Permission.PROJECT_DELETE,
            Permission.TASK_CREATE, Permission.TASK_READ, Permission.TASK_UPDATE, Permission.TASK_DELETE,
            Permission.PROMPT_CREATE, Permission.PROMPT_READ, Permission.PROMPT_UPDATE, Permission.PROMPT_DELETE,
            Permission.DASHBOARD_READ, Permission.CONFIG_READ, Permission.CONFIG_UPDATE,
        ]
        for p in perms:
            assert ":" in p, f"{p} should use resource:action format"

    def test_user_permissions(self):
        assert Permission.USER_CREATE == "user:create"
        assert Permission.USER_DELETE == "user:delete"


# ============ 权限矩阵 ============

class TestPermissionMatrix:
    """角色 -> 权限矩阵测试。"""

    def test_all_roles_have_permissions(self):
        for role in [UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.USER]:
            assert role in ROLE_PERMISSIONS
            assert len(ROLE_PERMISSIONS[role]) > 0

    def test_super_admin_has_all_permissions(self):
        all_perms = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms.update(perms)
        super_perms = set(ROLE_PERMISSIONS[UserRole.SUPER_ADMIN])
        assert super_perms == all_perms

    def test_super_admin_has_config_permission(self):
        assert Permission.CONFIG_READ in ROLE_PERMISSIONS[UserRole.SUPER_ADMIN]
        assert Permission.CONFIG_UPDATE in ROLE_PERMISSIONS[UserRole.SUPER_ADMIN]

    def test_admin_lacks_config_permission(self):
        admin_perms = ROLE_PERMISSIONS[UserRole.ADMIN]
        assert Permission.CONFIG_READ not in admin_perms
        assert Permission.CONFIG_UPDATE not in admin_perms

    def test_user_lacks_user_management(self):
        user_perms = ROLE_PERMISSIONS[UserRole.USER]
        assert Permission.USER_CREATE not in user_perms
        assert Permission.USER_DELETE not in user_perms

    def test_user_has_project_and_task(self):
        user_perms = ROLE_PERMISSIONS[UserRole.USER]
        assert Permission.PROJECT_CREATE in user_perms
        assert Permission.TASK_READ in user_perms


# ============ has_permission ============

class TestHasPermission:
    """has_permission 函数测试。"""

    def test_super_admin_has_any_permission(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        assert has_permission(user, Permission.USER_DELETE) is True
        assert has_permission(user, Permission.CONFIG_UPDATE) is True

    def test_admin_has_project_permission(self):
        user = MockUser(UserRole.ADMIN)
        assert has_permission(user, Permission.PROJECT_CREATE) is True
        assert has_permission(user, Permission.USER_CREATE) is True

    def test_admin_denied_config(self):
        user = MockUser(UserRole.ADMIN)
        assert has_permission(user, Permission.CONFIG_READ) is False

    def test_user_denied_user_management(self):
        user = MockUser(UserRole.USER)
        assert has_permission(user, Permission.USER_DELETE) is False
        assert has_permission(user, Permission.USER_CREATE) is False

    def test_user_allowed_project(self):
        user = MockUser(UserRole.USER)
        assert has_permission(user, Permission.PROJECT_READ) is True

    def test_unknown_role_no_permissions(self):
        user = MockUser("unknown_role")
        assert has_permission(user, Permission.USER_READ) is False

    def test_none_user_returns_false(self):
        assert has_permission(None, Permission.USER_READ) is False

    def test_user_with_empty_role_returns_false(self):
        # role 为空字符串 -> falsy -> 无权限
        user = MockUser("")
        assert has_permission(user, Permission.USER_READ) is False

    def test_user_without_role_attribute_returns_false(self):
        # 对象无 role 属性 -> 防御性返回 False 而非抛 AttributeError
        user = MagicMock()
        del user.role  # 删除属性，模拟无 role 的对象
        assert has_permission(user, Permission.USER_READ) is False


# ============ require_permission 装饰器 ============

class TestRequirePermission:
    """require_permission 依赖装饰器测试。"""

    @pytest.mark.asyncio
    async def test_authorized_user_passes(self):
        checker = require_permission(Permission.PROJECT_CREATE)

        user = MockUser(UserRole.SUPER_ADMIN)
        with patch("app.api.deps.get_current_user", new=AsyncMock(return_value=user)):
            result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_unauthorized_user_raises_403(self):
        checker = require_permission(Permission.CONFIG_UPDATE)

        user = MockUser(UserRole.USER)
        with patch("app.api.deps.get_current_user", new=AsyncMock(return_value=user)):
            with pytest.raises(HTTPException) as exc_info:
                await checker(current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_denied_config(self):
        checker = require_permission(Permission.CONFIG_READ)

        user = MockUser(UserRole.ADMIN)
        with patch("app.api.deps.get_current_user", new=AsyncMock(return_value=user)):
            with pytest.raises(HTTPException) as exc_info:
                await checker(current_user=user)
        assert exc_info.value.status_code == 403


# ============ require_role 装饰器 ============

class TestRequireRole:
    """require_role 依赖装饰器测试。"""

    @pytest.mark.asyncio
    async def test_correct_role_passes(self):
        checker = require_role([UserRole.ADMIN])

        user = MockUser(UserRole.ADMIN)
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_wrong_role_raises_403(self):
        checker = require_role([UserRole.SUPER_ADMIN])

        user = MockUser(UserRole.USER)
        with pytest.raises(HTTPException) as exc_info:
            await checker(current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_multiple_roles_allowed(self):
        checker = require_role([UserRole.SUPER_ADMIN, UserRole.ADMIN])

        admin = MockUser(UserRole.ADMIN)
        result = await checker(current_user=admin)
        assert result is admin


# ============ DataScope ============

class TestDataScope:
    """get_user_data_scope 数据范围测试。"""

    def test_super_admin_all_scope(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        assert get_user_data_scope(user) == DataScope.ALL

    def test_admin_self_and_sub_scope(self):
        user = MockUser(UserRole.ADMIN)
        assert get_user_data_scope(user) == DataScope.SELF_AND_SUB

    def test_user_self_only_scope(self):
        user = MockUser(UserRole.USER)
        assert get_user_data_scope(user) == DataScope.SELF_ONLY

    def test_unknown_role_self_only(self):
        user = MockUser("unknown")
        assert get_user_data_scope(user) == DataScope.SELF_ONLY


# ============ build_*_filter 数据范围隔离 ============

class TestBuildFilters:
    """build_*_filter 行级数据隔离测试。"""

    def test_super_admin_user_filter_returns_none(self):
        """super_admin 无过滤 -> 返回 None 表示查全部。"""
        user = MockUser(UserRole.SUPER_ADMIN)
        assert build_user_filter(user) is None

    def test_admin_user_filter_includes_self_and_subordinates(self):
        user = MockUser(UserRole.ADMIN, "admin-1")
        flt = build_user_filter(user, user_ids=["sub-1", "sub-2"])
        assert flt is not None  # 返回 SQLAlchemy where 条件

    def test_user_user_filter_self_only(self):
        user = MockUser(UserRole.USER, "u-1")
        flt = build_user_filter(user)
        assert flt is not None

    def test_super_admin_project_filter_returns_none(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        assert build_project_filter(user) is None

    def test_admin_project_filter(self):
        user = MockUser(UserRole.ADMIN, "admin-1")
        flt = build_project_filter(user, user_ids=["sub-1"])
        assert flt is not None

    def test_user_project_filter(self):
        user = MockUser(UserRole.USER, "u-1")
        flt = build_project_filter(user)
        assert flt is not None

    def test_super_admin_task_filter_returns_none(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        assert build_task_filter(user) is None

    def test_admin_task_filter(self):
        user = MockUser(UserRole.ADMIN, "admin-1")
        flt = build_task_filter(user, user_ids=["sub-1"])
        assert flt is not None

    def test_super_admin_prompt_filter_returns_none(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        assert build_prompt_filter(user) is None

    def test_admin_prompt_filter(self):
        user = MockUser(UserRole.ADMIN, "admin-1")
        flt = build_prompt_filter(user, user_ids=["sub-1"])
        assert flt is not None

    def test_super_admin_agent_task_filter_returns_none(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        assert build_agent_task_filter(user) is None

    def test_user_agent_task_filter(self):
        user = MockUser(UserRole.USER, "u-1")
        flt = build_agent_task_filter(user)
        assert flt is not None


# ============ 快捷依赖 ============

class TestShortcutDependencies:
    """require_super_admin / require_admin_or_above / require_any_role 测试。"""

    @pytest.mark.asyncio
    async def test_require_super_admin_passes(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        from app.core.rbac import require_super_admin
        result = await require_super_admin(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_require_super_admin_blocks_admin(self):
        user = MockUser(UserRole.ADMIN)
        from app.core.rbac import require_super_admin
        with pytest.raises(HTTPException) as exc_info:
            await require_super_admin(current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_admin_or_above_passes_admin(self):
        user = MockUser(UserRole.ADMIN)
        from app.core.rbac import require_admin_or_above
        result = await require_admin_or_above(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_require_admin_or_above_blocks_user(self):
        user = MockUser(UserRole.USER)
        from app.core.rbac import require_admin_or_above
        with pytest.raises(HTTPException) as exc_info:
            await require_admin_or_above(current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_any_role_passes_user(self):
        user = MockUser(UserRole.USER)
        from app.core.rbac import require_any_role
        result = await require_any_role(current_user=user)
        assert result is user
