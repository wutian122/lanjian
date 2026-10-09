"""P8 修复测试（2026-10-07，生产四任务实证的问题）：
1. 工具整型强转（LLM 发 "30" 字符串不再 TypeError）
2. 文本协议：无参数 finish/summarize 省略 Action Input 不判失败
3. _save_findings 集成：确认态 finding 被过滤时写 filtered_observations
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestCoerceIntP8:
    def test_string_int_coerced(self):
        from app.services.agent.tools.file_tool import _coerce_int

        assert _coerce_int("30") == 30
        assert _coerce_int(" 690 ") == 690
        assert _coerce_int(15) == 15
        assert _coerce_int(True) == 1

    def test_none_and_empty_to_default(self):
        from app.services.agent.tools.file_tool import _coerce_int

        assert _coerce_int(None, default=100) == 100
        assert _coerce_int("", default=60) == 60
        assert _coerce_int(None) is None

    def test_unparseable_to_default(self):
        from app.services.agent.tools.file_tool import _coerce_int

        assert _coerce_int("abc", default=0) == 0
        assert _coerce_int([], default=None) is None

    def test_max_lines_default(self):
        from app.services.agent.tools.file_tool import _coerce_int

        assert _coerce_int(None, default=500) == 500


class TestNoArgActionParseP8:
    def _parse(self, text):
        from app.services.agent.agents.orchestrator import OrchestratorAgent

        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent.config = MagicMock()
        agent.config.name = "Orchestrator"
        return agent._parse_llm_response(text)

    def test_finish_without_input_parses_empty(self):
        text = (
            "Thought: 审计完成，可以结束\n"
            "Action: finish\n"
        )
        step = self._parse(text)
        assert step is not None
        assert step.action == "finish"
        assert step.action_input == {}

    def test_summarize_without_input_parses_empty(self):
        text = "Thought: 汇总\nAction: summarize\n"
        step = self._parse(text)
        assert step is not None and step.action_input == {}

    def test_other_action_without_input_still_fails(self):
        text = (
            "Thought: 调度\n"
            "Action: dispatch_agent\n"
        )
        step = self._parse(text)
        assert step is None


class TestSaveFindingsIntegrationP8:
    def test_confirmed_finding_filtered_produces_observation(self):
        """集成回归：is_strict 过滤确认态 finding → filtered_observations 留痕。

        锁定 P7-5 调用点曾出现的 NameError 炸弹（函数名笔误+变量未定义），
        过滤路径在任何 DB 写入前返回，dummy db 不会被触碰。
        """
        from app.api.v1.endpoints.agent_tasks import _save_findings

        finding = {
            "title": "某确认态发现",
            "vulnerability_type": "hardcoded_secret",
            # 触发 is_strict_finding 失败：无行号
            "file_path": "app/config.py",
            "line_start": None,
            "verification_status": "confirmed",
            "confidence": 0.9,
        }
        logged: list = []
        db = MagicMock()
        db.commit = AsyncMock()
        saved = asyncio.run(_save_findings(
            db=db, task_id="t-p8", findings=[finding],
            project_root=None, filtered_observations=logged,
        ))
        assert saved == 0
        assert len(logged) == 1, f"确认态被过滤必须留痕: {logged}"
        entry = logged[0]
        assert entry["gate"] == "filtered_confirmed_finding"
        assert entry["verification_status"] == "confirmed"
        assert "is_strict_finding" in entry["reason"]

    def test_non_confirmed_filtered_no_observation(self):
        """needs_context 被过滤属正常质量门，不专门留痕。"""
        from app.api.v1.endpoints.agent_tasks import _save_findings

        finding = {
            "title": "证据不足项", "vulnerability_type": "other",
            "file_path": "a.java", "line_start": None,
            "verification_status": "needs_context",
        }
        logged: list = []
        db = MagicMock()
        db.commit = AsyncMock()
        asyncio.run(_save_findings(
            db=db, task_id="t-p8b", findings=[finding],
            filtered_observations=logged,
        ))
        assert logged == []
