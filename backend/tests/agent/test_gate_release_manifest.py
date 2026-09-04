"""sandbox-verification-hard-gate Task 8: R4 放行未验证清单强制标记与报告呈现。

覆盖两条放行收敛路径与报告/持久化接线：
- R4（finish 门禁连续拒绝达 verification_max_force_redispatch 后放行）：仍零沙箱
  尝试、无显式豁免的未验证 finding 强制写
  sandbox_skip_reason="gate_release_after_max_redispatch"，验证状态不升级
  （与 Task 7 elastic_exit 同语义）；
- 主循环轮次耗尽退出（不经过任何 finish 门禁，T6 注释 ec0985ad 生产回归）：
  同样强制标记，原因 orchestrator_max_iterations_exhausted；
- 放行前 _maybe_dispatch_force_verification 补验产出 confirmed 的 finding 不标记；
- 已有 elastic_exit/no_poc_template 豁免标记不覆盖；有沙箱尝试（含 infra_error）
  的 finding 不标记（执行过 ≠ 跳过）；正常 finish 放行不标记；
- observations 记 gate_release（含放行原因与未验证数量）；
- 报告"未沙箱验证清单"段落：有 skip_reason 则列出标题/位置/原因，无则无段落；
- _save_findings 将 sandbox_skip_reason 持久化进 verification_result JSON。
"""
import types

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_orch():
    """绕过 __init__ 构造 agent，手动设置放行标记依赖的实例属性。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._all_findings = []
    agent._steps = []
    agent._agent_results = {}
    agent._dispatched_tasks = {}
    agent._gate_observations = []
    agent._gate_release_reason = None
    agent._finish_accepted = False
    return agent


def _gate_release_obs(agent):
    return [o for o in agent._gate_observations if o.get("gate") == "gate_release"]


# ============ R4 放行标记 ============

def test_r4_release_marks_unverified_findings():
    """R4 三次拒绝后放行 → 零证据未验证 finding 带 gate_release_after_max_redispatch；
    标记不升级 verification_status/is_verified。"""
    agent = _make_orch()
    agent._gate_release_reason = "gate_release_after_max_redispatch"
    agent._all_findings = [
        {
            "title": "未验证SQLi",
            "file_path": "app/db.py",
            "line_start": 42,
            "vulnerability_type": "sql_injection",
            "verification_status": "needs_context",
        },
    ]

    marked = agent._apply_gate_release_marking()

    assert marked == 1
    finding = agent._all_findings[0]
    assert finding["sandbox_skip_reason"] == "gate_release_after_max_redispatch"
    # 标记仅作显式豁免，不升级验证状态（与 elastic_exit 同语义）
    assert finding["verification_status"] == "needs_context"
    assert finding.get("is_verified") is not True


def test_confirmed_after_force_verification_not_marked():
    """放行前程序化补验产出 confirmed（带沙箱证据）的 finding 不标记；
    仅仍零证据的 finding 标记。"""
    agent = _make_orch()
    agent._gate_release_reason = "gate_release_after_max_redispatch"
    agent._all_findings = [
        {
            "title": "补验确认XSS",
            "verification_status": "confirmed",
            "is_verified": True,
            "sandbox_attempts": [{"success": True, "exit_code": 0}],
        },
        {
            "title": "仍未验证SSRF",
            "verification_status": "needs_context",
        },
    ]

    marked = agent._apply_gate_release_marking()

    assert marked == 1
    assert "sandbox_skip_reason" not in agent._all_findings[0]
    assert (
        agent._all_findings[1]["sandbox_skip_reason"]
        == "gate_release_after_max_redispatch"
    )


def test_existing_skip_reason_not_overwritten():
    """已有 elastic_exit/no_poc_template 显式豁免的 finding 保留原标记，不被覆盖。"""
    agent = _make_orch()
    agent._gate_release_reason = "gate_release_after_max_redispatch"
    agent._all_findings = [
        {"title": "弹性退出", "verification_status": "needs_context",
         "sandbox_skip_reason": "elastic_exit"},
        {"title": "无模板", "verification_status": "needs_context",
         "sandbox_skip_reason": "no_poc_template"},
        {"title": "零证据", "verification_status": "needs_context"},
    ]

    marked = agent._apply_gate_release_marking()

    assert marked == 1
    assert agent._all_findings[0]["sandbox_skip_reason"] == "elastic_exit"
    assert agent._all_findings[1]["sandbox_skip_reason"] == "no_poc_template"
    assert (
        agent._all_findings[2]["sandbox_skip_reason"]
        == "gate_release_after_max_redispatch"
    )


def test_finding_with_attempts_not_marked():
    """有沙箱尝试（含全部 infra_error / 真实执行未复现）的 finding 属"执行过"，
    不属跳过，不写放行标记。"""
    agent = _make_orch()
    agent._gate_release_reason = "gate_release_after_max_redispatch"
    agent._all_findings = [
        {
            "title": "infra故障",
            "verification_status": "needs_context",
            "sandbox_attempts": [{"success": False, "infra_error": True}],
        },
        {
            "title": "真实执行未复现",
            "verification_status": "not_reproducible",
            "sandbox_attempts": [{"success": True, "exit_code": 1}],
        },
    ]

    marked = agent._apply_gate_release_marking()

    assert marked == 0
    assert all("sandbox_skip_reason" not in f for f in agent._all_findings)


def test_gate_release_observation_recorded():
    """R4 放行标记后 observations 含 gate_release 记录（放行原因 + 未验证数量）。"""
    agent = _make_orch()
    agent._gate_release_reason = "gate_release_after_max_redispatch"
    agent._all_findings = [
        {"title": f"未验证{i}", "verification_status": "needs_context"}
        for i in range(3)
    ]

    agent._apply_gate_release_marking()

    release_obs = _gate_release_obs(agent)
    assert len(release_obs) == 1
    reason = release_obs[0]["reason"]
    assert "gate_release_after_max_redispatch" in reason
    assert "3" in reason  # 未验证数量
    assert "time" in release_obs[0]


def test_all_verified_no_marking_no_observation():
    """R4 放行但补验后全部 finding 已验证 → 0 标记、无 gate_release observation。"""
    agent = _make_orch()
    agent._gate_release_reason = "gate_release_after_max_redispatch"
    agent._all_findings = [
        {
            "title": "已确认",
            "verification_status": "confirmed",
            "is_verified": True,
            "sandbox_attempts": [{"success": True, "exit_code": 0}],
        },
    ]

    marked = agent._apply_gate_release_marking()

    assert marked == 0
    assert _gate_release_obs(agent) == []


# ============ 主循环退出（轮次耗尽）路径 ============

def test_exhaustion_path_marks_with_exhaustion_reason():
    """轮次耗尽退出（未走 finish、R4 未触发）→ 零证据 finding 标记
    orchestrator_max_iterations_exhausted 并记 gate_release observation。"""
    agent = _make_orch()
    agent._gate_release_reason = None
    agent._finish_accepted = False
    agent._all_findings = [
        {"title": "零证据", "verification_status": "needs_context"},
    ]

    marked = agent._apply_gate_release_marking()

    assert marked == 1
    assert (
        agent._all_findings[0]["sandbox_skip_reason"]
        == "orchestrator_max_iterations_exhausted"
    )
    release_obs = _gate_release_obs(agent)
    assert len(release_obs) == 1
    assert "orchestrator_max_iterations_exhausted" in release_obs[0]["reason"]


def test_normal_finish_does_not_mark():
    """LLM 正常 finish 通过门禁链（无 R4 放行）→ 不写放行标记、无 gate_release observation。"""
    agent = _make_orch()
    agent._gate_release_reason = None
    agent._finish_accepted = True
    agent._all_findings = [
        {"title": "漏网未验证", "verification_status": "needs_context"},
    ]

    marked = agent._apply_gate_release_marking()

    assert marked == 0
    assert "sandbox_skip_reason" not in agent._all_findings[0]
    assert _gate_release_obs(agent) == []


# ============ 报告"未沙箱验证清单"段落 ============

from app.api.v1.endpoints.agent_tasks import _build_unverified_sandbox_section  # noqa: E402


def _stub_finding(title, skip_reason, file_path="app/db.py", line_start=42):
    verification_result = (
        {"sandbox_skip_reason": skip_reason} if skip_reason else None
    )
    return types.SimpleNamespace(
        title=title,
        file_path=file_path,
        line_start=line_start,
        verification_result=verification_result,
    )


def test_report_section_lists_skipped_findings():
    """有 skip_reason 的 finding → 段落含标题、位置、原因标记；无 skip_reason 者不列。"""
    lines = _build_unverified_sandbox_section([
        _stub_finding("未验证SQLi", "gate_release_after_max_redispatch"),
        _stub_finding("已确认XSS", None),
    ])

    assert lines, "有 skip_reason finding 时必须输出段落"
    text = "\n".join(lines)
    assert "## 未沙箱验证清单" in text
    assert "未验证SQLi" in text
    assert "app/db.py:42" in text
    assert "gate_release_after_max_redispatch" in text
    # 已验证 finding 不出现在清单
    assert "已确认XSS" not in text


def test_report_section_empty_when_no_skip_reason():
    """全部 finding 无 skip_reason（verification_result 为 None/{}）→ 不输出段落。"""
    findings = [
        _stub_finding("XSS", None),
        types.SimpleNamespace(
            title="CSRF", file_path="a.py", line_start=1, verification_result={}
        ),
    ]
    assert _build_unverified_sandbox_section(findings) == []
    assert _build_unverified_sandbox_section([]) == []


def test_report_section_has_chinese_reason_label():
    """原因标记附带中文释义（R4 放行/轮次耗尽/弹性退出/无模板）。"""
    lines = _build_unverified_sandbox_section([
        _stub_finding("R4放行", "gate_release_after_max_redispatch"),
        _stub_finding("轮次耗尽", "orchestrator_max_iterations_exhausted"),
        _stub_finding("弹性退出", "elastic_exit"),
    ])
    text = "\n".join(lines)
    assert "验证门禁连续拒绝达上限后放行" in text
    assert "审计轮次耗尽" in text
    assert "弹性退出" in text


# ============ 持久化接线 ============

async def test_save_findings_persists_skip_reason(mock_db_session):
    """_save_findings 将 finding 的 sandbox_skip_reason 写入 verification_result JSON
    （AgentFinding 无独立列，报告段落据此读取）。"""
    from app.api.v1.endpoints.agent_tasks import _save_findings

    findings = [
        {
            "title": "未验证SQLi",
            "severity": "high",
            "vulnerability_type": "sql_injection",
            "file_path": "app/db.py",
            "line_start": 42,
            "confidence": 0.9,
            "verification_status": "needs_context",
            "sandbox_skip_reason": "gate_release_after_max_redispatch",
        },
    ]

    saved = await _save_findings(mock_db_session, "task-1", findings)

    assert saved == 1
    added = mock_db_session.add.call_args_list[0][0][0]
    assert added.verification_result is not None
    assert (
        added.verification_result["sandbox_skip_reason"]
        == "gate_release_after_max_redispatch"
    )


# ============ run() 集成：R4 放行全链路 ============

@pytest.mark.asyncio
async def test_r4_release_marks_findings_end_to_end(monkeypatch):
    """run() 集成：LLM 连续 finish 触发 R4 达限放行 → 收尾后未验证 finding 带
    gate_release_after_max_redispatch 标记（状态不升级），observations 含
    gate_release 记录（放行原因 + 未验证数量）。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    service = SimpleNamespace(backend_capabilities=None)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    agent = OrchestratorAgent(llm_service=service, tools={}, event_emitter=emitter)

    monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
    monkeypatch.setattr(
        agent, "_run_semgrep_prescan",
        AsyncMock(return_value={"findings": [], "hot_files": [], "scan_success": False}),
    )
    monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "check_messages", lambda: [])
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    monkeypatch.setattr(
        agent, "_evaluate_current_coverage",
        lambda: SimpleNamespace(is_sufficient=True, covered_count=10, gaps=[]),
    )
    monkeypatch.setattr(agent, "_summarize_findings", MagicMock(return_value="summary"))

    seeded = [
        {"title": "未验证SQLi", "file_path": "app/db.py", "line_start": 42,
         "vulnerability_type": "sql_injection", "severity": "high",
         "verification_status": "needs_context"},
        {"title": "未验证SSRF", "file_path": "app/fetch.py", "line_start": 7,
         "vulnerability_type": "ssrf", "severity": "high",
         "verification_status": "needs_context"},
    ]

    async def fake_dispatch(params):
        # 模拟 analysis 轮合入 finding；后续（含 R4 补验）不产出验证证据
        if not agent._all_findings:
            agent._all_findings.extend(seeded)
        return "dispatch-observation"

    monkeypatch.setattr(agent, "_dispatch_agent", AsyncMock(side_effect=fake_dispatch))

    def _tool_call(name, arguments="{}"):
        return {"id": f"call_{name}", "name": name, "arguments": arguments}

    script = [{"tool_calls": [_tool_call(
        "dispatch_agent",
        '{"agent": "analysis", "task": "深度审计", "context": ""}',
    )]}]
    script += [{"tool_calls": [_tool_call("finish", "{}")]} for _ in range(9)]
    stream_calls: list = []

    async def fake_stream(messages, temperature=None, tools=None, **kwargs):
        idx = len(stream_calls)
        stream_calls.append(idx)
        spec = script[idx]
        agent._last_tool_calls = spec.get("tool_calls")
        return spec.get("output", ""), 11

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=fake_stream))

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"R4 放行应成功收尾: {result.error}"
    findings = result.data["findings"]
    assert len(findings) == 2
    for f in findings:
        assert f.get("sandbox_skip_reason") == "gate_release_after_max_redispatch", (
            f"未验证 finding 必须带 R4 放行标记，got {f.get('sandbox_skip_reason')!r}"
        )
        assert f.get("verification_status") == "needs_context"
        assert f.get("is_verified") is not True
    release_obs = [
        o for o in result.data["observations"] if o.get("gate") == "gate_release"
    ]
    assert len(release_obs) == 1
    assert "gate_release_after_max_redispatch" in release_obs[0]["reason"]
    assert "2" in release_obs[0]["reason"]  # 未验证数量
