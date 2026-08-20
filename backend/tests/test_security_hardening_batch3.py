"""
security-hardening-2026-08 第三批：C2 LLM Base URL SSRF 校验 + traceback 脱敏
"""
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _set_secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    import app.core.config as cfg_mod
    importlib.reload(cfg_mod)


class TestValidateLlmBaseUrl:
    def test_rejects_non_http_scheme(self):
        from app.api.v1.endpoints.config import _validate_llm_base_url

        with pytest.raises(HTTPException) as e:
            _validate_llm_base_url("ftp://example.com/v1")
        assert e.value.status_code == 400

    def test_rejects_loopback_ip(self):
        from app.api.v1.endpoints.config import _validate_llm_base_url

        with pytest.raises(HTTPException) as e:
            _validate_llm_base_url("http://127.0.0.1:9999/v1")
        assert e.value.status_code == 400

    def test_rejects_private_and_reserved_ips(self):
        from app.api.v1.endpoints.config import _validate_llm_base_url

        for url in ("http://192.168.1.10/v1", "http://10.0.0.5/v1", "http://172.16.0.1/v1"):
            with pytest.raises(HTTPException):
                _validate_llm_base_url(url)

    def test_rejects_internal_service_names(self):
        from app.api.v1.endpoints.config import _validate_llm_base_url

        for host in ("localhost", "db", "redis", "backend", "frontend", "sandbox"):
            with pytest.raises(HTTPException):
                _validate_llm_base_url(f"http://{host}:8000/v1")

    def test_rejects_unresolvable_hostname(self):
        from app.api.v1.endpoints.config import _validate_llm_base_url

        with pytest.raises(HTTPException):
            _validate_llm_base_url("http://nonexistent-host.invalid.example/v1")

    def test_allows_public_ip(self):
        from app.api.v1.endpoints.config import _validate_llm_base_url

        assert _validate_llm_base_url("https://1.1.1.1/v1") == "https://1.1.1.1/v1"

    def test_allows_public_hostname(self):
        from app.api.v1.endpoints.config import _validate_llm_base_url

        assert _validate_llm_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"

    def test_allowlist_bypasses_private_check(self, monkeypatch):
        monkeypatch.setenv("LLM_TEST_ALLOWED_HOSTS", "internal-proxy")
        import app.core.config as cfg_mod
        importlib.reload(cfg_mod)
        import app.api.v1.endpoints.config as cfg
        importlib.reload(cfg)

        assert (
            cfg._validate_llm_base_url("http://internal-proxy:8000/v1")
            == "http://internal-proxy:8000/v1"
        )


async def test_llm_test_error_response_has_no_traceback():
    """LLM 测试失败时不再把 traceback 全文回传客户端。"""
    from app.api.v1.endpoints.config import LLMTestRequest, test_llm_connection

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = None  # 用户无已存配置
    db.execute.return_value = db_result

    req = LLMTestRequest(
        provider="openai",
        apiKey="",
        model=None,
        baseUrl="https://1.1.1.1/v1",
    )
    with patch("app.services.llm.adapters.LiteLLMAdapter") as Adapter:
        Adapter.return_value.complete = AsyncMock(side_effect=RuntimeError("boom"))
        resp = await test_llm_connection(
            request=req,
            db=db,
            current_user=SimpleNamespace(id="u1"),
        )

    assert resp.success is False
    assert resp.debug is not None
    assert "traceback" not in resp.debug
    assert resp.debug.get("error_type") == "RuntimeError"


# ============ C1: Agent 注册表按任务隔离（并发互不干扰） ============

class TestRegistryTaskScoping:
    def test_clear_task_only_removes_own_subtree(self):
        """清 A 任务不得影响 B 任务的注册树（此前全局 clear 会互相清空）。"""
        from app.services.agent.core.registry import AgentRegistry

        r = AgentRegistry()
        r.register_agent("a-root", "A", "orchestrator", "t")
        r.register_agent("a-child", "A1", "analysis", "t", parent_id="a-root")
        r.register_agent("b-root", "B", "orchestrator", "t")
        r.register_agent("b-child", "B1", "analysis", "t", parent_id="b-root")
        r.bind_task("task-a", "a-root")
        r.bind_task("task-b", "b-root")

        r.clear_task("task-a")

        assert r.get_agent_node("a-root") is None
        assert r.get_agent_node("a-child") is None
        assert r.get_agent_node("b-root") is not None
        assert r.get_agent_node("b-child") is not None

    def test_get_task_tree_returns_only_own_subtree(self):
        """树读取按任务隔离：只返回本任务子树。"""
        from app.services.agent.core.registry import AgentRegistry

        r = AgentRegistry()
        r.register_agent("a-root", "A", "orchestrator", "t")
        r.register_agent("a-child", "A1", "analysis", "t", parent_id="a-root")
        r.register_agent("b-root", "B", "orchestrator", "t")
        r.bind_task("task-a", "a-root")
        r.bind_task("task-b", "b-root")

        tree = r.get_task_tree("task-a")
        assert set(tree["nodes"].keys()) == {"a-root", "a-child"}
        assert tree["root_agent_id"] == "a-root"

    def test_get_task_tree_falls_back_to_global_for_unbound_task(self):
        """未绑定任务回退到全局树（兼容旧数据/旧版本任务）。"""
        from app.services.agent.core.registry import AgentRegistry

        r = AgentRegistry()
        r.register_agent("a-root", "A", "orchestrator", "t")
        tree = r.get_task_tree("unknown-task")
        assert set(tree["nodes"].keys()) == {"a-root"}

    def test_task_statistics_isolated(self):
        """统计按任务隔离。"""
        from app.services.agent.core.registry import AgentRegistry

        r = AgentRegistry()
        r.register_agent("a-root", "A", "orchestrator", "t")
        r.register_agent("b-root", "B", "orchestrator", "t")
        r.bind_task("task-a", "a-root")
        r.bind_task("task-b", "b-root")

        assert r.get_task_statistics("task-a")["total"] == 1
        r.clear_task("task-a")
        assert r.get_task_statistics("task-b")["total"] == 1

    def test_get_agent_tree_subtree_isolated_by_root(self):
        """按根取子树：finish_tool 等只看到自己任务的树。"""
        from app.services.agent.core.registry import AgentRegistry

        r = AgentRegistry()
        r.register_agent("a-root", "A", "orchestrator", "t")
        r.register_agent("a-child", "A1", "analysis", "t", parent_id="a-root")
        r.register_agent("b-root", "B", "orchestrator", "t")
        r.bind_task("task-a", "a-root")
        r.bind_task("task-b", "b-root")

        tree_a = r.get_agent_tree_subtree("a-root")
        assert set(tree_a["nodes"].keys()) == {"a-root", "a-child"}
        tree_b = r.get_agent_tree_subtree("b-root")
        assert set(tree_b["nodes"].keys()) == {"b-root"}

    def test_is_bound_root(self):
        from app.services.agent.core.registry import AgentRegistry

        r = AgentRegistry()
        r.register_agent("a-root", "A", "orchestrator", "t")
        r.bind_task("task-a", "a-root")
        assert r.is_bound_root("a-root") is True
        assert r.is_bound_root("nonexistent") is False

    def test_finish_tool_validate_root_accepts_bound_root(self):
        """并发下非全局根的任务根也能通过 finish 校验（C1，此前会被全局根误拒）。"""
        from app.services.agent.tools.finish_tool import FinishScanTool
        from app.services.agent.core import agent_registry

        agent_registry.clear()
        try:
            agent_registry.register_agent("a-root", "A", "orchestrator", "t")
            agent_registry.register_agent("b-root", "B", "orchestrator", "t")
            agent_registry.bind_task("task-a", "a-root")
            agent_registry.bind_task("task-b", "b-root")

            tool_b = FinishScanTool(agent_id="b-root")
            assert tool_b._validate_root_agent() is None
        finally:
            agent_registry.clear()
