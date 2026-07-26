"""
R1 修复测试：RAG 工具参数健壮性

根因：RAGQueryTool._execute(query: str) 把 query 设为必填位置参数，
      LLM 漏传时抛 TypeError: missing 1 required positional argument: 'query'
      （8 次调用中 1 次）。
修复：query 改默认 None + 函数内判空返回友好 ToolResult；
      base.py execute 入口对 args_schema 必填字段校验。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.agent.tools.rag_tool import RAGQueryTool
from app.services.agent.tools.base import AgentTool, ToolResult


def _make_rag_tool():
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(return_value=[])
    return RAGQueryTool(retriever=retriever)


class TestRAGQueryParameterRobustness:
    """验证 rag_query 参数健壮性"""

    @pytest.mark.asyncio
    async def test_rag_query_missing_param_returns_structured_error(self):
        """R1: 缺 query 参数返回 ToolResult(success=False)，不抛 TypeError"""
        tool = _make_rag_tool()
        # 不传 query
        result = await tool.execute(top_k=5)
        assert isinstance(result, ToolResult)
        assert result.success is False
        # 错误信息提及 query
        assert "query" in (result.error or "").lower() or "query" in str(result.data).lower()

    @pytest.mark.asyncio
    async def test_rag_query_none_query_returns_structured_error(self):
        """R1: query=None 返回结构化错误"""
        tool = _make_rag_tool()
        result = await tool.execute(query=None)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rag_query_empty_string_returns_structured_error(self):
        """R1: query='' 返回结构化错误"""
        tool = _make_rag_tool()
        result = await tool.execute(query="")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rag_query_valid_query_calls_retriever(self):
        """R1: 正常 query 调用 retriever.retrieve"""
        tool = _make_rag_tool()
        await tool.execute(query="用户登录处理")
        tool.retriever.retrieve.assert_called_once()
        call_kwargs = tool.retriever.retrieve.call_args.kwargs
        assert call_kwargs["query"] == "用户登录处理"
