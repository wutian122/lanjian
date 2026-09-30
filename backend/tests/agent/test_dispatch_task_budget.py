"""dispatch 任务预算守卫（2026-09-29 层 2b）。

背景（任务 c6d6cd09 / 327b6430）：orchestrator 生成的 dispatch 任务描述超
预算 → length 截断 → 坏 JSON/second 崩坏（"~ff files"级乱码）传给子 Agent。
本守卫在任务文本出站前压缩：未超限原样；超限先试图 LLM 摘要（保留文件路径
与维度关键词，一次性小预算调用）；摘要不可用/失败/超长时回落确定性压缩
（头 2/3 + 尾 1/3 + 省略统计）。

契约：
- `_ensure_dispatch_task_budget`（async）：≤3500 字符原样返回；
- 超限 + LLM 摘要成功（非空且 ≤预算）→ 用摘要；
- 超限 + 摘要失败/空/超长 → 确定性压缩，长度 ≤ 预算且含头尾采样。
"""
import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_orch() -> OrchestratorAgent:
    orch = OrchestratorAgent.__new__(OrchestratorAgent)
    orch.llm_service = MagicMock()
    orch.event_emitter = MagicMock()
    orch.event_emitter.emit = AsyncMock()
    return orch


SHORT = "审计 src/http/modules/ngx_http_proxy_module.c 的路径遍历风险"
LONG = ("## Scope (only these files)\n" + "src/http/modules/ngx_http_proxy_module.c\n" * 10 +
        "## Focus dimensions\n" + "路径遍历/SSRF 维度分析要点\n" * 200 + "## Tail marker: 完整报告要求\n" * 5)


class TestDispatchTaskBudget:
    def test_within_budget_unchanged(self):
        orch = _make_orch()
        out = asyncio.run(orch._ensure_dispatch_task_budget(SHORT))
        assert out == SHORT
        orch.llm_service.chat_completion.assert_not_called()

    def test_overbudget_llm_summary_used(self):
        orch = _make_orch()
        orch.llm_service.chat_completion = AsyncMock(
            return_value={"content": "摘要：审 ngx_http_proxy_module.c 路径遍历"})
        out = asyncio.run(orch._ensure_dispatch_task_budget(LONG))
        assert out == "摘要：审 ngx_http_proxy_module.c 路径遍历"

    def test_overbudget_llm_failure_falls_back(self):
        orch = _make_orch()
        orch.llm_service.chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
        out = asyncio.run(orch._ensure_dispatch_task_budget(LONG))
        assert len(out) <= orch.DISPATCH_TASK_CHAR_BUDGET
        assert "ngx_http_proxy_module.c" in out
        assert "省略" in out

    def test_overbudget_llm_empty_falls_back(self):
        orch = _make_orch()
        orch.llm_service.chat_completion = AsyncMock(return_value={"content": ""})
        out = asyncio.run(orch._ensure_dispatch_task_budget(LONG))
        assert 0 < len(out) <= orch.DISPATCH_TASK_CHAR_BUDGET

    def test_overbudget_llm_oversized_summary_compressed(self):
        orch = _make_orch()
        orch.llm_service.chat_completion = AsyncMock(
            return_value={"content": "x" * (orch.DISPATCH_TASK_CHAR_BUDGET + 100)})
        out = asyncio.run(orch._ensure_dispatch_task_budget(LONG))
        assert len(out) <= orch.DISPATCH_TASK_CHAR_BUDGET

    def test_compress_keeps_head_and_tail(self):
        out = OrchestratorAgent._compress_dispatch_task(LONG)
        assert len(out) <= OrchestratorAgent.DISPATCH_TASK_CHAR_BUDGET
        assert out.startswith("## Scope")
        assert "省略" in out
