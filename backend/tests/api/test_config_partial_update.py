"""
W5: PUT /config/me 部分更新契约（llmMaxTokens 回写陷阱后端侧）

生产实证：前端 SystemConfig 全量保存时把「加载时快照」整体回写——用户只改
temperature，payload 仍携带加载时的 llmMaxTokens 旧值，在 PUT 合并语义
（existing_llm.update(llm_data)）下旧值覆盖 DB 现值（老板把 llmMaxTokens
改 32768 两次都被回写 4096）。

修复落在前端字段级 dirty-check（未修改字段不进 payload）。后端契约本已成立
（schema 全字段 Optional + dict(exclude_none=True) + existing.update），
本测试锁定该契约，防回归：

1. payload 未含 llmMaxTokens → DB 现值保留（旧快照不得回写）；
2. payload 显式携带 llmMaxTokens → 覆盖 DB 现值（用户显式修改仍生效）；
3. payload 只含 otherConfig 字段 → llm 配置整体不动。
"""
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.config import (
    LLMConfigSchema,
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
    """最小 AsyncSession 替身：只支持 update_my_config 用到的接口。"""

    def __init__(self, config_row: Any | None):
        self._config_row = config_row
        self.added: list = []

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeScalarResult:
        return _FakeScalarResult(self._config_row)

    def add(self, obj: Any) -> None:
        # 模拟 DB 侧默认主键/时间戳填充（真实 UserConfig 由数据库生成 id）
        if getattr(obj, "id", None) is None:
            obj.id = "cfg-new"
        self.added.append(obj)
        self._config_row = obj

    commit = AsyncMock()
    refresh = AsyncMock()


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="user-partial-1", is_superuser=False)


def _config_row(llm: dict, other: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="cfg-1",
        user_id="user-partial-1",
        llm_config=json.dumps(llm),
        other_config=json.dumps(other or {}),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=None,
    )


def _saved_llm(row: SimpleNamespace) -> dict:
    return json.loads(row.llm_config)


@pytest.mark.asyncio
async def test_put_omitting_llm_max_tokens_preserves_db_value():
    """payload 不含 llmMaxTokens（前端 dirty-check 省略未改字段）→ DB 现值 32768 保留。"""
    row = _config_row({"llmMaxTokens": 32768, "llmTemperature": 0.6})
    db = _FakeDB(row)

    req = UserConfigRequest(llmConfig=LLMConfigSchema(llmTemperature=0.7))
    await update_my_config(config_in=req, db=db, current_user=_user())

    saved = _saved_llm(row)
    assert saved["llmMaxTokens"] == 32768, "未发送的字段被旧值/默认值覆盖——回写陷阱"
    assert saved["llmTemperature"] == 0.7, "显式发送的字段未生效"


@pytest.mark.asyncio
async def test_put_including_llm_max_tokens_overwrites_db_value():
    """payload 显式携带 llmMaxTokens=2048 → 覆盖 DB 现值（用户显式修改必须生效）。"""
    row = _config_row({"llmMaxTokens": 4096, "llmTemperature": 0.6})
    db = _FakeDB(row)

    req = UserConfigRequest(llmConfig=LLMConfigSchema(llmMaxTokens=2048))
    await update_my_config(config_in=req, db=db, current_user=_user())

    saved = _saved_llm(row)
    assert saved["llmMaxTokens"] == 2048
    assert saved["llmTemperature"] == 0.6, "未发送的字段不应被改动"


@pytest.mark.asyncio
async def test_put_only_other_config_leaves_llm_untouched():
    """payload 只含 otherConfig → llm_config 原样保留（连更新分支都不应进入）。"""
    row = _config_row({"llmMaxTokens": 32768, "llmModel": "qwen-test"})
    original_llm_blob = row.llm_config
    db = _FakeDB(row)

    req = UserConfigRequest(otherConfig=OtherConfigSchema(outputLanguage="en"))
    await update_my_config(config_in=req, db=db, current_user=_user())

    assert row.llm_config == original_llm_blob, "otherConfig 更新不应重写 llm_config"
    assert json.loads(row.other_config)["outputLanguage"] == "en"


@pytest.mark.asyncio
async def test_put_partial_fields_on_new_config_creates_only_sent_keys():
    """用户无既有配置时，部分 payload 新建的 llm_config 只含发送的键（不固化继承值）。"""
    db = _FakeDB(None)

    req = UserConfigRequest(llmConfig=LLMConfigSchema(llmMaxTokens=8192))
    await update_my_config(config_in=req, db=db, current_user=_user())

    assert len(db.added) == 1
    saved = json.loads(db.added[0].llm_config)
    assert saved == {"llmMaxTokens": 8192}
