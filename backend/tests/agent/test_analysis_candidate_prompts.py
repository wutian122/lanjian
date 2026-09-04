"""
sandbox-verification-hard-gate Task 9：Analysis 分层候选提示词改造

生产实证（nginx 任务 194 万 tokens 0 findings）：Analysis 提示词
"宁可漏报，不可误报" + 自验可利用性导致 LLM 主动放弃产出。改造为
分层候选制——高置信（≥0.7）直接报告；低置信（0.1-0.7）作为候选输出
并标 needs_verification=true（交沙箱证实/证伪）；防幻觉规则保留
（候选也必须有 read_file 实际观察支撑）。

覆盖（结构化断言——提示词改造的测试是断言关键语义句）：
- ANALYSIS_SYSTEM_PROMPT 含分层候选关键句（高置信/低置信/needs_verification）
- 组装后的完整系统提示词不再含"宁可漏报，不可误报"绝对表述
  （ANTI_HALLUCINATION_ENHANCED / FILE_VALIDATION_RULES 同步改写）
- 防幻觉规则保留（"实际看到"约束仍在）
- submit_findings 协议说明段与分层语义衔接（findings 可含候选、无矛盾表述）
- _run_forced_summary 提示词含维度级产出下限（候选或书面豁免）
- 归一化豁免：needs_verification=true 的 confidence 0.5 候选通过
  orchestrator._normalize_finding 与 is_strict_finding 不被丢弃；
  confidence 0.05 仍丢弃；无候选标记的 0.5 仍丢弃
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.agents.analysis import (
    ANALYSIS_SYSTEM_PROMPT,
    SUBMIT_FINDINGS_PROTOCOL_NOTE,
    AnalysisAgent,
)
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.prompts import build_enhanced_prompt
from app.services.agent.strict_finding import is_strict_finding

_TIMEOUT_CONFIG = {
    "llm_first_token_timeout": 30,
    "llm_stream_timeout": 60,
    "agent_timeout": 1800,
    "sub_agent_timeout": 600,
    "tool_timeout": 60,
}


def _make_service():
    service = MagicMock()
    service.backend_capabilities = None
    service.get_agent_timeout_config = MagicMock(return_value=_TIMEOUT_CONFIG)
    service.config = SimpleNamespace(max_tokens=8192)
    return service


def _make_emitter():
    e = MagicMock()
    for name in [
        "emit", "emit_info", "emit_warning", "emit_error", "emit_thinking",
        "emit_tool_call", "emit_tool_result", "emit_finding", "emit_progress",
        "emit_phase_start", "emit_phase_complete", "emit_task_complete",
    ]:
        setattr(e, name, AsyncMock())
    return e


# ============ 1. 系统提示词分层候选语义 ============

class TestAnalysisPromptTieredCandidates:
    """ANALYSIS_SYSTEM_PROMPT 从"宁可漏报"改为分层候选产出策略。"""

    def test_prompt_contains_tiered_candidate_semantics(self):
        """高置信 ≥0.7 直接报告；低置信 0.1-0.7 候选 + needs_verification=true。"""
        prompt = ANALYSIS_SYSTEM_PROMPT
        # 分层阈值表述
        assert "0.7" in prompt
        assert "0.1" in prompt
        # 候选机制关键语义
        assert "候选" in prompt
        assert "needs_verification" in prompt
        # 候选交沙箱验证证实/证伪的语义
        assert "沙箱" in prompt and ("证实" in prompt or "证伪" in prompt)

    def test_prompt_does_not_contain_absolutist_false_negative_motto(self):
        """系统提示词本体不再含"宁可漏报"绝对表述。"""
        assert "宁可漏报" not in ANALYSIS_SYSTEM_PROMPT
        assert "不可误报" not in ANALYSIS_SYSTEM_PROMPT

    def test_full_enhanced_prompt_drops_absolutist_motto(self):
        """组装后的完整提示词（含 ANTI_HALLUCINATION_ENHANCED 与
        FILE_VALIDATION_RULES）也不含"宁可漏报，不可误报"——共享提示词
        片段已同步改写。"""
        full = build_enhanced_prompt(
            base_prompt=ANALYSIS_SYSTEM_PROMPT,
            include_principles=True,
            include_priorities=True,
            include_tools=True,
            include_validation=True,
            include_anti_hallucination=True,
            include_coverage_matrix=False,
            include_control_driven=False,
            include_contract=True,
        )
        assert "宁可漏报" not in full
        assert "不可误报" not in full

    def test_anti_hallucination_rules_preserved(self):
        """防幻觉规则保留：候选也必须有 read_file 实际观察支撑。"""
        prompt = ANALYSIS_SYSTEM_PROMPT
        # "实际看到/实际读取"约束仍在
        assert "实际看到" in prompt or "实际读取" in prompt
        # 知识示例 ≠ 项目代码 的幻觉警戒仍在
        assert "幻觉" in prompt

    def test_prompt_does_not_make_self_verification_a_prerequisite(self):
        """Analysis 不得把"验证可利用性"作为报告前置条件——可利用性
        验证是 Verification Agent 的职责。"""
        prompt = ANALYSIS_SYSTEM_PROMPT
        assert "Verification" in prompt or "验证 Agent" in prompt or "沙箱验证" in prompt


# ============ 2. submit_findings 协议说明段衔接 ============

class TestSubmitFindingsNoteCandidateAlignment:
    """Task 8 的 SUBMIT_FINDINGS_PROTOCOL_NOTE 与分层候选语义自然衔接。"""

    def test_note_allows_candidates_in_findings(self):
        """协议说明段明确 findings 数组可含低置信候选。"""
        note = SUBMIT_FINDINGS_PROTOCOL_NOTE
        assert "候选" in note
        assert "needs_verification" in note

    def test_note_has_no_contradiction_with_tiered_policy(self):
        """协议段不含"宁可漏报/仅高置信/不确定就不要报告"类矛盾表述，
        且保留"凭推测提交属于幻觉"的防幻觉约束（推测=无 read_file 依据，
        候选=有依据但结论不确定，两者不矛盾）。"""
        note = SUBMIT_FINDINGS_PROTOCOL_NOTE
        assert "宁可漏报" not in note
        assert "幻觉" in note  # 防幻觉约束保留


# ============ 3. 强制总结提示词维度级下限 ============

class TestForcedSummaryDimensionFloor:
    """_run_forced_summary 提示词要求每个未覆盖维度给候选或书面豁免。
    Task 10 负责解析与 data 回写，本任务只锁定提示词文本。"""

    @pytest.mark.asyncio
    async def test_forced_summary_prompt_requires_candidate_or_waiver(self):
        agent = AnalysisAgent(
            llm_service=_make_service(),
            tools={},
            event_emitter=_make_emitter(),
        )

        captured = {}

        async def _fake_stream(messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return "", 0  # 空输出，解析走默认空 findings

        agent.stream_llm_call = _fake_stream

        await agent._run_forced_summary([])

        last_user = [
            m for m in captured["messages"] if m.get("role") == "user"
        ][-1]["content"]
        # 维度级产出下限
        assert "维度" in last_user
        # 候选或书面豁免二选一
        assert "候选" in last_user
        assert "豁免" in last_user
        # 强制总结 JSON 契约含分层字段（与 Final Answer 契约一致）
        assert "confidence" in last_user
        assert "needs_verification" in last_user


# ============ 4. 归一化豁免：低置信候选不被 0.7 阈值丢弃 ============

def _make_orch():
    """绕过 __init__；_runtime_context 为空 → 跳过文件路径存在性校验，
    隔离 confidence 阈值行为。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._runtime_context = {}
    return agent


class TestNormalizationCandidateExemption:
    """spec finding-output-floor：needs_verification=true 的低置信候选
    （0.1 ≤ confidence < 0.7）SHALL 通过归一化进入后续流程；
    confidence < 0.1 的纯噪声仍丢弃。"""

    def test_candidate_confidence_0_5_passes_normalize(self):
        orch = _make_orch()
        finding = {
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "title": "疑似 SQL 注入（中间层部分缓解）",
            "description": "疑似可注入但被部分缓解，需沙箱证实",
            "file_path": "",
            "line_start": 0,
            "confidence": 0.5,
            "needs_verification": True,
        }
        result = orch._normalize_finding(dict(finding))
        assert result is not None
        assert result["needs_verification"] is True

    def test_low_confidence_without_candidate_flag_still_dropped(self):
        """无 needs_verification 标记的 0.5 发现维持旧行为（0.7 闸丢弃）。"""
        orch = _make_orch()
        finding = {
            "vulnerability_type": "sql_injection",
            "severity": "medium",
            "title": "低置信无候选标记",
            "description": "needs_verification 缺省/False",
            "file_path": "",
            "line_start": 0,
            "confidence": 0.5,
            "needs_verification": False,
        }
        assert orch._normalize_finding(dict(finding)) is None

    def test_below_floor_confidence_0_05_still_dropped_even_as_candidate(self):
        """confidence < 0.1 即使标了候选也丢弃（纯猜测噪声）。"""
        orch = _make_orch()
        finding = {
            "vulnerability_type": "ssrf",
            "severity": "low",
            "title": "近乎无依据的猜测",
            "description": "confidence 0.05",
            "file_path": "",
            "line_start": 0,
            "confidence": 0.05,
            "needs_verification": True,
        }
        assert orch._normalize_finding(dict(finding)) is None

    def test_high_confidence_0_9_still_passes(self):
        """高置信发现不受豁免改造影响。"""
        orch = _make_orch()
        finding = {
            "vulnerability_type": "command_injection",
            "severity": "critical",
            "title": "确认的命令注入",
            "description": "污点链完整",
            "file_path": "",
            "line_start": 0,
            "confidence": 0.9,
            "needs_verification": True,
        }
        assert orch._normalize_finding(dict(finding)) is not None


class TestStrictFindingCandidateExemption:
    """agent_tasks 落库前 is_strict_finding 闸同步豁免候选。"""

    def test_candidate_with_file_location_passes_strict(self):
        finding = {
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "title": "疑似 SQL 注入候选",
            "description": "有实际代码依据但置信度不足，交沙箱验证",
            "file_path": "app/db.py",
            "line_start": 42,
            "confidence": 0.4,
            "needs_verification": True,
        }
        assert is_strict_finding(finding) is True

    def test_low_confidence_without_flag_fails_strict(self):
        finding = {
            "vulnerability_type": "sql_injection",
            "severity": "medium",
            "title": "低置信无标记",
            "description": "旧行为：<0.7 不通过",
            "file_path": "app/db.py",
            "line_start": 42,
            "confidence": 0.4,
            "needs_verification": False,
        }
        assert is_strict_finding(finding) is False

    def test_below_floor_candidate_fails_strict(self):
        finding = {
            "vulnerability_type": "ssrf",
            "severity": "low",
            "title": "0.05 噪声",
            "description": "低于候选下界",
            "file_path": "app/x.py",
            "line_start": 7,
            "confidence": 0.05,
            "needs_verification": True,
        }
        assert is_strict_finding(finding) is False
