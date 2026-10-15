"""P12 Wave 0 · F8 回归夹具（analysis 三批全 success=False 不得 completed+100 分）。

事件背景（任务 8a3a713f 收口链）：analysis 批次三连失败/零产出后，编排器仍可能
以"正常完成"收口，端点对 0 findings 任务打 100 分——"崩坏没干活"被呈现成
"项目非常安全"。

Wave 2.2/3 契约（方案第二章 2.2 + Wave 3「失败调度记账」）：
- 失败返回体（orchestrator success=False/子 Agent success=False 但 data.findings
  非空）不得被 result.success 单门挡住，保全通道与落库通道一致；
- max_dispatch 守卫必须区分两类零产出：
  「崩溃零产出（failed_zero_output，analysis data 标记，见 F3）」与
  「下限违规（模型拒交 output_floor_violated）」，两种文案与 gate observation
  分开记账；
- 全败收口不得 completed + 100：metadata coverage_bypassed 必须置位，
  评分侧 gaps=True 时 0 findings 返回 None（无法评分）。
- 失败调度占名额（计数陷阱显式化：3 次失败后第 4 次调度被守卫拦截，不重跑）。

T0 阶段：守卫失败种类记账用例 RED（当前只有"产出下限违规/自动放行"两种文案，
不区分崩溃零产出）；评分与 bypass 断言为回归基线。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent.agents.base import AgentResult
from app.services.agent.agents.orchestrator import OrchestratorAgent


_DISPATCH_TEXT = (
    'Thought: 再派 analysis\n'
    'Action: dispatch_agent\n'
    'Action Input: {"agent": "analysis", "task": "继续审计", "context": ""}'
)
_FINISH_TEXT = (
    "Thought: 收口\nAction: finish\nAction Input: {}"
)


class _FakeAnalysis:
    """success=False 的 analysis 子 Agent（批 1 charwall 错误退出形态）。"""

    def __init__(self):
        self.run_count = 0
        self.is_soft_stopped = False
        # 真实 BaseAgent 属性（_dispatch_agent 调度链会读）
        self._registered = True
        self.config = SimpleNamespace(agent_type=SimpleNamespace(value="analysis"))

    def set_parent_id(self, parent_id):
        self.parent_id = parent_id

    def _register_to_registry(self, task=None):
        pass

    def reset_dispatch_cancel(self):
        pass

    def cancel(self):
        pass

    async def run(self, sub_input):
        self.run_count += 1
        return AgentResult(
            success=False,
            error="连续收到空响应，使用回退结果",
            data={
                "findings": [],
                "dimension_gaps_reported": {},
                "output_floor_violated": False,
                # Wave 1.5 契约字段（F3 夹具）：崩溃零产出，非模型拒交
                "failed_zero_output": True,
            },
            iterations=6,
            tool_calls=6,
            tokens_used=12000,
            duration_ms=30000,
        )


def _make_orchestrator(monkeypatch, fake_analysis):
    agent = OrchestratorAgent(llm_service=SimpleNamespace(), tools={})
    agent.sub_agents = {"analysis": fake_analysis}
    monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
    monkeypatch.setattr(
        agent,
        "_run_semgrep_prescan",
        AsyncMock(return_value={"findings": [], "hot_files": [], "scan_success": False}),
    )
    monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
    for name in (
        "emit_thinking", "emit_event", "emit_llm_decision", "emit_llm_thought",
        "emit_llm_observation", "emit_llm_action",
    ):
        monkeypatch.setattr(agent, name, AsyncMock())
    monkeypatch.setattr(agent, "check_messages", lambda: [])
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    monkeypatch.setattr(agent, "_inject_trace_summary", AsyncMock())
    return agent


def _scripted_stream(script):
    calls = []

    async def _fake_stream(messages, **kwargs):
        i = len(calls)
        calls.append(i)
        return script[i], 5

    return calls, _fake_stream


@pytest.mark.asyncio
async def test_f8_three_failed_analysis_batches_not_completed_perfect(monkeypatch):
    """三批 analysis 全 success=False（failed_zero_output）：守卫按"崩溃零产出"
    记账（区别于 floor 拒交），收口 metadata 标 coverage_bypassed，不得 completed+100。"""
    fake_analysis = _FakeAnalysis()
    agent = _make_orchestrator(monkeypatch, fake_analysis)
    # 3 次真实失败调度 + 第 4 次被 max_dispatch 守卫拦截 + finish
    script = [_DISPATCH_TEXT] * 4 + [_FINISH_TEXT]
    calls, stream = _scripted_stream(script)
    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=stream))

    result = await agent.run({
        "project_info": {"name": "nginx"},
        "config": {},
        "task_timeout_seconds": 7200,
    })

    assert result.success, "orchestrator 自身 finish 收口（终态分类由 metadata 承担）"
    # 失败调度占名额：第 4 次不重跑子 Agent
    assert fake_analysis.run_count == 3, (
        "失败调度必须占 max_dispatch 名额（计数陷阱显式化），实际重跑次数异常"
    )
    # 守卫必须区分崩溃零产出与下限违规：gate observation 可识别失败种类
    observations = result.data.get("observations") or []
    zero_output_gates = [
        o for o in observations
        if isinstance(o, dict)
        and (
            o.get("gate") in ("failed_zero_output", "failed_dispatch")
            or "零产出" in str(o.get("reason", ""))
            or "崩溃" in str(o.get("reason", ""))
        )
    ]
    assert zero_output_gates, (
        "三批崩溃零产出必须留下区别于 floor 拒交的 gate observation，"
        f"实际 gates: {[o.get('gate') for o in observations]}"
    )
    # 不得把崩溃零产出误记为模型拒交（output_floor 违规）
    floor_gates = [o for o in observations if o.get("gate") == "output_floor"]
    assert not floor_gates, "子 Agent 未报 output_floor_violated，不得按 floor 违规记账"
    # 收口不得伪装成干净完成
    metadata = result.metadata or {}
    assert metadata.get("coverage_bypassed") is True, (
        "全败零产出收口必须 coverage_bypassed（COMPLETED_WITH_GAPS），不得 completed"
    )
    assert result.data.get("findings") == []


def test_f8_scoring_returns_none_for_gap_zero_findings():
    """评分回归：gaps=True 的 0 findings 任务返回 None（无法评分），
    只有干净完成才允许 100。"""
    from app.api.v1.endpoints.agent_tasks import (
        _calculate_quality_score,
        _calculate_security_score,
    )

    assert _calculate_security_score([], gaps=True) is None
    assert _calculate_security_score([], gaps=False) == 100.0
    assert _calculate_quality_score([], 0, 0, 10, saved_count=0, gaps=True) is None
    assert _calculate_quality_score([], 0, 0, 10, saved_count=0, gaps=False) == 100.0
