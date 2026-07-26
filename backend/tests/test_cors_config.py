"""
P0-3: CORS 白名单测试

保护点：
- 未配置 CORS_ALLOWED_ORIGINS 时禁止跨源凭证请求（origins=[], credentials=False）
- 配置多个 origin 时仅这些被允许，其他被拒
- 通配符 * 不再启用
"""
import importlib

import pytest
from fastapi.testclient import TestClient


def _reload_app(monkeypatch, cors_value: str, secret_key: str = "x" * 48):
    """Reload app.main with a fresh CORS_ALLOWED_ORIGINS and return TestClient."""
    monkeypatch.setenv("SECRET_KEY", secret_key)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", cors_value)
    # 每次都重载 config + main，让新的 env 生效
    import app.core.config as cfg_mod
    importlib.reload(cfg_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app), main_mod


class TestCorsAllowlist:
    def test_empty_origins_disables_credentials(self, monkeypatch):
        client, main_mod = _reload_app(monkeypatch, "")
        # preflight 一个未在名单上的 origin，期望不返回 allow-origin 头
        r = client.options(
            "/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Starlette CORSMiddleware 对空白名单会不返回 CORS 响应头
        assert r.headers.get("access-control-allow-origin") is None
        assert r.headers.get("access-control-allow-credentials") is None

    def test_configured_origin_is_allowed_with_credentials(self, monkeypatch):
        client, main_mod = _reload_app(
            monkeypatch,
            "http://frontend-host-a.example.com,http://frontend-host-b.example.com",
        )
        r = client.options(
            "/health",
            headers={
                "Origin": "http://frontend-host-a.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "http://frontend-host-a.example.com"
        assert r.headers.get("access-control-allow-credentials") == "true"

    def test_unlisted_origin_is_rejected(self, monkeypatch):
        client, main_mod = _reload_app(monkeypatch, "http://frontend-host-a.example.com")
        r = client.options(
            "/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") is None

    def test_wildcard_is_not_used(self, monkeypatch):
        """回归保护：不允许再有人偷偷改回 allow_origins=['*']。"""
        client, main_mod = _reload_app(monkeypatch, "http://frontend-host-a.example.com")
        # 遍历 middleware stack，确保没有 allow_origins=['*']
        for mw in main_mod.app.user_middleware:
            opts = getattr(mw, "options", None) or getattr(mw, "kwargs", {})
            if "allow_origins" in opts:
                assert "*" not in opts["allow_origins"], (
                    f"CORS wildcard re-introduced: {opts['allow_origins']}"
                )
