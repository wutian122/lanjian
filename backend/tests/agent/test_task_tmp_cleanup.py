"""
任务收尾清理 /tmp/lanjian/<task_id> 临时源码目录（防止 tmpfs 累积塞满）

根因：_execute_agent_task 的 finally 块只清理内存状态，从不清理文件目录；
task_cleanup.cleanup_agent_task_resources 的 cleanedFiles 恒空。46 个历史任务
目录累积 13G 塞满服务器 A 的 16G tmpfs，导致新任务 [Errno 28] 失败，并连带
db/redis 因 docker exec 写 /tmp/runc-process* 失败而误报 unhealthy。

对应 specs/agent-task-cleanup/spec.md：
  REQ-CLEAN-1 任务收尾清理临时源码目录
  REQ-CLEAN-2 重新验证 finding 兼容已清理目录
  REQ-CLEAN-3 删除任务同步清理临时目录
"""

import ast
import shutil as shutil_mod
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

SRC_AGENT_TASKS = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints" / "agent_tasks.py"
SRC_TASK_CLEANUP = Path(__file__).resolve().parents[2] / "app" / "services" / "agent" / "task_cleanup.py"


def _extract_function_body(source: str, func_name: str) -> str:
    """用 ast 提取指定函数的完整函数体（含 def 行）。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            start = node.lineno - 1
            end = node.end_lineno or len(source.splitlines())
            return "\n".join(source.splitlines()[start:end])
    raise ValueError(f"function {func_name!r} not found")


def _finally_block(body: str) -> str:
    """提取函数体的 finally: 块（从 finally: 行到函数末尾）。"""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "finally:":
            return "\n".join(lines[i:])
    return ""


class TestFinallyCleansTmpDir:
    """REQ-CLEAN-1：任务进入终态后 finally 清理 /tmp/lanjian/<task_id>"""

    def test_finally_block_removes_tmp_dir(self):
        body = _extract_function_body(
            SRC_AGENT_TASKS.read_text(encoding="utf-8"), "_execute_agent_task"
        )
        finally_blk = _finally_block(body)
        assert "/tmp/lanjian" in finally_blk, "finally 块应清理 /tmp/lanjian 临时目录"
        assert "shutil.rmtree" in finally_blk, "finally 块应调用 shutil.rmtree 删除目录"

    def test_cleanup_ignores_errors(self):
        body = _extract_function_body(
            SRC_AGENT_TASKS.read_text(encoding="utf-8"), "_execute_agent_task"
        )
        finally_blk = _finally_block(body)
        assert "ignore_errors=True" in finally_blk, "清理应忽略异常（ignore_errors=True）不阻断收尾"


class TestReverifyRepopulatesSource:
    """REQ-CLEAN-2：reverify 兼容已清理目录"""

    def test_reverify_repopulates_zip_and_409_for_repo(self):
        body = _extract_function_body(
            SRC_AGENT_TASKS.read_text(encoding="utf-8"), "reverify_finding"
        )
        assert "_get_project_root" in body, "reverify 目录缺失时应复用 _get_project_root 重新准备源码"
        assert "409" in body, "仓库项目目录缺失时应返回明确的 409 错误"


class TestTaskCleanupRemovesDir:
    """REQ-CLEAN-3：删除任务同步清理临时目录"""

    async def test_cleanup_removes_tmp_dir_and_reports_cleaned_files(self, monkeypatch):
        import app.services.agent.task_cleanup as tc
        from app.models.agent_task import AgentTask

        task = MagicMock(spec=AgentTask)
        task.id = "b9f9a1c0-0000-0000-0000-000000000000"
        task.project_id = "proj-1"
        task.name = "test"
        task.findings_count = 0
        task.verified_count = 0
        task.status = None

        class _FakeResult:
            rowcount = 0

            def scalars(self):
                return self

            def all(self):
                # 非空 → 跳过向量索引清理分支（避免真删索引）
                return ["another-task"]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_FakeResult())
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        removed: list[str] = []
        monkeypatch.setattr(shutil_mod, "rmtree", lambda p, **kw: removed.append(p))

        result = await tc.cleanup_agent_task_resources(db, task)

        expected = f"/tmp/lanjian/{task.id}"
        assert expected in removed, "删除任务应清理 /tmp/lanjian/<task_id> 目录"
        assert expected in result["cleanedFiles"], "cleanedFiles 应反映被清理的目录"