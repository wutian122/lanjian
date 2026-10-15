"""P12 Wave 0 · F4 回归夹具（floor 粘滞位按批次可撤销）。

事件证据（任务 8a3a713f）：第二批 analysis 两次强制总结全空 →
output_floor_violated 粘滞置位；第三批合规（0 候选但强总给出 2 条豁免，
未违规），max_dispatch 守卫仍被批 2 的粘滞位误伤，引导
completed_with_gaps 收口——"审完判干净"被"崩坏没干活"语义混淆。

Wave 3 契约（方案第二章 Wave 3 / 裁决⑧）：
- ``_output_floor_violated`` 由"一旦置位永不清零"改为**按批次记录、可撤销**：
  后批合规（violated=False 且给出候选或有效豁免）必须清掉前批粘滞；
- 守卫（max_dispatch 分支）按"最近批次信号 + 最终是否仍 0 可验证产出"判定，
  合规批次后不得再输出"产出下限违规"文案/observation；
- 无 file:line 引用的豁免降权 + observation（严格交叉校验后续加），
  本夹具锁定粘滞可撤销主语义。

T0 阶段 RED：当前 _ingest_analysis_floor_signal 只置位不清零。
注意：既有 test_analysis_floor_signal_ingested_sticky 锁定旧粘滞语义，
Wave 3 实施时必须与本夹具一同改写（已在 T0 notes/open_issues 登记）。
"""
import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_orch():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._all_findings = []
    agent._semgrep_findings = []
    agent._semgrep_fallback_applied = False
    agent._semgrep_leads_registered = False
    agent._output_floor_violated = False
    agent._gate_observations = []
    agent._dispatched_tasks = {}
    agent._hard_coverage_block_count = 0
    agent._coverage_bypassed = False
    agent._coverage_bypass_info = {}
    agent._runtime_context = {}
    agent._agent_results = {}
    agent._agent_handoffs = {}
    agent.sub_agents = {}
    agent._force_verification_dispatched = False
    return agent


def test_f4_compliant_batch_revokes_previous_floor_violation():
    """批 1 违规置位 → 批 2 合规（0 候选但带有效豁免）→ 粘滞位必须撤销。"""
    agent = _make_orch()

    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": True, "findings": [],
         "dimension_gaps_reported": {}}
    )
    assert agent._output_floor_violated is True

    # 批 2：未违规，且对未覆盖维度给出了书面豁免（合规批次）
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": False, "findings": [],
         "dimension_gaps_reported": {"D6_ssrf": "exempt", "D7_crypto": "exempt"}}
    )

    assert agent._output_floor_violated is False, (
        "合规批次必须撤销前批粘滞位（当前一旦置位永不清零 → 批 3 被误伤）"
    )


def test_f4_batch_with_candidate_revokes_previous_floor_violation():
    """批 2 合规（产出候选）同样撤销粘滞位。"""
    agent = _make_orch()
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": True, "findings": [],
         "dimension_gaps_reported": {}}
    )

    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": False,
         "findings": [{"title": "ssrf 候选", "vulnerability_type": "ssrf"}],
         "dimension_gaps_reported": {"D6_ssrf": "candidate"}}
    )

    assert agent._output_floor_violated is False


def test_f4_repeated_violated_batches_keep_flag():
    """控制组：连续批次都违规时粘滞位保持。"""
    agent = _make_orch()
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": True, "findings": [],
         "dimension_gaps_reported": {}}
    )
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": True, "findings": [],
         "dimension_gaps_reported": {}}
    )
    assert agent._output_floor_violated is True


@pytest.mark.asyncio
async def test_f4_guard_after_compliant_batch_uses_normal_release():
    """守卫口径：批 1 违规、批 2/3 合规后第 4 次调度被 max_dispatch 拦截时，
    不得再输出"产出下限违规"收口文案/observation（8a3a713f 误伤形态）。"""
    agent = _make_orch()
    agent.sub_agents = {"analysis": object()}
    agent._dispatched_tasks = {"analysis": 3}

    # 模拟三批次信号依次进入（_dispatch_agent 成功分支外的同一入口）
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": True, "findings": [],
         "dimension_gaps_reported": {}}
    )
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": False, "findings": [],
         "dimension_gaps_reported": {"D6_ssrf": "exempt"}}
    )
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": False, "findings": [],
         "dimension_gaps_reported": {"D1_injection": "exempt", "D7_crypto": "exempt"}}
    )

    message = await agent._dispatch_agent({"agent": "analysis", "task": "再扫一轮", "context": ""})

    assert agent._output_floor_violated is False
    assert "覆盖率门禁已自动放行" in message, (
        "最近批次合规时必须走正常自动放行，而非 floor 违规收口"
    )
    assert "产出下限违规" not in message, "粘滞位已撤销，不得再指控违规"
    assert not [
        o for o in agent._gate_observations if o.get("gate") == "output_floor"
    ], "合规批次后不得留 output_floor gate observation"


@pytest.mark.asyncio
async def test_f4_guard_still_blocks_when_latest_batch_violated():
    """控制组：最近批次仍违规（且无兜底候选）时，守卫继续按覆盖不足收口。"""
    agent = _make_orch()
    agent.sub_agents = {"analysis": object()}
    agent._dispatched_tasks = {"analysis": 3}

    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": False, "findings": [],
         "dimension_gaps_reported": {"D6_ssrf": "exempt"}}
    )
    agent._ingest_analysis_floor_signal(
        {"output_floor_violated": True, "findings": [],
         "dimension_gaps_reported": {}}
    )

    message = await agent._dispatch_agent({"agent": "analysis", "task": "再扫一轮", "context": ""})

    assert agent._coverage_bypassed is True
    assert "产出下限违规" in message or "覆盖不足" in message
