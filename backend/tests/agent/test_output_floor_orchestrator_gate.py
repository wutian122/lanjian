"""sandbox-verification-hard-gate Task 11: orchestrator 产出下限门禁与 Semgrep 兜底。

覆盖 spec finding-output-floor 两个 Requirement：
- Analysis 全部派发完成且 0 产出时，Semgrep 预扫发现去重后作为兜底候选
  （source="semgrep_fallback"、confidence=0.5、needs_verification=true）落库，
  进入 Verification 验证队列；有 Analysis 产出时兜底不触发；
- Analysis 强制总结返回 output_floor_violated=true 时，max_dispatch 达限不再
  无条件自动放行 finish：有兜底候选则引导沙箱验证（不放行覆盖率门禁），
  无任何可验证产出则记 gate="output_floor" observation 并按覆盖不足收口
  （completed_with_gaps，coverage_bypass reason=output_floor_violated）；
- Task 9 Important 交接：recon 侦察线索（source="recon"/"recon_high_risk"）
  仅作报告上下文，不进验证队列、不计入门禁未验证口径（承接 :617 既有裁决
  "高风险区不作 finding"）。
"""
import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.verification import _is_verification_work_item


def _make_orch():
    """绕过 __init__ 构造 agent，手动设置本任务逻辑依赖的实例属性。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._all_findings = []
    agent._semgrep_findings = []
    agent._semgrep_fallback_applied = False
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


def _semgrep_finding(path="app/db.py", line=42, rule="p.sql-injection", message="SQL injection"):
    """构造一条 _semgrep_findings 格式记录（与 _run_semgrep_prescan 输出一致）。"""
    vuln_type = "xss" if "xss" in rule else "injection"
    return {
        "title": rule,  # 预扫映射时 check_id 写入 title
        "file_path": path,
        "line_start": line,
        "line_end": line,
        "severity": "high",
        "description": message,
        "vulnerability_type": vuln_type,
        "code_snippet": "cursor.execute(query)",
        "source": "semgrep",
        "verification_method": "semgrep_static_analysis",
        "is_verified": False,
    }


def _recon_finding(source="recon_high_risk"):
    return {
        "title": "疑似高风险区 app/auth.py",
        "description": "auth 相关代码",
        "file_path": "app/auth.py",
        "line_start": 10,
        "severity": "high" if source == "recon_high_risk" else "medium",
        "vulnerability_type": "potential_issue",
        "source": source,
        "needs_verification": True,
        "confidence": 0.6 if source == "recon_high_risk" else 0.5,
    }


# ============ Semgrep 兜底落库 ============

@pytest.mark.asyncio
async def test_semgrep_fallback_persists_candidates_on_zero_output():
    """0 findings + 3 条 semgrep 预扫发现 → 兜底落库 3 候选，
    候选带 source=semgrep_fallback / confidence=0.5 / needs_verification=true，
    且计入可验证产出（进验证队列口径）。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path=f"app/f{i}.py", line=10 + i, rule=f"p.rule-{i}", message=f"msg{i}")
        for i in range(3)
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 3
    assert len(agent._all_findings) == 3
    for f in agent._all_findings:
        assert f["source"] == "semgrep_fallback"
        assert f["confidence"] == 0.5
        assert f["needs_verification"] is True
        assert f["is_verified"] is False
    actionable = agent._actionable_findings()
    assert len(actionable) == 3
    fallback_obs = [o for o in agent._gate_observations if o.get("gate") == "semgrep_fallback"]
    assert len(fallback_obs) == 1


@pytest.mark.asyncio
async def test_fallback_dedupes_by_file_path_and_rule_id():
    """去重键 file_path+rule_id：同文件同规则不同行的 semgrep 记录只落 1 条候选。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path="app/db.py", line=42, rule="p.sql-injection", message="SQL injection via query"),
        _semgrep_finding(path="app/db.py", line=88, rule="p.sql-injection", message="SQL injection via query"),
        _semgrep_finding(path="app/db.py", line=42, rule="p.xss", message="XSS sink in template"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 2
    titles = {f["title"] for f in agent._all_findings}
    assert len(titles) == 2


@pytest.mark.asyncio
async def test_fallback_not_triggered_with_analysis_findings():
    """已有可验证产出（Analysis finding / 候选）时兜底不触发。"""
    agent = _make_orch()
    agent._all_findings = [
        {
            "title": "Analysis 确认 SQLi",
            "file_path": "app/db.py",
            "line_start": 7,
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "confidence": 0.9,
        }
    ]
    agent._semgrep_findings = [_semgrep_finding()]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert len(agent._all_findings) == 1
    assert agent._all_findings[0].get("source") != "semgrep_fallback"


@pytest.mark.asyncio
async def test_fallback_idempotent():
    """兜底只执行一次：重复调用不重复落库。"""
    agent = _make_orch()
    agent._semgrep_findings = [_semgrep_finding()]

    first = await agent._apply_semgrep_fallback()
    second = await agent._apply_semgrep_fallback()

    assert first == 1
    assert second == 0
    assert len(agent._all_findings) == 1


@pytest.mark.asyncio
async def test_recon_only_findings_still_trigger_fallback():
    """仅 recon 线索（上下文，不算产出）时 Semgrep 兜底仍触发——
    recon 线索不占 _all_findings 的可验证产出口径。"""
    agent = _make_orch()
    agent._all_findings = [_recon_finding("recon"), _recon_finding("recon_high_risk")]
    agent._semgrep_findings = [_semgrep_finding()]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert agent._all_findings[-1]["source"] == "semgrep_fallback"


# ============ output_floor_violated 门禁 ============

@pytest.mark.asyncio
async def test_output_floor_violated_with_fallback_candidates_no_auto_release():
    """violated=true 且 Semgrep 兜底有候选：不自动放行覆盖率门禁，
    记 output_floor observation，引导 verification 验证兜底候选。"""
    agent = _make_orch()
    agent.sub_agents = {"analysis": object()}
    agent._dispatched_tasks = {"analysis": 3}
    agent._output_floor_violated = True
    agent._semgrep_findings = [
        _semgrep_finding(path=f"app/f{i}.py", line=10 + i, rule=f"p.rule-{i}", message=f"m{i}")
        for i in range(3)
    ]

    message = await agent._dispatch_agent({"agent": "analysis", "task": "再扫一轮", "context": ""})

    # 兜底候选落库
    assert len(agent._actionable_findings()) == 3
    # 覆盖率门禁未被自动放行
    assert agent._coverage_bypassed is False
    assert agent._hard_coverage_block_count < 3
    # 门禁 observation 记录
    gates = {o["gate"] for o in agent._gate_observations}
    assert "output_floor" in gates
    assert "semgrep_fallback" in gates
    # 文案引导验证而非直接 finish
    assert "verification" in message.lower() or "验证" in message
    assert "直接 finish" not in message


@pytest.mark.asyncio
async def test_output_floor_violated_empty_closes_with_gaps():
    """violated=true 且 Semgrep 也无发现：记 output_floor observation，
    按覆盖不足收口（coverage_bypassed reason=output_floor_violated），允许 finish。"""
    agent = _make_orch()
    agent.sub_agents = {"analysis": object()}
    agent._dispatched_tasks = {"analysis": 3}
    agent._output_floor_violated = True
    agent._semgrep_findings = []

    message = await agent._dispatch_agent({"agent": "analysis", "task": "再扫一轮", "context": ""})

    assert agent._coverage_bypassed is True
    assert agent._coverage_bypass_info.get("reason") == "output_floor_violated"
    assert agent._hard_coverage_block_count >= 3
    gates = [o for o in agent._gate_observations if o.get("gate") == "output_floor"]
    assert len(gates) == 1
    assert "覆盖不足" in message or "产出" in message


@pytest.mark.asyncio
async def test_non_violated_max_dispatch_keeps_auto_bypass():
    """未违规（既有行为回归保护）：max_dispatch 达限仍自动放行覆盖率门禁。"""
    agent = _make_orch()
    agent.sub_agents = {"analysis": object()}
    agent._dispatched_tasks = {"analysis": 3}
    agent._output_floor_violated = False
    agent._semgrep_findings = []

    message = await agent._dispatch_agent({"agent": "analysis", "task": "再扫一轮", "context": ""})

    assert agent._hard_coverage_block_count >= 3
    assert "覆盖率门禁已自动放行" in message


def test_analysis_floor_signal_ingested_sticky():
    """analysis 返回 data.output_floor_violated=true → 粘滞标记；
    后续轮次未违规不清零（收口判定以"曾违规且最终 0 产出"为准）。"""
    agent = _make_orch()

    agent._ingest_analysis_floor_signal({"output_floor_violated": True, "findings": []})
    assert agent._output_floor_violated is True

    agent._ingest_analysis_floor_signal({"output_floor_violated": False, "findings": []})
    assert agent._output_floor_violated is True


# ============ recon 线索处置（Task 9 Important 交接）============

def test_recon_leads_are_context_only():
    """recon/recon_high_risk 来源为上下文线索：不计可验证产出；
    semgrep_fallback 与普通 finding 计入。"""
    agent = _make_orch()
    agent._all_findings = [
        _recon_finding("recon"),
        _recon_finding("recon_high_risk"),
        {"title": "真发现", "source": "analysis", "severity": "high"},
        {"title": "兜底候选", "source": "semgrep_fallback", "severity": "medium"},
    ]

    actionable = agent._actionable_findings()

    assert len(actionable) == 2
    assert {f["title"] for f in actionable} == {"真发现", "兜底候选"}


def test_verification_queue_accepts_fallback_rejects_recon():
    """Verification 验证队列口径：semgrep_fallback 候选必须入队；
    recon 线索（含 severity=high 的 recon_high_risk）不得入队。"""
    fallback = {"source": "semgrep_fallback", "severity": "medium", "needs_verification": True}
    assert _is_verification_work_item(fallback) is True

    recon_str = {"source": "recon", "severity": "medium", "needs_verification": True}
    recon_high = {"source": "recon_high_risk", "severity": "high", "needs_verification": True}
    assert _is_verification_work_item(recon_str) is False
    assert _is_verification_work_item(recon_high) is False

    normal = {"source": "analysis", "severity": "high", "needs_verification": False}
    assert _is_verification_work_item(normal) is True


def test_verification_handoff_excludes_recon_leads():
    """给 Verification 的 handoff key_findings 排除 recon 线索，
    保留 Analysis 发现与 semgrep_fallback 候选。"""
    agent = _make_orch()
    # analysis data 需有非空 findings 才进入 key_findings 全量构建分支
    agent._agent_results = {"analysis": {"findings": [{"title": "placeholder"}]}}
    agent._all_findings = [
        _recon_finding("recon_high_risk"),
        {"title": "Analysis 发现", "source": "analysis", "severity": "high"},
        {"title": "兜底候选", "source": "semgrep_fallback", "severity": "medium"},
    ]

    handoff = agent._build_handoff_for_agent("verification", "验证全部发现", "")

    assert handoff is not None
    titles = {f.get("title") for f in handoff.key_findings}
    assert "Analysis 发现" in titles
    assert "兜底候选" in titles
    assert not any("高风险区" in t for t in titles)


@pytest.mark.asyncio
async def test_recon_leads_not_counted_as_unverified_in_force_dispatch():
    """仅 recon 线索时 _maybe_dispatch_force_verification 不触发补验
    （recon 不进未验证口径）；有真未验证 finding 时正常触发。"""
    agent = _make_orch()
    agent._all_findings = [_recon_finding("recon"), _recon_finding("recon_high_risk")]

    await agent._maybe_dispatch_force_verification()
    assert agent._force_verification_dispatched is False

    agent._all_findings.append(
        {"title": "未验证真发现", "source": "analysis", "severity": "high",
         "verification_status": "needs_context"}
    )
    await agent._maybe_dispatch_force_verification()
    assert agent._force_verification_dispatched is True


# ============ 报告"静态扫描兜底候选"段落 ============

import types  # noqa: E402

from app.api.v1.endpoints.agent_tasks import _build_semgrep_fallback_section  # noqa: E402


def _stub_report_finding(title, source):
    verification_result = {"source": source} if source else None
    return types.SimpleNamespace(
        title=title,
        file_path="app/db.py",
        line_start=42,
        verification_result=verification_result,
    )


def test_report_section_lists_semgrep_fallback_candidates():
    """source=semgrep_fallback 的 finding 进"静态扫描兜底候选"段落并标注；
    Analysis 高置信发现不列入。"""
    lines = _build_semgrep_fallback_section([
        _stub_report_finding("兜底SQLi", "semgrep_fallback"),
        _stub_report_finding("Analysis发现", "analysis"),
    ])

    assert lines, "有兜底候选时必须输出段落"
    text = "\n".join(lines)
    assert "## 静态扫描兜底候选" in text
    assert "兜底SQLi" in text
    assert "静态扫描兜底候选" in text
    assert "Analysis发现" not in text


def test_report_section_empty_without_fallback():
    """无 semgrep_fallback 来源 finding（含 verification_result 为 None/{}）→ 无段落。"""
    assert _build_semgrep_fallback_section([
        _stub_report_finding("普通发现", "analysis"),
        types.SimpleNamespace(title="x", file_path="a.py", line_start=1, verification_result={}),
    ]) == []
    assert _build_semgrep_fallback_section([]) == []
