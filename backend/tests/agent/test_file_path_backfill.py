"""
D4 修复测试：file_path 空时从 source/snippet 回填

根因：Dockerfile 类 finding 的 file_path 为空，现有回填仅从 title/description
      正则提取，未覆盖 source/matched_pattern 字段，导致无法溯源。
修复：提取 _extract_finding_file_path，候选字段增加 source/matched_pattern。
"""
import pytest
from app.api.v1.endpoints.agent_tasks import _extract_finding_file_path


class TestFilePathBackfill:
    """验证 file_path 回填逻辑"""

    def test_file_path_backfill_from_source(self):
        """D4: file_path 空但 source 含路径时从 source 回填"""
        finding = {
            "file_path": None,
            "source": "containers/app/Dockerfile:69",
            "title": "Dockerfile 中使用 sudo",
        }
        result = _extract_finding_file_path(finding)
        assert result is not None
        assert "Dockerfile" in result

    def test_file_path_backfill_from_matched_pattern(self):
        """D4: 从 matched_pattern 回填"""
        finding = {
            "file_path": None,
            "matched_pattern": "src/auth/jwt.py",
        }
        result = _extract_finding_file_path(finding)
        assert result == "src/auth/jwt.py"

    def test_file_path_preserved_when_present(self):
        """已有 file_path 时不被覆盖"""
        finding = {"file_path": "existing/path.py", "source": "other/Dockerfile"}
        result = _extract_finding_file_path(finding)
        assert result == "existing/path.py"

    def test_file_path_from_location(self):
        """从 location 字段提取（含行号）"""
        finding = {"file_path": None, "location": "src/main.py:42:10"}
        result = _extract_finding_file_path(finding)
        assert result == "src/main.py"

    def test_file_path_none_when_no_path_anywhere(self):
        """所有字段都无路径时返回 None（D2 会排除，不计入 files_with_findings）"""
        finding = {
            "file_path": None,
            "source": "通用配置问题",
            "title": "无具体文件的配置告警",
            "description": "这是一段没有文件路径的描述",
        }
        result = _extract_finding_file_path(finding)
        assert result is None
