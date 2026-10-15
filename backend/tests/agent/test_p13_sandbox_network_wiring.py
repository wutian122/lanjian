"""P13 T3（P0-D 沙箱联网开关接线）：用户配置驱动沙箱联网判定。

背景：前端开关（SystemConfig.tsx）把 sandboxNetworkEnabled 存进 DB
user_configs.other_config（双机已存 true），但验证引擎 4 处读点全部只读环境变量
settings.SANDBOX_NETWORK_ENABLED；config.py:206 的配置回显又硬编码 False →
"页面已开却仍断网 / 刷新后又变关"的呈现断裂。

P13 T3 契约：
- 新增 resolve_sandbox_network_enabled(user_config)：用户配置
  otherConfig.sandboxNetworkEnabled 显式值优先；缺失回退
  settings.SANDBOX_NETWORK_ENABLED（env 双保险）；都缺失 → False。
- 三处读点（verification 的 _network_mode_for_command / sandbox_tool 网络门禁 /
  agent_tasks 重跑端点）统一消费该 helper，口径一致。
- config 回显修复：系统默认 sandboxNetworkEnabled 不再硬编码 False，而是反映
  settings.SANDBOX_NETWORK_ENABLED；用户保存值仍覆盖默认（用户优先）。

这些都是纯单元测试（monkeypatch + 轻量 mock，不依赖 DB/Redis/Docker）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.services.agent.tools.sandbox_tool import (
    SandboxConfig,
    SandboxTool,
    resolve_sandbox_network_enabled,
)

# ============ ① helper 真值表 ============

def test_helper_user_true_wins_over_env_false(monkeypatch):
    """用户配置 true + env false → True（用户优先）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    assert resolve_sandbox_network_enabled({"otherConfig": {"sandboxNetworkEnabled": True}}) is True


def test_helper_user_false_wins_over_env_true(monkeypatch):
    """用户配置 false + env true → False（用户优先，可显式关网）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    assert resolve_sandbox_network_enabled({"otherConfig": {"sandboxNetworkEnabled": False}}) is False


def test_helper_missing_user_config_falls_back_to_env(monkeypatch):
    """用户配置缺失该键 + env true → True（env 双保险）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    assert resolve_sandbox_network_enabled({"otherConfig": {}}) is True
    assert resolve_sandbox_network_enabled(None) is True
    # 直接传 otherConfig 层 dict（无 otherConfig 包裹）也支持
    assert resolve_sandbox_network_enabled({}) is True


def test_helper_all_missing_is_false(monkeypatch):
    """用户与 env 都缺失/关 → False（安全侧默认关）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    assert resolve_sandbox_network_enabled(None) is False
    assert resolve_sandbox_network_enabled({}) is False
    assert resolve_sandbox_network_enabled({"otherConfig": {}}) is False


def test_helper_flat_otherconfig_shape(monkeypatch):
    """兼容直接传 otherConfig 层（无 otherConfig 包裹键）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    assert resolve_sandbox_network_enabled({"sandboxNetworkEnabled": True}) is True


# ============ ② _network_mode_for_command 消费用户配置 ============

def _ssrf_cmd_input():
    return {
        "command": "python3 /tmp/poc_0.py",
        "timeout": 30,
        "network_enabled": True,
    }


def _make_verification_agent(user_config=None):
    from app.services.agent.agents.verification import VerificationAgent

    kwargs = {}
    if user_config is not None or True:
        kwargs["user_config"] = user_config
    agent = VerificationAgent(llm_service=MagicMock(), tools={}, **kwargs)
    return agent


def test_network_mode_bridge_when_db_switch_true(monkeypatch):
    """DB 开关 true（用户配置）+ SSRF 模板请求网络 → bridge（当前恒 none 是 RED 根因）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    agent = _make_verification_agent(
        {"otherConfig": {"sandboxNetworkEnabled": True}}
    )
    assert agent._network_mode_for_command(_ssrf_cmd_input()) == "bridge"


def test_network_mode_none_when_db_switch_false(monkeypatch):
    """DB 开关 false 覆盖 env true → none（用户可关网，且 kill-switch 生效）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    agent = _make_verification_agent(
        {"otherConfig": {"sandboxNetworkEnabled": False}}
    )
    assert agent._network_mode_for_command(_ssrf_cmd_input()) == "none"


def test_network_mode_env_fallback_when_user_missing(monkeypatch):
    """用户配置缺失该键 → 回退 env（双保险）；env true 时 SSRF 得 bridge。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    agent = _make_verification_agent({"otherConfig": {}})
    assert agent._network_mode_for_command(_ssrf_cmd_input()) == "bridge"


def test_network_mode_non_network_cmd_stays_none(monkeypatch):
    """未请求网络的模板即使开关开也 none（最小权限）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    agent = _make_verification_agent(
        {"otherConfig": {"sandboxNetworkEnabled": True}}
    )
    assert agent._network_mode_for_command({"command": "echo x"}) == "none"


# ============ ③ sandbox_tool 门禁同口径 ============

def _ok_result():
    return {
        "success": True,
        "stdout": "ok\n",
        "stderr": "",
        "exit_code": 0,
        "error": None,
    }


def _mock_manager_with_config(network_enabled: bool):
    mgr = MagicMock()
    mgr.config = SimpleNamespace(
        network_enabled=network_enabled,
        network_mode="bridge" if network_enabled else "none",
    )
    mgr.is_available = True
    mgr.initialize = AsyncMock()
    mgr.execute_tool_command = AsyncMock(return_value=_ok_result())
    mgr.execute_command = AsyncMock(return_value=_ok_result())
    return mgr


@pytest.mark.asyncio
async def test_sandbox_tool_gate_uses_user_config_true(tmp_path, monkeypatch):
    """SandboxTool 注入用户配置 true（env false）→ LLM 请求网络时给 bridge。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    mgr = _mock_manager_with_config(network_enabled=False)
    tool = SandboxTool(
        mgr, str(tmp_path), user_config={"otherConfig": {"sandboxNetworkEnabled": True}}
    )
    result = await tool._execute(command="python3 app.py", timeout=5, network_enabled=True)
    assert result.success is True
    assert mgr.execute_tool_command.await_args.kwargs.get("network_mode") == "bridge"


@pytest.mark.asyncio
async def test_sandbox_tool_gate_user_false_overrides_env_true(tmp_path, monkeypatch):
    """用户配置 false（env true）→ 容器仍 none（用户可显式关网）。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    mgr = _mock_manager_with_config(network_enabled=True)
    tool = SandboxTool(
        mgr, str(tmp_path), user_config={"otherConfig": {"sandboxNetworkEnabled": False}}
    )
    result = await tool._execute(command="python3 app.py", timeout=5, network_enabled=True)
    assert result.success is True
    assert mgr.execute_tool_command.await_args.kwargs.get("network_mode") == "none"


def test_sandbox_config_user_config_drives_network(monkeypatch):
    """SandboxConfig 携带 user_config → network_enabled 由 helper 解析。"""
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    cfg = SandboxConfig(user_config={"otherConfig": {"sandboxNetworkEnabled": True}})
    assert cfg.network_enabled is True
    assert cfg.network_mode == "bridge"

    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    cfg2 = SandboxConfig(user_config={"otherConfig": {"sandboxNetworkEnabled": False}})
    assert cfg2.network_enabled is False


# ============ ④ config 回显修复 ============

def test_default_config_reflects_env_switch(monkeypatch):
    """系统默认 sandboxNetworkEnabled 反映 settings.SANDBOX_NETWORK_ENABLED（非硬编码 False）。"""
    from app.api.v1.endpoints.config import get_default_config

    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    assert get_default_config()["otherConfig"]["sandboxNetworkEnabled"] is True

    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    assert get_default_config()["otherConfig"]["sandboxNetworkEnabled"] is False


@pytest.mark.asyncio
async def test_get_me_echo_reflects_env_when_user_unsaved(monkeypatch):
    """GET /me 口径：用户未保存该键 + env true → 回显 true（当前硬编码 False 是 RED）。"""
    from app.api.v1.endpoints import config as cfg

    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    monkeypatch.setattr(cfg, "get_system_admin_config", AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(cfg, "_load_raw_user_config", AsyncMock(return_value=({}, {})))

    db = AsyncMock()
    _, other = await cfg.resolve_effective_config(db, "u1", is_superuser=False)
    assert other.get("sandboxNetworkEnabled") is True


@pytest.mark.asyncio
async def test_get_me_echo_user_saved_true_wins(monkeypatch):
    """GET /me 口径：用户保存 true（env false）→ 回显 true（用户优先，不回退 env）。"""
    from app.api.v1.endpoints import config as cfg

    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    monkeypatch.setattr(cfg, "get_system_admin_config", AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(
        cfg,
        "_load_raw_user_config",
        AsyncMock(return_value=({}, {"sandboxNetworkEnabled": True})),
    )

    db = AsyncMock()
    _, other = await cfg.resolve_effective_config(db, "u1", is_superuser=False)
    assert other.get("sandboxNetworkEnabled") is True
