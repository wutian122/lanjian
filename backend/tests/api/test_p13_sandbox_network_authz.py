"""P13 终审 Critical 修复（方案 a）：sandboxNetworkEnabled 管理员限定。

终审发现：沙箱联网开关是安全策略键（kill-switch），但 PUT /config/me 的
OtherConfigSchema 允许**任意登录用户**写入该键，而 P0-D 的 helper 采
「用户配置优先」——等于任何用户可给自己开沙箱联网（越权）。

方案 a（老板批准）：该键仅超级管理员（系统级配置来源）可写——
- 非超管 PUT 请求中该键被剔除（不落库）；
- 非超管更新既有配置时，DB 历史残留的同键被主动清除（防越权残留）；
- 超管 PUT 正常保存（系统级配置即其个人配置，全用户继承）。

helper 侧语义无需改动：PUT 杜绝用户层写入后，resolve_effective_config 合并
（default(env) → sys(超管) → user(无该键)）的结果自然只来自系统层/默认。
"""
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.config import (
    OtherConfigSchema,
    UserConfigRequest,
    update_my_config,
)


class _FakeScalarResult:
    def __init__(self, obj: Any | None):
        self._obj = obj

    def scalar_one_or_none(self) -> Any | None:
        return self._obj


class _FakeDB:
    def __init__(self, config_row: Any | None):
        self._config_row = config_row
        self.added: list = []

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeScalarResult:
        return _FakeScalarResult(self._config_row)

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = "cfg-new"
        self.added.append(obj)
        self._config_row = obj

    commit = AsyncMock()
    refresh = AsyncMock()


def _user(is_superuser: bool, uid: str = "u-x") -> SimpleNamespace:
    return SimpleNamespace(id=uid, is_superuser=is_superuser)


def _config_row(other: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="cfg-1",
        user_id="u-x",
        llm_config=json.dumps({}),
        other_config=json.dumps(other or {}),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_non_superuser_sandbox_network_key_stripped_on_create():
    """非超管新建配置携带 sandboxNetworkEnabled=true → 该键被剔除（不落库），
    其他字段正常保存。"""
    db = _FakeDB(None)
    req = UserConfigRequest(
        otherConfig=OtherConfigSchema(
            sandboxNetworkEnabled=True, maxAnalyzeFiles=100
        )
    )
    await update_my_config(config_in=req, db=db, current_user=_user(False))

    saved = json.loads(db.added[0].other_config)
    assert "sandboxNetworkEnabled" not in saved, (
        "非超管不得写入沙箱联网开关（越权开网）"
    )
    assert saved.get("maxAnalyzeFiles") == 100, "其他字段不得被误伤"


@pytest.mark.asyncio
async def test_superuser_can_set_sandbox_network_key():
    """超管（系统级配置来源）可正常保存该键（全用户继承语义）。"""
    db = _FakeDB(None)
    req = UserConfigRequest(
        otherConfig=OtherConfigSchema(sandboxNetworkEnabled=True)
    )
    await update_my_config(config_in=req, db=db, current_user=_user(True, "u-admin"))

    saved = json.loads(db.added[0].other_config)
    assert saved.get("sandboxNetworkEnabled") is True


@pytest.mark.asyncio
async def test_non_superuser_update_removes_historical_key():
    """非超管更新既有配置（DB 历史残留该键=true）→ 更新后该键被清除。"""
    row = _config_row({"sandboxNetworkEnabled": True, "outputLanguage": "zh"})
    db = _FakeDB(row)

    req = UserConfigRequest(otherConfig=OtherConfigSchema(maxAnalyzeFiles=50))
    await update_my_config(config_in=req, db=db, current_user=_user(False))

    saved = json.loads(row.other_config)
    assert "sandboxNetworkEnabled" not in saved, (
        "非超管更新时历史越权残留必须清除"
    )
    assert saved.get("outputLanguage") == "zh", "其他历史字段保留"
    assert saved.get("maxAnalyzeFiles") == 50


@pytest.mark.asyncio
async def test_superuser_update_preserves_key():
    """超管更新其他字段 → 既有该键保留（不得误清除）。"""
    row = _config_row({"sandboxNetworkEnabled": True})
    db = _FakeDB(row)

    req = UserConfigRequest(otherConfig=OtherConfigSchema(maxAnalyzeFiles=50))
    await update_my_config(config_in=req, db=db, current_user=_user(True, "u-admin"))

    saved = json.loads(row.other_config)
    assert saved.get("sandboxNetworkEnabled") is True
    assert saved.get("maxAnalyzeFiles") == 50
