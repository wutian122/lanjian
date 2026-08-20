"""
security-hardening-2026-08 第一批：认证与越权修复测试（TDD RED→GREEN）

A1: GET /config/defaults 必须要求认证，且敏感字段一律脱敏（空串 + {field}Set 标记）
A2: 刷新令牌不可当访问令牌；登出后 access/refresh 均立即失效（jti 黑名单）
A3: GET /projects/{id}/members 需项目访问权限（assert_can_access_project）
A4: 系统规则集（is_system）的启用/切换仅 super_admin 可操作
"""
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from app.core.config import settings
from app.core.rbac import UserRole
from app.core.security import create_access_token, create_refresh_token


@pytest.fixture(autouse=True)
def _set_secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    import app.core.config as cfg_mod
    importlib.reload(cfg_mod)


# ============ A1: /config/defaults 认证 + 脱敏 ============

def test_config_defaults_route_requires_auth():
    """路由必须声明 get_current_user 依赖（回归保护：不许再删）。"""
    from app.api import deps as deps_mod
    from app.api.v1.endpoints import config as cfg

    route = next(r for r in cfg.router.routes if getattr(r, "path", "") == "/defaults")
    dependant = getattr(route, "dependant", None)
    assert dependant is not None, "/defaults 路由没有 dependant"

    calls: list = []

    def walk(d):
        if getattr(d, "call", None):
            calls.append(d.call)
        for sub in getattr(d, "dependencies", []) or []:
            walk(sub)

    walk(dependant)
    assert deps_mod.get_current_user in calls, "GET /config/defaults 必须要求认证"


def test_config_defaults_masks_sensitive_fields():
    """即使带认证访问，敏感字段也只返回空串 + Set 标记，绝不返回明文。"""
    from app.api.v1.endpoints import config as cfg

    mock_user = SimpleNamespace(id="u1", role=UserRole.USER, is_superuser=False)
    result = asyncio.run(cfg.get_default_config_endpoint(current_user=mock_user))

    for f in cfg.SENSITIVE_LLM_FIELDS:
        assert result["llmConfig"][f] == "", f"{f} 必须脱敏"
        assert f"{f}Set" in result["llmConfig"], f"缺少 {f}Set 标记"
    for f in ("githubToken", "gitlabToken"):
        assert result["otherConfig"][f] == "", f"{f} 必须脱敏"
        assert f"{f}Set" in result["otherConfig"]

    # 非敏感默认值仍正常返回（供前端表单预填）
    assert "llmProvider" in result["llmConfig"]
    assert "llmBaseUrl" in result["llmConfig"]


# ============ A2: 令牌类型校验 + 登出黑名单 ============

def _mock_db_user(user):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = user
    db.execute.return_value = result
    return db


def _no_blacklist_redis():
    redis = AsyncMock()
    redis.get.return_value = None
    return redis


def test_refresh_token_rejected_as_access_token():
    """刷新令牌不能当访问令牌使用（A2 核心）。"""
    from app.api.deps import get_current_user

    token = create_refresh_token("u1")
    db = _mock_db_user(SimpleNamespace(id="u1", is_active=True))
    with patch("app.api.deps.get_redis", new=AsyncMock(return_value=_no_blacklist_redis())):
        with pytest.raises(HTTPException) as e:
            asyncio.run(get_current_user(db=db, token=token))
    assert e.value.status_code == 401


def test_access_token_accepted():
    from app.api.deps import get_current_user

    token = create_access_token("u1")
    user = SimpleNamespace(id="u1", is_active=True)
    db = _mock_db_user(user)
    with patch("app.api.deps.get_redis", new=AsyncMock(return_value=_no_blacklist_redis())):
        u = asyncio.run(get_current_user(db=db, token=token))
    assert u is user


def test_blacklisted_access_token_rejected():
    """登出后 access token 立即失效（Redis 黑名单命中）。"""
    from app.api.deps import get_current_user

    token = create_access_token("u1")
    db = _mock_db_user(SimpleNamespace(id="u1", is_active=True))
    redis = AsyncMock()
    redis.get.return_value = b"1"
    with patch("app.api.deps.get_redis", new=AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as e:
            asyncio.run(get_current_user(db=db, token=token))
    assert e.value.status_code == 401


def test_blacklist_check_fails_open_on_redis_error():
    """Redis 不可用时认证不整体挂掉（fail-open，仅登出强失效降级）。"""
    from app.api.deps import get_current_user

    token = create_access_token("u1")
    user = SimpleNamespace(id="u1", is_active=True)
    db = _mock_db_user(user)
    with patch("app.api.deps.get_redis", new=AsyncMock(side_effect=RuntimeError("redis down"))):
        u = asyncio.run(get_current_user(db=db, token=token))
    assert u is user


def test_access_token_contains_jti():
    """access token 必须带 jti，否则无法被登出黑名单。"""
    token = create_access_token("u1")
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    assert payload.get("jti")
    assert payload.get("type") == "access"


def test_refresh_blacklisted_rejected():
    """登出后 refresh 端点拒绝发放新令牌。"""
    from app.api.v1.endpoints.auth import RefreshTokenRequest, refresh_token

    token = create_refresh_token("u1")
    req = RefreshTokenRequest(refresh_token=token)
    db = _mock_db_user(SimpleNamespace(id="u1", is_active=True))
    redis = AsyncMock()
    redis.get.return_value = b"1"
    with patch("app.api.v1.endpoints.auth.get_redis", new=AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as e:
            asyncio.run(refresh_token(request=req, db=db))
    assert e.value.status_code == 401


def test_refresh_ok_when_not_blacklisted():
    from app.api.v1.endpoints.auth import RefreshTokenRequest, refresh_token

    token = create_refresh_token("u1")
    req = RefreshTokenRequest(refresh_token=token)
    db = _mock_db_user(SimpleNamespace(id="u1", is_active=True))
    with patch("app.api.v1.endpoints.auth.get_redis", new=AsyncMock(return_value=_no_blacklist_redis())):
        result = asyncio.run(refresh_token(request=req, db=db))
    assert result["access_token"]


def test_logout_blacklists_both_tokens():
    """登出同时拉黑 access 与 refresh 的 jti。"""
    from app.api.v1.endpoints.auth import LogoutRequest, logout

    access = create_access_token("u1")
    refresh = create_refresh_token("u1")
    req = LogoutRequest(refresh_token=refresh, access_token=access)
    fake_redis = AsyncMock()
    with patch("app.api.v1.endpoints.auth.get_redis", new=AsyncMock(return_value=fake_redis)):
        asyncio.run(logout(request=req))

    ap = jwt.decode(access, settings.SECRET_KEY, algorithms=["HS256"])
    rp = jwt.decode(refresh, settings.SECRET_KEY, algorithms=["HS256"])
    called = {c.args[0] for c in fake_redis.setex.await_args_list}
    assert f"logout:blacklist:{ap['jti']}" in called
    assert f"logout:blacklist:{rp['jti']}" in called


# ============ A3: 项目成员列表越权 ============

def test_get_project_members_denies_non_member():
    """非项目成员读取成员列表必须被拒（assert_can_access_project → 404 不泄露存在性）。"""
    from app.api.v1.endpoints.members import get_project_members

    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id="p1", owner_id="owner1")
    user = SimpleNamespace(id="u2", role=UserRole.USER)
    with pytest.raises(HTTPException) as e:
        asyncio.run(get_project_members(project_id="p1", db=db, current_user=user))
    assert e.value.status_code == 404


def test_get_project_members_owner_ok():
    from app.api.v1.endpoints.members import get_project_members

    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id="p1", owner_id="owner1")
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["m1"]
    db.execute.return_value = result
    user = SimpleNamespace(id="owner1", role=UserRole.USER)
    out = asyncio.run(get_project_members(project_id="p1", db=db, current_user=user))
    assert out == ["m1"]


# ============ A4: 系统规则集仅 super_admin 可操作 ============

def test_update_system_rule_set_denied_for_user():
    """普通用户不能改系统规则集的启用状态。"""
    from app.api.v1.endpoints.rules import AuditRuleSetUpdate, update_rule_set

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(
        id="rs1", is_system=True, created_by="admin", rules=[]
    )
    db.execute.return_value = result
    user = SimpleNamespace(id="u1", role=UserRole.USER)
    with pytest.raises(HTTPException) as e:
        asyncio.run(update_rule_set(
            rule_set_id="rs1",
            rule_set_in=AuditRuleSetUpdate(is_active=True),
            db=db,
            current_user=user,
        ))
    assert e.value.status_code == 403


def test_update_system_rule_set_denied_for_admin():
    from app.api.v1.endpoints.rules import AuditRuleSetUpdate, update_rule_set

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(
        id="rs1", is_system=True, created_by="admin", rules=[]
    )
    db.execute.return_value = result
    user = SimpleNamespace(id="a1", role=UserRole.ADMIN)
    with pytest.raises(HTTPException) as e:
        asyncio.run(update_rule_set(
            rule_set_id="rs1",
            rule_set_in=AuditRuleSetUpdate(is_active=True),
            db=db,
            current_user=user,
        ))
    assert e.value.status_code == 403


def test_toggle_system_rule_denied_for_user():
    from app.api.v1.endpoints.rules import toggle_rule

    db = AsyncMock()
    db.execute.side_effect = [
        _result(SimpleNamespace(id="rs1", is_system=True, created_by="admin")),
        _result(SimpleNamespace(id="r1", rule_set_id="rs1", enabled=True)),
    ]
    user = SimpleNamespace(id="u1", role=UserRole.USER)
    with pytest.raises(HTTPException) as e:
        asyncio.run(toggle_rule(rule_set_id="rs1", rule_id="r1", db=db, current_user=user))
    assert e.value.status_code == 403


def test_toggle_system_rule_allowed_for_super_admin():
    from app.api.v1.endpoints.rules import toggle_rule

    rule = SimpleNamespace(id="r1", rule_set_id="rs1", enabled=True)
    db = AsyncMock()
    db.execute.side_effect = [
        _result(SimpleNamespace(id="rs1", is_system=True, created_by="admin")),
        _result(rule),
    ]
    user = SimpleNamespace(id="sa1", role=UserRole.SUPER_ADMIN, is_superuser=True)
    out = asyncio.run(toggle_rule(rule_set_id="rs1", rule_id="r1", db=db, current_user=user))
    assert out["enabled"] is False  # 已翻转


def _result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r
