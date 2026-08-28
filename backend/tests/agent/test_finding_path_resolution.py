"""#4-5 修复回归测试：LLM 路径形态漂移不得误杀真实 finding。

E2E 实证（服务器日志铁证）：ZIP 样本文件位于 project_root/src/ 子目录时，
LLM 输出的裸文件名（"vuln.js"）/ 沙箱前缀路径（"/workspace/src/vuln.js"）被
orchestrator._validate_file_path 与 _save_findings 双重 isfile 校验误杀，
已通过确定性沙箱验证的 findings 反而无法落库（15 声明 → 仅 5 落库）。

本测试锁定统一路径解析器 resolve_project_file 的语义：
- 裸文件名命中 project_root/src/ 层级
- /workspace/... 沙箱前缀剥离
- ":行号" 剥离
- basename 限深度递归兜底
- 找不到仍返回 None（幻觉过滤能力不回退）
"""
import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.utils.finding_path import resolve_project_file


@pytest.fixture
def nested_project(tmp_path):
    """模拟 E2E 样本结构：ZIP 内带 src/ 目录，含嵌套 main/ 子目录。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print(1)\n")
    (src / "vuln.js").write_text("console.log(1)\n")
    (src / "vuln.jsp").write_text("<% out.print(request.getParameter('x')); %>\n")
    main = src / "main"
    main.mkdir()
    (main / "VulnServlet.java").write_text("class VulnServlet {}\n")
    return tmp_path


class TestResolveProjectFile:
    def test_src_prefixed_relative(self, nested_project):
        assert resolve_project_file(str(nested_project), "src/app.py") == "src/app.py"

    def test_bare_filename_hits_src_subdir(self, nested_project):
        # E2E 主场景：LLM 输出裸文件名，文件实际在 src/ 下
        assert resolve_project_file(str(nested_project), "app.py") == "src/app.py"
        assert resolve_project_file(str(nested_project), "vuln.js") == "src/vuln.js"

    def test_sandbox_prefix_stripped(self, nested_project):
        assert resolve_project_file(str(nested_project), "/workspace/src/vuln.js") == "src/vuln.js"

    def test_line_number_stripped(self, nested_project):
        assert resolve_project_file(str(nested_project), "app.py:36") == "src/app.py"

    def test_basename_recursive_fallback(self, nested_project):
        # 嵌套子目录中的文件按 basename 限深度递归定位
        assert resolve_project_file(str(nested_project), "VulnServlet.java") == "src/main/VulnServlet.java"

    def test_backslash_separators(self, nested_project):
        assert resolve_project_file(str(nested_project), "src\\app.py") == "src/app.py"

    def test_absolute_host_path(self, nested_project):
        abs_path = str(nested_project / "src" / "app.py")
        assert resolve_project_file(str(nested_project), abs_path) == "src/app.py"

    def test_missing_file_returns_none(self, nested_project):
        # 幻觉过滤能力不回退：找不到仍返回 None
        assert resolve_project_file(str(nested_project), "notexist.py") is None

    def test_empty_path_returns_none(self, nested_project):
        assert resolve_project_file(str(nested_project), "") is None
        assert resolve_project_file(str(nested_project), "   ") is None

    def test_missing_project_root_returns_none(self, nested_project):
        assert resolve_project_file(str(nested_project / "ghost"), "app.py") is None


class TestValidateFilePath:
    def test_bare_filename_now_accepted(self, nested_project):
        # orchestrator 侧入口：runtime_context.project_root + 裸文件名
        agent = object.__new__(OrchestratorAgent)
        agent._runtime_context = {"project_root": str(nested_project)}
        assert agent._validate_file_path("vuln.js") is True
        assert agent._validate_file_path("/workspace/src/vuln.js") is True

    def test_hallucination_still_rejected(self, nested_project):
        agent = object.__new__(OrchestratorAgent)
        agent._runtime_context = {"project_root": str(nested_project)}
        assert agent._validate_file_path("ghost_file.py") is False

    def test_no_project_root_still_permissive(self):
        agent = object.__new__(OrchestratorAgent)
        agent._runtime_context = {}
        assert agent._validate_file_path("anything.py") is True


class TestSaveFindingsNormalization:
    def test_src_nested_finding_persisted_with_normalized_path(self, nested_project):
        from app.api.v1.endpoints.agent_tasks import _save_findings

        class FakeDB:
            def __init__(self):
                self.added = []

            def add(self, obj):
                self.added.append(obj)

            async def commit(self):
                pass

            async def rollback(self):
                pass

        db = FakeDB()
        finding = {
            "title": "Node.js 命令注入漏洞",
            "description": "child_process.exec 拼接用户输入，可执行任意命令",
            "vulnerability_type": "command_injection",
            "severity": "high",
            "file_path": "vuln.js",
            "line_start": 12,
            "confidence": 0.9,
        }
        saved = asyncio.run(
            _save_findings(db, "task-1", [finding], project_root=str(nested_project))
        )
        assert saved == 1
        assert len(db.added) == 1
        # 落库路径必须是解析后的真实相对路径，否则后续 reverify/展示仍不可用
        assert db.added[0].file_path == "src/vuln.js"

    def test_hallucination_still_skipped(self, nested_project):
        from app.api.v1.endpoints.agent_tasks import _save_findings

        class FakeDB:
            def __init__(self):
                self.added = []

            def add(self, obj):
                self.added.append(obj)

            async def commit(self):
                pass

            async def rollback(self):
                pass

        db = FakeDB()
        finding = {
            "title": "不存在文件的漏洞",
            "description": "描述某不存在的文件",
            "vulnerability_type": "xss",
            "severity": "medium",
            "file_path": "ghost_file.py",
            "line_start": 1,
            "confidence": 0.9,
        }
        saved = asyncio.run(
            _save_findings(db, "task-2", [finding], project_root=str(nested_project))
        )
        assert saved == 0
        assert len(db.added) == 0
