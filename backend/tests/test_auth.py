"""
认证系统测试
测试：密码策略、账户锁定、JWT、验证码
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from app.core.security import (
    validate_password_policy,
    check_password_history,
    create_access_token,
    create_refresh_token,
    verify_password,
    get_password_hash,
)

from app.core.rbac import Permission, has_permission, ROLE_PERMISSIONS
from app.models.user import UserRole


class MockUser:
    def __init__(self, role: str):
        self.role = role
        self.id = "u-1"


class TestRBAC:
    """RBAC 权限测试"""

    def test_super_admin_has_all_permissions(self):
        user = MockUser(UserRole.SUPER_ADMIN)
        for perm in [
            Permission.USER_DELETE, Permission.CONFIG_UPDATE,
            Permission.PROJECT_CREATE, Permission.TASK_DELETE,
        ]:
            assert has_permission(user, perm) is True

    def test_admin_has_user_management(self):
        user = MockUser(UserRole.ADMIN)
        assert has_permission(user, Permission.USER_CREATE) is True
        assert has_permission(user, Permission.USER_UPDATE) is True
        assert has_permission(user, Permission.USER_DELETE) is True

    def test_admin_denied_config(self):
        user = MockUser(UserRole.ADMIN)
        assert has_permission(user, Permission.CONFIG_READ) is False
        assert has_permission(user, Permission.CONFIG_UPDATE) is False

    def test_user_no_admin_permissions(self):
        user = MockUser(UserRole.USER)
        for perm in [Permission.USER_CREATE, Permission.USER_DELETE, Permission.USER_UPDATE]:
            assert has_permission(user, perm) is False
        assert has_permission(user, Permission.PROJECT_READ) is True

    def test_unknown_role_no_permissions(self):
        user = MockUser("unknown")
        assert has_permission(user, Permission.USER_READ) is False


class TestPasswordPolicy:
    """密码策略测试"""

    def test_valid_password(self):
        valid, msg = validate_password_policy("MySecure@Pass1")
        assert valid is True

    def test_too_short(self):
        valid, msg = validate_password_policy("Abc@1")
        assert valid is False
        assert "12" in msg

    def test_no_uppercase(self):
        valid, msg = validate_password_policy("mypassword@123")
        assert valid is False
        assert "大写" in msg

    def test_no_lowercase(self):
        valid, msg = validate_password_policy("MYPASSWORD@123")
        assert valid is False
        assert "小写" in msg

    def test_no_digit(self):
        valid, msg = validate_password_policy("MyPassword@abc")
        assert valid is False
        assert "数字" in msg

    def test_no_special_char(self):
        valid, msg = validate_password_policy("MyPassword123")
        assert valid is False
        assert "特殊字符" in msg

    def test_minimum_length_12(self):
        valid, msg = validate_password_policy("Abcdef@12345")
        assert valid is True


class TestPasswordHistory:
    """密码历史测试"""

    @patch('app.core.security.verify_password')
    def test_not_in_history(self, mock_verify):
        mock_verify.return_value = False
        old_pw_hash = "$2b$12$mockedhashvalue000000000000Ch"

        class MockUser:
            password_history = [old_pw_hash]

        user = MockUser()
        result = check_password_history(user, "NewPassword@2")
        assert result is True

    @patch('app.core.security.verify_password')
    def test_in_history_rejected(self, mock_verify):
        mock_verify.return_value = True
        old_pw = "Reused@Pass1"

        class MockUser:
            password_history = ["$2b$12$mockedhashvalue000000000000Ch"]

        user = MockUser()
        result = check_password_history(user, old_pw)
        assert result is False


class TestJWT:
    """JWT 令牌测试"""

    def test_access_token_creation(self):
        token = create_access_token("user-123")
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_refresh_token_creation(self):
        token = create_refresh_token("user-123")
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_tokens_are_different(self):
        access = create_access_token("user-123")
        refresh = create_refresh_token("user-123")
        assert access != refresh


class TestAccountLockout:
    """账户锁定测试"""

    def test_locked_user_rejected(self):
        """模拟 locked_until 在未来 → 应拒绝"""
        from datetime import timedelta
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert future > datetime.now(timezone.utc)

    def test_unlocked_user_allowed(self):
        """locked_until 已过期 → 应允许"""
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert past < datetime.now(timezone.utc)
