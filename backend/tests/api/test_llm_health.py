"""LLM 健康度指标（2026-09-29 层 5b）。

背景：任务 c6d6cd09 双机的"0 发现"根因是 LLM 每轮截断/空响应，但任务记录
中无任何结构化健康信号——健康度把事件流中的截断/空响应/格式失败/乱码丢弃
统计为机器可读指标，degraded=True 时任务以 completed_with_gaps 收口，报告与
前端据此展示"LLM 输出异常，审计结果不可信"。

契约：
- `_compute_llm_health`：从 agent_events 统计 llm_calls（thinking_start 数）、
  truncations（"max_tokens 截断"）、empty_responses、format_retries、
  garbled_drops；degraded = llm_calls>0 且 (截断+空响应)/llm_calls ≥ 0.5；
- 阈值场景：6/10 截断 → degraded；2/10 → 不 degraded；0 calls → 不 degraded。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints.agent_tasks import _compute_llm_health


def _db_with(rows):
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    return db


def _rows(specs):
    # specs: list of (event_type, message)
    return specs


class TestLLMHealth:
    async def test_heavy_truncation_degraded(self):
        rows = ([("thinking_start", "")] * 10 +
                [("warning", "第 1 轮 LLM 输出被 max_tokens 截断")] * 6)
        health = await _compute_llm_health(_db_with(rows), "t")
        assert health["llm_calls"] == 10
        assert health["truncations"] == 6
        assert health["degraded"] is True

    async def test_moderate_issues_not_degraded(self):
        rows = ([("thinking_start", "")] * 10 +
                [("warning", "被 max_tokens 截断")] * 2 +
                [("warning", "连续收到空响应，使用回退结果")] * 1)
        health = await _compute_llm_health(_db_with(rows), "t")
        assert health["truncations"] == 2
        assert health["empty_responses"] == 1
        assert health["degraded"] is False

    async def test_no_calls_not_degraded(self):
        health = await _compute_llm_health(_db_with([]), "t")
        assert health["llm_calls"] == 0
        assert health["degraded"] is False

    async def test_format_and_garbled_counted(self):
        rows = ([("thinking_start", "")] * 4 +
                [("info", "格式解析失败（第1次），静默重试..."),
                 ("warning", "上一轮输出被截断且包含乱码，本轮决策已丢弃"),
                 ("error", "连续收到空响应，使用回退结果")])
        health = await _compute_llm_health(_db_with(rows), "t")
        assert health["format_retries"] == 1
        assert health["garbled_drops"] == 1
        assert health["empty_responses"] == 1
        assert health["degraded"] is False  # 1/4 < 0.5
