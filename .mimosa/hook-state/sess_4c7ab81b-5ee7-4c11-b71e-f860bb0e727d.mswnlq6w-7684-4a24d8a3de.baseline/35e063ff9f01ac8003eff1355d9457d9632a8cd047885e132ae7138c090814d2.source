"""
P2-1: assert_can_access_project 单元测试
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.rbac import (
    assert_can_access_project,
    can_access_project,
    ProjectAccessDenied,
)
from app.models.user import UserRole


def _user(user_id: str, role: str) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=role)


def _project(project_id: str, owner_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=project_id, owner_id=owner_id)


class TestCanAccessProject:
    def test_owner_can_access(self):
        u = _user("u1", UserRole.USER)
        p = _project("p1", "u1")
        assert can_access_project(u, p) is True

    def test_super_admin_can_access_any(self):
        u = _user("admin", UserRole.SUPER_ADMIN)
        p = _project("p1", "someone_else")
        assert can_access_project(u, p) is True

    def test_admin_cannot_access_other_user_project(self):
        """
        ADMIN 是租户/部门管理员，不等于超管。data-scope 的下辖判定属于 list 场景，
        单资源访问按 owner 判即可，避免越权。
        """
        u = _user("admin_dept", UserRole.ADMIN)
        p = _project("p1", "u1")
        assert can_access_project(u, p) is False

    def test_regular_user_cannot_access_other(self):
        u = _user("u2", UserRole.USER)
        p = _project("p1", "u1")
        assert can_access_project(u, p) is False

    def test_none_user(self):
        p = _project("p1", "u1")
        assert can_access_project(None, p) is False

    def test_none_project(self):
        u = _user("u1", UserRole.USER)
        assert can_access_project(u, None) is False


class TestAssertCanAccessProject:
    def test_owner_no_raise(self):
        u = _user("u1", UserRole.USER)
        p = _project("p1", "u1")
        assert_can_access_project(u, p)  # 不抛

    def test_denies_with_404(self):
        u = _user("u2", UserRole.USER)
        p = _project("p1", "u1")
        with pytest.raises(ProjectAccessDenied) as exc:
            assert_can_access_project(u, p)
        assert exc.value.status_code == 404
        # 404 而非 403 —— 不泄露资源存在性
        assert "无权访问" in exc.value.detail or "不存在" in exc.value.detail

    def test_none_project_treated_as_not_found(self):
        u = _user("u1", UserRole.USER)
        with pytest.raises(ProjectAccessDenied) as exc:
            assert_can_access_project(u, None)
        assert exc.value.status_code == 404

    def test_project_access_denied_is_http_exception(self):
        """FastAPI 端点抛出时能被自动转 404 响应。"""
        assert issubclass(ProjectAccessDenied, HTTPException)
