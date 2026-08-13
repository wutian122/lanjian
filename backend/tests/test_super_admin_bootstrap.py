"""
P0-4: 超级管理员默认密码修复测试

保护点：
- SUPERADMIN_PASSWORD 未设置 —— create_super_admin 返回 None，不写入数据库
- SUPERADMIN_PASSWORD 太弱 —— 同样返回 None
- SUPERADMIN_PASSWORD 强 —— 创建成功，is_first_login=True
- 已存在超管 + SUPERADMIN_PASSWORD 是新密码 —— **不覆盖**旧密码
"""
import importlib

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def db():
    """
    伪造 AsyncSession：只需要 execute + scalars().first() + add + flush。
    """
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


def _empty_result():
    """让 db.execute(...) 返回一个 first() 为 None 的伪结果。"""
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = None
    result.scalars.return_value = scalars
    return result


def _existing_result(existing_user):
    """让 db.execute(...) 返回一个 first() 为 existing_user 的伪结果。"""
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = existing_user
    result.scalars.return_value = scalars
    return result


def _reload_init_db(monkeypatch, *, password=None):
    """
    重新加载 app.db.init_db，让其在导入时读取到我们设定的 SUPERADMIN_PASSWORD。
    """
    monkeypatch.delenv("SUPERADMIN_PASSWORD", raising=False)
    if password is not None:
        monkeypatch.setenv("SUPERADMIN_PASSWORD", password)
    import app.db.init_db as mod
    importlib.reload(mod)
    return mod


class TestSuperAdminBootstrap:
    async def test_skips_when_password_missing(self, db, monkeypatch):
        mod = _reload_init_db(monkeypatch, password=None)
        db.execute.return_value = _empty_result()
        result = await mod.create_super_admin(db)
        assert result is None
        db.add.assert_not_called() if hasattr(db.add, "assert_not_called") else None
        # add 只有在写入时才会被调用
        assert not any(call.args and call.args[0].__class__.__name__ == "User" for call in db.add.call_args_list)

    async def test_skips_when_password_too_weak(self, db, monkeypatch):
        # 弱密码：无大写 + 无特殊字符 + 太短
        mod = _reload_init_db(monkeypatch, password="123456789")
        db.execute.return_value = _empty_result()
        result = await mod.create_super_admin(db)
        assert result is None
        assert not any(call.args and call.args[0].__class__.__name__ == "User" for call in db.add.call_args_list)

    async def test_creates_when_password_strong(self, db, monkeypatch):
        mod = _reload_init_db(monkeypatch, password="Strong!Pass2026#Long")
        db.execute.return_value = _empty_result()
        result = await mod.create_super_admin(db)
        assert result is not None
        assert result.is_first_login is True
        assert result.is_superuser is True
        # add 被调用过一次
        assert db.add.call_count == 1

    async def test_does_not_overwrite_existing_superadmin_password(self, db, monkeypatch):
        """
        存在超管时不再覆盖密码 —— 覆盖逻辑等于把管理员改过的密码每次重启拉回环境值。
        """
        mod = _reload_init_db(monkeypatch, password="Strong!Pass2026#Long")
        existing = MagicMock()
        existing.username = "admin"
        existing.hashed_password = "old-hash"
        db.execute.return_value = _existing_result(existing)
        result = await mod.create_super_admin(db)
        assert result is existing
        # 关键：旧 hash 未被写入
        assert existing.hashed_password == "old-hash"


# pytest-asyncio marker
pytestmark = pytest.mark.asyncio
