"""审计记忆系统单元测试。

覆盖：
- 记忆条目转换 (_finding_to_memory)
- fingerprint 去重 (_dedup_by_fingerprint)
- 记忆线索格式化 (format_memory_lead)
- 项目记忆加载编排 (load_project_memory)
"""
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

AUDIT_MEMORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "app" / "services" / "agent" / "audit_memory.py"
)


def _load_audit_memory_module():
    """直接从文件路径加载 audit_memory，绕开 app.services.agent.__init__ 的重依赖。

    audit_memory 模块级仅 ``from sqlalchemy import select``，不触发重依赖，
    可安全用文件路径加载。``load_project_memory`` 内的
    ``from app.models.agent_task import ...`` 是延迟导入，测试时通过替换
    模块的 ``select``（见下方）绕开真实查询，因此**不需要**污染 sys.modules。
    """
    spec = spec_from_file_location("audit_memory_module", AUDIT_MEMORY_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_audit_memory_module()


class _ChainableStmt:
    """可链式调用的 SQL 语句桩：join/where 均返回自身。"""
    def join(self, *a, **k):
        return self
    def where(self, *a, **k):
        return self


# 用桩替换真实 select，避免 MagicMock ORM 列构造 select 时抛错
_mod.select = lambda *a, **k: _ChainableStmt()

load_project_memory = _mod.load_project_memory
format_memory_lead = _mod.format_memory_lead
_finding_to_memory = _mod._finding_to_memory
_dedup_by_fingerprint = _mod._dedup_by_fingerprint
_rows_to_memory = _mod._rows_to_memory
MEMORY_VERIFICATION_STATUSES = _mod.MEMORY_VERIFICATION_STATUSES
MEMORY_SEVERITIES = _mod.MEMORY_SEVERITIES


def _make_finding(**kwargs):
    """构造一个类 AgentFinding 的轻量对象（不入库）。"""
    defaults = {
        "vulnerability_type": "sql_injection",
        "severity": "high",
        "file_path": "src/sql_vuln.py",
        "line_start": 45,
        "function_name": "get_user",
        "title": "SQL Injection",
        "description": "f-string SQL query",
        "verification_status": "confirmed",
        "code_snippet": "query = f\"...{user_id}...\"",
        "fingerprint": "abc123",
    }
    defaults.update(kwargs)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


# ---------- _finding_to_memory ----------

def test_finding_to_memory_maps_fields():
    f = _make_finding()
    mem = _finding_to_memory(f, "审计v1")
    assert mem["type"] == "sql_injection"
    assert mem["severity"] == "high"
    assert mem["file_path"] == "src/sql_vuln.py"
    assert mem["line_start"] == 45
    assert mem["verification_status"] == "confirmed"
    assert mem["title"] == "SQL Injection"
    assert mem["task_name"] == "审计v1"
    assert mem["fingerprint"] == "abc123"


# ---------- _dedup_by_fingerprint ----------

def test_dedup_keeps_first_of_same_fingerprint():
    entries = [
        {"fingerprint": "fp1", "title": "first"},
        {"fingerprint": "fp1", "title": "dup"},
        {"fingerprint": "fp2", "title": "second"},
    ]
    out = _dedup_by_fingerprint(entries)
    assert len(out) == 2
    assert out[0]["title"] == "first"
    assert out[1]["title"] == "second"


def test_dedup_handles_missing_fingerprint():
    # fingerprint 为空时不应误合并
    entries = [
        {"fingerprint": None, "title": "a"},
        {"fingerprint": None, "title": "b"},
    ]
    out = _dedup_by_fingerprint(entries)
    assert len(out) == 2


# ---------- format_memory_lead ----------

def test_format_empty_returns_empty_string():
    assert format_memory_lead([]) == ""


def test_format_contains_key_info():
    memory = [
        {"type": "sql_injection", "severity": "critical", "file_path": "a.py",
         "line_start": 10, "verification_status": "confirmed",
         "title": "SQLi", "description": "bad", "task_name": "t1"},
        {"type": "path_traversal", "severity": "high", "file_path": "b.py",
         "line_start": 20, "verification_status": "static_confirmed",
         "title": "PT", "description": "bad2", "task_name": "t1"},
    ]
    text = format_memory_lead(memory)
    assert "历史审计记忆" in text
    assert "a.py:10" in text
    assert "b.py:20" in text
    assert "sql_injection" in text
    # 强调需独立验证
    assert "验证" in text


# ---------- load_project_memory ----------

# ---------- _rows_to_memory (纯函数：映射+排序+去重+截断) ----------

def test_rows_to_memory_dedups_by_fingerprint():
    f1 = _make_finding(fingerprint="fp1", title="A")
    f2 = _make_finding(fingerprint="fp1", title="A-dup")  # 重复指纹
    f3 = _make_finding(fingerprint="fp2", title="B", severity="critical")
    out = _rows_to_memory([(f1, "t1"), (f2, "t1"), (f3, "t1")], limit=30)
    titles = [m["title"] for m in out]
    assert "A" in titles
    assert "B" in titles
    assert "A-dup" not in titles


def test_rows_to_memory_sorts_by_severity():
    f_med = _make_finding(fingerprint="m", title="M", severity="medium")
    f_crit = _make_finding(fingerprint="c", title="C", severity="critical")
    f_high = _make_finding(fingerprint="h", title="H", severity="high")
    out = _rows_to_memory([(f_med, "t"), (f_crit, "t"), (f_high, "t")], limit=30)
    assert [m["severity"] for m in out] == ["critical", "high", "medium"]


def test_rows_to_memory_respects_limit():
    rows = [(_make_finding(fingerprint=f"fp{i}", title=f"T{i}"), "t1") for i in range(10)]
    out = _rows_to_memory(rows, limit=5)
    assert len(out) == 5


# ---------- load_project_memory (async 编排) ----------

@pytest.mark.asyncio
async def test_load_returns_empty_when_no_project_id():
    db = AsyncMock()
    out = await load_project_memory(db, project_id="", exclude_task_id="t")
    assert out == []
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_load_returns_empty_when_no_rows():
    db = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(return_value=result)

    out = await load_project_memory(db, project_id="p1", exclude_task_id="t-cur")
    assert out == []


@pytest.mark.asyncio
async def test_load_processes_rows_from_db():
    db = AsyncMock()
    f1 = _make_finding(fingerprint="fp1", title="A")
    f3 = _make_finding(fingerprint="fp2", title="B", severity="critical")
    result = MagicMock()
    result.all = MagicMock(return_value=[(f1, "t1"), (f3, "t1")])
    db.execute = AsyncMock(return_value=result)

    out = await load_project_memory(db, project_id="p1", exclude_task_id="t-cur")
    titles = [m["title"] for m in out]
    assert titles == ["B", "A"]  # critical 排前


@pytest.mark.asyncio
async def test_load_swallows_db_errors():
    # DB 异常不应抛出，返回空列表（non-fatal）
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    out = await load_project_memory(db, project_id="p1", exclude_task_id="t-cur")
    assert out == []


def test_memory_constants_are_sane():
    assert "confirmed" in MEMORY_VERIFICATION_STATUSES
    assert "static_confirmed" in MEMORY_VERIFICATION_STATUSES
    assert "critical" in MEMORY_SEVERITIES
    assert "high" in MEMORY_SEVERITIES
