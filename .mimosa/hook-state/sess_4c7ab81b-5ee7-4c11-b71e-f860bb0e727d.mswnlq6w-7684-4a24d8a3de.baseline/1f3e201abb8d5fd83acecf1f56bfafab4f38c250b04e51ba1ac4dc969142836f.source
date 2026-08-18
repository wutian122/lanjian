"""
Q2 修复测试：SandboxBrowserTool 浏览器验证

根因：sandbox 镜像装了 chromium+playwright 但无封装工具，verification 全程
      0 次调用浏览器，XSS/重定向类漏洞只能判 not_reproducible。
修复：新增 SandboxBrowserTool 封装 playwright，复用 sandbox_manager.execute_python。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent.tools.sandbox_tool import SandboxBrowserTool, SandboxBrowserInput
from app.services.agent.tools.base import ToolResult


def _make_browser_tool(execute_python_ret=None):
    """构造 SandboxBrowserTool，mock sandbox_manager"""
    tool = SandboxBrowserTool.__new__(SandboxBrowserTool)
    tool._call_count = 0
    tool._total_duration_ms = 0
    tool.sandbox_manager = MagicMock()
    tool.sandbox_manager.is_available = True
    tool.sandbox_manager.initialize = AsyncMock()
    tool.sandbox_manager.execute_python = AsyncMock(return_value=execute_python_ret or {
        "success": True,
        "stdout": '{"title": "Test Page", "url": "http://target/", "status": "ok"}',
        "stderr": "",
        "exit_code": 0,
    })
    return tool


class TestSandboxBrowserTool:
    """验证 SandboxBrowserTool 行为"""

    def test_browser_input_schema(self):
        """Q2: SandboxBrowserInput 含 action/url/selector/script/timeout"""
        schema = SandboxBrowserInput.model_fields
        assert "action" in schema
        assert "url" in schema
        assert "selector" in schema
        assert "script" in schema
        assert "timeout" in schema

    def test_browser_tool_name(self):
        """Q2: 工具名为 sandbox_browser"""
        tool = _make_browser_tool()
        assert tool.name == "sandbox_browser"

    @pytest.mark.asyncio
    async def test_browser_navigate(self):
        """Q2: navigate 成功返回页面信息"""
        tool = _make_browser_tool()
        result = await tool.execute(action="navigate", url="http://target/xss")
        assert isinstance(result, ToolResult)
        assert result.success is True
        # execute_python 被调用
        tool.sandbox_manager.execute_python.assert_called_once()
        # 生成的脚本含 chromium.launch 与 --no-sandbox
        call_args = tool.sandbox_manager.execute_python.call_args
        script = call_args.kwargs.get("code") or call_args.args[0]
        assert "chromium" in script
        assert "--no-sandbox" in script

    @pytest.mark.asyncio
    async def test_browser_eval(self):
        """Q2: eval 执行 JS 返回结果"""
        tool = _make_browser_tool(execute_python_ret={
            "success": True,
            "stdout": '{"result": "<script>alert(1)</script>"}',
            "stderr": "",
            "exit_code": 0,
        })
        result = await tool.execute(
            action="eval",
            url="http://target/xss",
            script="document.body.innerHTML",
        )
        assert result.success is True
        call_args = tool.sandbox_manager.execute_python.call_args
        script = call_args.kwargs.get("code") or call_args.args[0]
        assert "evaluate" in script or "eval" in script.lower()

    @pytest.mark.asyncio
    async def test_browser_timeout_graceful(self):
        """Q2: 超时/失败优雅降级返回 ToolResult(success=False)"""
        tool = _make_browser_tool(execute_python_ret={
            "success": False,
            "stdout": "",
            "stderr": "TimeoutError: navigation timeout",
            "exit_code": 1,
        })
        result = await tool.execute(action="navigate", url="http://slow-target/", timeout=5)
        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_browser_sandbox_unavailable(self):
        """Q2: 沙箱不可用时返回结构化错误"""
        tool = _make_browser_tool()
        tool.sandbox_manager.is_available = False
        result = await tool.execute(action="navigate", url="http://target/")
        assert result.success is False
        assert "沙箱" in (result.error or "") or "sandbox" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_browser_chromium_no_sandbox_arg(self):
        """Q2: 生成的脚本含 --no-sandbox --headless（Docker 必需）"""
        tool = _make_browser_tool()
        await tool.execute(action="navigate", url="http://target/")
        call_args = tool.sandbox_manager.execute_python.call_args
        script = call_args.kwargs.get("code") or call_args.args[0]
        assert "--no-sandbox" in script
        assert "--headless" in script
        assert "--disable-dev-shm-usage" in script

    @pytest.mark.asyncio
    async def test_browser_get_text_selector_none_fallback(self):
        """Q2: get_text 的 selector=None 时 fallback 到 body，不生成 inner_text('None')"""
        tool = _make_browser_tool()
        await tool.execute(action="get_text", url="http://target/")
        call_args = tool.sandbox_manager.execute_python.call_args
        script = call_args.kwargs.get("code") or call_args.args[0]
        # 不应出现 inner_text('None') 这种错误调用
        assert "inner_text('None')" not in script
        assert "inner_text(None)" not in script
        # 应 fallback 到 body
        assert 'inner_text("body")' in script

    @pytest.mark.asyncio
    async def test_browser_click_requires_selector(self):
        """Q2: click 缺 selector 返回结构化错误"""
        tool = _make_browser_tool()
        result = await tool.execute(action="click", url="http://target/")
        assert result.success is False
        assert "selector" in (result.error or "").lower()
