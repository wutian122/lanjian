"""
B5 修复测试：search_code 工具参数契约明确

根因：LLM 偶尔以 query/pattern 调用 search_code，触发
      'missing 1 required positional argument: keyword'
修复：强化 description 明确参数名为 keyword
"""
import pytest

from app.services.agent.tools import FileSearchTool
from app.services.agent.tools.base import ToolResult


class TestSearchCodeParameterContract:
    """验证 search_code 工具参数契约"""

    def test_description_explicitly_states_keyword_param(self, temp_project_dir):
        """description 必须明确声明参数名为 keyword"""
        tool = FileSearchTool(temp_project_dir)
        desc = tool.description
        assert "keyword" in desc, "description 应含 keyword 参数名"
        # 明确提示不得使用别名
        assert ("query" in desc.lower() and "不要" in desc) or "必须为 keyword" in desc or "参数名: keyword" in desc, (
            "description 应明确提示参数名必须为 keyword"
        )

    @pytest.mark.asyncio
    async def test_search_code_with_correct_keyword_succeeds(self, temp_project_dir):
        """正确参数名 keyword 调用，搜索成功"""
        tool = FileSearchTool(temp_project_dir)
        result = await tool.execute(keyword="cursor.execute")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_search_code_missing_keyword_returns_structured_error(self, temp_project_dir):
        """缺失 keyword 时返回 ToolResult(success=False)，不抛未捕获 TypeError"""
        tool = FileSearchTool(temp_project_dir)
        # 不传 keyword，只传其他参数
        result = await tool.execute(file_pattern="*.py")
        assert isinstance(result, ToolResult)
        assert result.success is False
        assert "keyword" in result.error.lower() or "keyword" in str(result.data).lower()
