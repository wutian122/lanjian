"""fix-audit-observability-time-governance Task 1: 任务级超时默认值短路修复。

根因：AgentTaskCreate.timeout_seconds 的 Pydantic 默认值 1800 导致每个任务落库
都是 1800，执行入口又以 `task.timeout_seconds or 1800` 作为最高优先级传给
orchestrator，_resolve_task_timeout 第一优先级永远命中，全局
llmConfig.agentTimeout（系统设置 → Agent 总超时）成为死配置。

修复契约（specs/audit-time-governance/spec.md Requirement: 任务时间预算解析
不得被任务级默认值短路）：
1. 创建请求未携带 timeout_seconds → 字段为 None → 落库 NULL（不得落 1800）；
2. 执行入口对 NULL 传 None（不得回退字面量 1800），orchestrator 回退全局配置；
3. endpoint watchdog 时钟与 orchestrator 内部 deadline 时钟同源
   （resolve_task_timeout_seconds 与 _resolve_task_timeout 语义一致）；
4. 历史任务（DB 已落 1800 的显式值）行为不变。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import settings

# ---------- Schema：未显式传值时不得落默认 1800 ----------


def test_create_schema_omitted_timeout_is_none():
    """Scenario 全局生效前置：不传 timeout_seconds → 字段为 None（而非 1800）。"""
    from app.api.v1.endpoints.agent_tasks import AgentTaskCreate

    payload = AgentTaskCreate(project_id="proj-1")
    assert payload.timeout_seconds is None


def test_create_schema_explicit_timeout_preserved():
    """Scenario 显式优先：传 3600 → 字段保留 3600。"""
    from app.api.v1.endpoints.agent_tasks import AgentTaskCreate

    payload = AgentTaskCreate(project_id="proj-1", timeout_seconds=3600)
    assert payload.timeout_seconds == 3600


def test_create_schema_rejects_out_of_range_timeout():
    """边界校验保持：60 <= timeout_seconds <= 7200。"""
    from app.api.v1.endpoints.agent_tasks import AgentTaskCreate

    with pytest.raises(ValidationError):
        AgentTaskCreate(project_id="proj-1", timeout_seconds=59)
    with pytest.raises(ValidationError):
        AgentTaskCreate(project_id="proj-1", timeout_seconds=7201)


# ---------- Model：列允许 NULL ----------


def test_model_timeout_column_nullable():
    """落库 NULL 的前提：agent_tasks.timeout_seconds 列 nullable。"""
    from app.models.agent_task import AgentTask

    column = AgentTask.__table__.c.timeout_seconds
    assert column.nullable is True


# ---------- 创建落库：未显式传值 → None（NULL），不得落 1800 ----------


async def _create_task_capturing_kwargs(monkeypatch, request):
    """调用 create_agent_task 并捕获 AgentTask(...) 构造 kwargs（落库值）。"""
    from app.api.v1.endpoints import agent_tasks as module

    project = SimpleNamespace(id="project-1", owner_id="user-1", name="proj")
    db = AsyncMock()
    db.get = AsyncMock(return_value=project)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr(module, "_get_user_config", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "_execute_agent_task", AsyncMock())
    # D1: 创建端点改走 _launch_task_bg，调度包装必须 mock
    monkeypatch.setattr(module, "_launch_task_bg", MagicMock())

    captured: dict = {}
    original_init = module.AgentTask.__init__

    def spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(module.AgentTask, "__init__", spy_init)

    await module.create_agent_task(
        request,
        db=db,
        current_user=SimpleNamespace(id="user-1"),
    )
    return captured


@pytest.mark.asyncio
async def test_create_persists_null_when_timeout_omitted(monkeypatch):
    """Scenario 全局生效：不传 timeout_seconds → 落库值为 None（NULL），不是 1800。"""
    from app.api.v1.endpoints.agent_tasks import AgentTaskCreate

    request = AgentTaskCreate(project_id="project-1")
    captured = await _create_task_capturing_kwargs(monkeypatch, request)

    assert captured["timeout_seconds"] is None


@pytest.mark.asyncio
async def test_create_persists_explicit_timeout(monkeypatch):
    """Scenario 显式优先：传 3600 → 落库 3600。"""
    from app.api.v1.endpoints.agent_tasks import AgentTaskCreate

    request = AgentTaskCreate(project_id="project-1", timeout_seconds=3600)
    captured = await _create_task_capturing_kwargs(monkeypatch, request)

    assert captured["timeout_seconds"] == 3600


# ---------- 共享解析函数：显式 > 用户 agentTimeout > settings > 1800 ----------


def test_resolve_explicit_task_timeout_wins_over_global():
    """Scenario 显式优先：任务级 3600 压过全局 agentTimeout=7200。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    task = SimpleNamespace(timeout_seconds=3600)
    user_config = {"llmConfig": {"agentTimeout": 7200}}
    assert resolve_task_timeout_seconds(task, user_config) == 3600.0


def test_resolve_null_task_timeout_uses_user_agent_timeout():
    """Scenario 全局生效：任务级 NULL → 回退用户 llmConfig.agentTimeout=7200。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    task = SimpleNamespace(timeout_seconds=None)
    user_config = {"llmConfig": {"agentTimeout": 7200}}
    assert resolve_task_timeout_seconds(task, user_config) == 7200.0


def test_resolve_null_without_user_config_uses_settings(monkeypatch):
    """任务级 NULL 且无用户配置 → settings.AGENT_TIMEOUT_SECONDS；再缺省 1800。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    task = SimpleNamespace(timeout_seconds=None)
    monkeypatch.setattr(settings, "AGENT_TIMEOUT_SECONDS", 2400)
    assert resolve_task_timeout_seconds(task, {}) == 2400.0
    assert resolve_task_timeout_seconds(task, None) == 2400.0


def test_resolve_default_floor_is_1800(monkeypatch):
    """全链缺失时兜底 1800（与 orchestrator _resolve_task_timeout 一致）。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    task = SimpleNamespace(timeout_seconds=None)
    monkeypatch.setattr(settings, "AGENT_TIMEOUT_SECONDS", 1800)
    assert resolve_task_timeout_seconds(task, {}) == 1800.0


def test_resolve_legacy_1800_row_unchanged():
    """Scenario 历史任务：DB 已落 1800（显式值）→ 仍 1800，不被全局 7200 覆盖。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    task = SimpleNamespace(timeout_seconds=1800)
    user_config = {"llmConfig": {"agentTimeout": 7200}}
    assert resolve_task_timeout_seconds(task, user_config) == 1800.0


def test_resolve_ignores_invalid_values(monkeypatch):
    """防御：非法值（0/负数/不可转换）不短路，落到下一级。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    monkeypatch.setattr(settings, "AGENT_TIMEOUT_SECONDS", 1800)
    user_config = {"llmConfig": {"agentTimeout": 0}}
    assert resolve_task_timeout_seconds(SimpleNamespace(timeout_seconds=0), user_config) == 1800.0
    assert resolve_task_timeout_seconds(SimpleNamespace(timeout_seconds="bad"), user_config) == 1800.0


# ---------- 时钟同源：endpoint watchdog 与 orchestrator deadline 解析一致 ----------


def _make_orchestrator_with_user_config(user_config):
    """用真实 LLMService 构造 orchestrator，使其 _timeout_config 走用户配置链。"""
    from app.services.agent.agents.orchestrator import OrchestratorAgent
    from app.services.llm.service import LLMService

    return OrchestratorAgent(llm_service=LLMService(user_config=user_config), tools={})


def test_watchdog_and_orchestrator_clocks_same_source_null_task_timeout():
    """任务级 NULL：两处时钟都解析为用户 agentTimeout=7200（全局真正生效）。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    user_config = {"llmConfig": {"agentTimeout": 7200}}
    task = SimpleNamespace(timeout_seconds=None)

    watchdog_clock = resolve_task_timeout_seconds(task, user_config)
    orchestrator = _make_orchestrator_with_user_config(user_config)
    # 执行入口对 NULL 传 None（不得传 1800）
    deadline_clock = orchestrator._resolve_task_timeout({"task_timeout_seconds": None})

    assert watchdog_clock == 7200.0
    assert deadline_clock == 7200.0
    assert watchdog_clock == deadline_clock


def test_watchdog_and_orchestrator_clocks_same_source_explicit_timeout():
    """任务级显式 3600：两处时钟都为 3600，全局不覆盖。"""
    from app.api.v1.endpoints.agent_tasks import resolve_task_timeout_seconds

    user_config = {"llmConfig": {"agentTimeout": 7200}}
    task = SimpleNamespace(timeout_seconds=3600)

    watchdog_clock = resolve_task_timeout_seconds(task, user_config)
    orchestrator = _make_orchestrator_with_user_config(user_config)
    deadline_clock = orchestrator._resolve_task_timeout({"task_timeout_seconds": 3600})

    assert watchdog_clock == 3600.0
    assert deadline_clock == 3600.0
    assert watchdog_clock == deadline_clock
