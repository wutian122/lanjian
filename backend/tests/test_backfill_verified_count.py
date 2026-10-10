"""P10-T4 回填脚本单测：backfill_verified_count.py。

验证：
- 重算口径（is_verified=True 且 status in confirmed/static_confirmed）；
- 无匹配任务新值为 0；
- 新旧相同不产生更新（幂等）；
- --dry-run 零写入零提交；
- 实算执行 UPDATE 并提交。

脚本不在 app 包内，按文件路径加载。
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_verified_count.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_verified_count", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """按编译后 SQL 内容分发预置结果，并记录全部写入。"""

    def __init__(self, task_rows, grouped):
        self._task_rows = task_rows
        self._grouped = grouped
        self.executed_sql: list[str] = []
        self.commits = 0

    async def execute(self, stmt):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.executed_sql.append(sql)
        if sql.lstrip().upper().startswith("UPDATE"):
            return MagicMock(rowcount=1)
        if "agent_findings" in sql:
            return _FakeResult(self._grouped)
        return _FakeResult(self._task_rows)

    async def commit(self):
        self.commits += 1


class TestPlanBackfill:
    def test_changed_tasks_in_plan(self):
        mod = _load_module()
        plan = mod.plan_backfill(
            [("t1", 1), ("t2", 0), ("t3", 5)],
            {"t1": 3, "t3": 5},
        )
        assert plan == [("t1", 1, 3)]

    def test_zero_match_task_with_dirty_old_value_resets(self):
        """GROUP BY 不返回零匹配任务：旧值非零时必须重置为 0"""
        mod = _load_module()
        plan = mod.plan_backfill([("t4", 2)], {})
        assert plan == [("t4", 2, 0)]

    def test_already_consistent_no_plan_idempotent(self):
        mod = _load_module()
        plan = mod.plan_backfill([("t1", 2)], {"t1": 2})
        assert plan == []


class TestFetchVerifiedCounts:
    @pytest.mark.asyncio
    async def test_counts_mapped_by_task_id(self):
        mod = _load_module()
        db = _FakeDB(
            task_rows=[],
            grouped=[("t1", 2), ("t2", 1)],
        )
        counts = await mod.fetch_verified_counts(db)
        assert counts == {"t1": 2, "t2": 1}

    @pytest.mark.asyncio
    async def test_count_query_uses_new_predicate(self):
        """GROUP BY 查询必须带新口径谓词（is_verified + 双状态 IN）"""
        mod = _load_module()
        db = _FakeDB(task_rows=[], grouped=[])
        await mod.fetch_verified_counts(db)
        sql = db.executed_sql[0]
        assert "is_verified" in sql
        assert "confirmed" in sql
        assert "static_confirmed" in sql


class TestRunBackfill:
    @pytest.mark.asyncio
    async def test_dry_run_no_writes(self, capsys):
        mod = _load_module()
        db = _FakeDB(
            task_rows=[("t1", 1), ("t2", 0)],
            grouped=[("t1", 3)],
        )
        plan = await mod.run_backfill(db, dry_run=True)
        assert plan == [("t1", 1, 3)]
        assert not any(s.lstrip().upper().startswith("UPDATE") for s in db.executed_sql)
        assert db.commits == 0
        out = capsys.readouterr().out
        assert "t1" in out and "dry-run" in out

    @pytest.mark.asyncio
    async def test_real_run_updates_and_commits(self):
        mod = _load_module()
        db = _FakeDB(
            task_rows=[("t1", 1)],
            grouped=[("t1", 3)],
        )
        await mod.run_backfill(db, dry_run=False)
        update_sql = [s for s in db.executed_sql if s.lstrip().upper().startswith("UPDATE")]
        assert len(update_sql) == 1
        assert "3" in update_sql[0]
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_real_run_empty_plan_no_writes(self):
        """幂等：已是新口径时重跑零 UPDATE 零 commit"""
        mod = _load_module()
        db = _FakeDB(
            task_rows=[("t1", 2)],
            grouped=[("t1", 2)],
        )
        await mod.run_backfill(db, dry_run=False)
        assert not any(
            s.lstrip().upper().startswith("UPDATE") for s in db.executed_sql
        )
        assert db.commits == 0
