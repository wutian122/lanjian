"""sandbox-verification-hard-gate Task 12: 门禁候选口径统一（Phase 3 收口）。

口径事实（spec finding-output-floor「orchestrator 候选口径 SHALL 纳入门禁判定」）：
- needs_verification=true 候选（Analysis 低置信候选 / source=semgrep_fallback
  兜底候选）SHALL 计入 has_findings 与 UNVERIFIED_TERMINAL 未验证口径——
  仅候选存在即触发 verification 派发与验证完成度检查；
- recon 侦察线索（source=recon/recon_high_risk）是上下文线索而非漏洞发现，
  全链一致排除：可验证产出口径、未验证口径、验证队列入队、handoff、落库；
- 门禁拦截消息/observation 的计数 SHALL 使用可验证产出口径（不含 recon）；
- 三处消费者（orchestrator._actionable_findings / verification 入队判定 /
  agent_tasks._save_findings 落库过滤）SHALL 共用 strict_finding 的同一谓词，
  消除同语义多副本漂移。
"""
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.structured_output import BackendCapabilities

# ---------- orchestrator 轻量构造（同 Task 11 测试模式） ----------

def _make_orch():
    """绕过 __init__ 构造 agent，手动设置口径逻辑依赖的实例属性。"""
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


def _candidate(title, source="analysis", confidence=0.4):
    """Analysis 低置信候选 / 兜底候选（needs_verification=true）。"""
    return {
        "title": title,
        "file_path": "app/x.py",
        "line_start": 12,
        "vulnerability_type": "sql_injection",
        "severity": "medium",
        "confidence": confidence,
        "needs_verification": True,
        "source": source,
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


# ============ ① 候选计入 has_findings 口径 ============

def test_candidates_count_into_has_findings_scope():
    """仅 3 个候选（无高置信发现）→ 可验证产出口径非空（has_findings=True）。

    覆盖 Analysis 候选（source=analysis）、semgrep_fallback 兜底候选与
    无 source 字段的归一化候选三种形态。"""
    agent = _make_orch()
    agent._all_findings = [
        _candidate("分析候选A"),
        _candidate("兜底候选B", source="semgrep_fallback", confidence=0.5),
        {**_candidate("无source候选C"), "source": None},
    ]

    actionable = agent._actionable_findings()

    assert len(actionable) == 3
    assert len(actionable) > 0  # has_findings 判定


def test_recon_leads_excluded_from_gate_scope():
    """2 条 recon 线索 + 1 候选 → 可验证产出口径只含候选；
    仅 recon 线索时口径为 0（has_findings=False）。"""
    agent = _make_orch()
    agent._all_findings = [
        _recon_finding("recon"),
        _recon_finding("recon_high_risk"),
        _candidate("唯一候选"),
    ]

    actionable = agent._actionable_findings()
    assert len(actionable) == 1
    assert actionable[0]["title"] == "唯一候选"

    agent2 = _make_orch()
    agent2._all_findings = [_recon_finding("recon"), _recon_finding("recon_high_risk")]
    assert len(agent2._actionable_findings()) == 0


# ============ ② 候选进未验证口径 → 触发 verification 派发 ============

@pytest.mark.asyncio
async def test_candidates_count_as_unverified_trigger_force_dispatch():
    """仅候选（无终态、无沙箱证据）→ _maybe_dispatch_force_verification
    判定为未验证并触发补验派发；仅 recon 线索不触发。"""
    agent = _make_orch()
    agent._all_findings = [
        _candidate("候选A"),
        _candidate("候选B", source="semgrep_fallback", confidence=0.5),
    ]

    await agent._maybe_dispatch_force_verification()

    assert agent._force_verification_dispatched is True

    agent2 = _make_orch()
    agent2._all_findings = [_recon_finding("recon"), _recon_finding("recon_high_risk")]
    await agent2._maybe_dispatch_force_verification()
    assert agent2._force_verification_dispatched is False


# ============ ③ recon 全链一致排除 + M4 计数口径（run() 集成） ============

def _build_run_orch(monkeypatch, tmp_path, *, seed_recon=False, violated=True):
    """构造可跑 run() 的 orchestrator（同 test_gate_release_manifest harness）。

    LLM 脚本恒为 finish；_run_semgrep_prescan 桩在 run() 状态重置后注入
    output_floor 违规信号（与可选 recon 线索），返回 3 条预扫发现——
    Analysis 一次未调度，兜底只可能由 finish 分支（orchestrator.py finish 前
    Semgrep 兜底段）触发，这正是 M8 要锁定的路径。

    预扫发现的文件须在 project_root 下真实存在——_normalize_finding 的
    幻觉文件过滤对兜底候选同样生效（生产语义：预扫扫的就是真实文件）。
    """
    service = SimpleNamespace(backend_capabilities=None)
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    agent = OrchestratorAgent(llm_service=service, tools={}, event_emitter=emitter)

    monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
    monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "check_messages", lambda: [])
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    monkeypatch.setattr(
        agent, "_evaluate_current_coverage",
        lambda: SimpleNamespace(is_sufficient=True, covered_count=10, gaps=[]),
    )
    monkeypatch.setattr(agent, "_summarize_findings", MagicMock(return_value="summary"))

    (tmp_path / "app").mkdir(exist_ok=True)
    prescan_records = []
    for i in range(3):
        rel = f"app/f{i}.py"
        (tmp_path / rel).write_text(f"# semgrep hit {i}\ncursor.execute(query)\n", encoding="utf-8")
        prescan_records.append({
            "title": f"p.rule-{i}",  # 预扫映射时 check_id 写入 title
            "file_path": rel,
            "line_start": 2,
            "line_end": 2,
            "severity": "high",
            "description": f"Semgrep rule {i} hit",
            "vulnerability_type": "injection",
            "code_snippet": "cursor.execute(query)",
            "source": "semgrep",
            "verification_method": "semgrep_static_analysis",
            "is_verified": False,
        })

    async def _fake_prescan():
        # run() 在非 resume 分支已重置 _all_findings/_output_floor_violated，
        # 此处注入的状态等价于 Analysis 阶段结束后的现场。
        if violated:
            agent._output_floor_violated = True
        if seed_recon:
            agent._all_findings.extend([
                _recon_finding("recon"),
                _recon_finding("recon_high_risk"),
            ])
        return {"findings": prescan_records, "hot_files": [], "scan_success": True}

    monkeypatch.setattr(agent, "_run_semgrep_prescan", AsyncMock(side_effect=_fake_prescan))

    def _tool_call(name, arguments="{}"):
        return {"id": f"call_{name}", "name": name, "arguments": arguments}

    script = [{"tool_calls": [_tool_call("finish", "{}")]} for _ in range(12)]
    stream_calls: list = []

    async def fake_stream(messages, temperature=None, tools=None, **kwargs):
        idx = len(stream_calls)
        stream_calls.append(idx)
        spec = script[idx]
        agent._last_tool_calls = spec.get("tool_calls")
        return spec.get("output", ""), 11

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=fake_stream))
    return agent, str(tmp_path)


@pytest.mark.asyncio
async def test_finish_branch_fallback_persists_candidates_and_gate_counts_them(monkeypatch, tmp_path):
    """M8 集成：LLM 直接 finish（Analysis 零调度）+ output_floor 违规 + Semgrep
    预扫有发现 → finish 分支兜底落库 3 条候选；候选计入门禁口径，沙箱证据
    门禁拒绝 finish 并记录 verification_evidence_gate observation。"""
    agent, project_root = _build_run_orch(monkeypatch, tmp_path)

    result = await agent.run({"project_info": {"root": project_root}, "config": {}})

    assert result.success, f"finish 分支兜底后应经门禁链收口: {result.error}"
    findings = result.data["findings"]
    fallback = [f for f in findings if f.get("source") == "semgrep_fallback"]
    assert len(fallback) == 3, "finish 分支必须把 Semgrep 预扫发现兜底落库为候选"
    for f in fallback:
        assert f["needs_verification"] is True
        assert f["confidence"] == 0.5

    gates = {o["gate"]: o for o in result.data["observations"]}
    assert "semgrep_fallback" in gates, "finish 分支兜底须记 semgrep_fallback observation"
    assert "verification_evidence_gate" in gates, (
        "仅候选时沙箱证据门禁必须拦截 finish（候选计入 has_findings）"
    )
    # 门禁计数按可验证产出口径：3 条候选
    reason = gates["verification_evidence_gate"]["reason"]
    assert "发现 3 个漏洞" in reason, f"门禁计数应为 3 条候选，got: {reason!r}"


@pytest.mark.asyncio
async def test_gate_intercept_counts_exclude_recon_leads(monkeypatch, tmp_path):
    """M4：finish 门禁拦截消息/observation 的计数使用可验证产出口径——
    2 条 recon 线索 + 3 条兜底候选时，计数为 3 而非 5（旧代码 len(_all_findings)
    含 recon，observation 写"发现 5 个漏洞"）。"""
    agent, project_root = _build_run_orch(monkeypatch, tmp_path, seed_recon=True)

    result = await agent.run({"project_info": {"root": project_root}, "config": {}})

    assert result.success, f"应收口: {result.error}"
    assert len(result.data["findings"]) == 5  # recon 线索仍在内存状态
    evidence_obs = [
        o for o in result.data["observations"] if o.get("gate") == "verification_evidence_gate"
    ]
    assert evidence_obs, "候选在场时门禁必须拦截"
    for o in evidence_obs:
        assert "发现 3 个漏洞" in o["reason"], f"计数须为可验证口径 3，got: {o['reason']!r}"
        assert "发现 5 个漏洞" not in o["reason"], f"recon 线索不得计入门禁计数: {o['reason']!r}"


# ============ verification.py 验证队列入队口径一致性 ============

def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    return e


def _make_service(caps=None):
    service = MagicMock()
    service.backend_capabilities = caps or BackendCapabilities(tools=True, guided_json=False)

    async def _stream(messages=None, temperature=None, max_tokens=None, tools=None,
                      response_format=None):
        yield {"type": "done", "content": "", "reasoning": "", "tool_calls": [],
               "usage": {"total_tokens": 10}, "finish_reason": "stop"}

    service.chat_completion_stream = MagicMock(return_value=_stream())
    return service


def _tool_call(name, arguments):
    return {"id": f"call_{name}", "name": name, "arguments": arguments}


def _done_with_tool_calls(tool_calls):
    async def _stream(messages=None, temperature=None, max_tokens=None, tools=None,
                      response_format=None):
        yield {
            "type": "done", "content": "", "reasoning": "",
            "tool_calls": tool_calls,
            "usage": {"total_tokens": 10}, "finish_reason": "tool_calls",
        }
    return _stream


@pytest.mark.asyncio
async def test_verification_queue_accepts_candidate_rejects_recon():
    """findings_to_verify 四入口口径一致性：needs_verification=true 候选
    （semgrep_fallback）必须入队验证；recon 线索（含 high 严重度）不得入队。"""
    from app.services.agent.agents.verification import VerificationAgent

    service = _make_service(caps=BackendCapabilities(tools=True, guided_json=False))
    agent = VerificationAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
    agent._check_token_budget_exceeded = lambda: False

    # 弹性上限达标即收口（同 test_elastic_budget_fallback_gate 模式）
    async def _fake_det(commands, root):
        agent._sandbox_exec_attempts = 12
        agent._sandbox_exec_calls = 12

    agent._run_deterministic_sandbox_commands = _fake_det

    captured: dict = {}

    def _spy_prepare(findings):
        captured["queue"] = list(findings)
        return "/fake/project/root"

    agent._prepare_sandbox_files = MagicMock(side_effect=_spy_prepare)

    candidate = {
        "title": "兜底候选 SQLi",
        "vulnerability_type": "sql_injection",
        "severity": "medium",
        "file_path": "app/cand.py",
        "line_start": 7,
        "description": "Semgrep 兜底候选",
        "code_snippet": "cursor.execute(q)",
        "needs_verification": True,
        "confidence": 0.5,
        "source": "semgrep_fallback",
    }
    recon_lead = {
        "title": "侦察高风险区线索",
        "vulnerability_type": "potential_issue",
        "severity": "high",
        "file_path": "app/recon.py",
        "line_start": 3,
        "description": "auth 相关",
        "needs_verification": True,
        "confidence": 0.6,
        "source": "recon_high_risk",
    }

    payload = {
        "summary": {"total": 1, "confirmed": 0, "likely": 0, "false_positive": 0},
        "findings": [{
            "file_path": "app/cand.py",
            "line_start": 7,
            "vulnerability_type": "sql_injection",
            "severity": "medium",
            "title": "兜底候选 SQLi",
            "verdict": "needs_context",
            "confidence": 0.5,
            "is_verified": False,
            "verification_method": "尝试沙箱验证",
            "verification_details": "达到弹性上限仍未成功",
            "sandbox_attempts": [],
        }],
    }
    agent.llm_service.chat_completion_stream = _done_with_tool_calls(
        [_tool_call("submit_findings", json.dumps(payload, ensure_ascii=False))]
    )

    result = await agent.run({
        "previous_results": {"findings": [dict(candidate), dict(recon_lead)]},
        "config": {},
    })

    assert result.success, f"验证应收口: {result.error}"
    queue = captured.get("queue")
    assert queue is not None, "_prepare_sandbox_files 必须收到验证队列"
    assert len(queue) == 1, f"队列只应含候选（recon 不得入队），实际 {len(queue)} 条"
    assert queue[0]["source"] == "semgrep_fallback"
    out = result.data["findings"]
    assert len(out) == 1, "recon 线索不得作为验证产出来源"
    assert not any(f.get("source") in ("recon", "recon_high_risk") for f in out)


# ============ ⑤ 口径 helper 合并：单一来源 + 行为等价 ============

def test_scope_predicates_single_source_and_equivalent():
    """三处消费者共用 strict_finding 同一谓词（同语义零漂移）：
    - verification 模块的入队判定 is_verification_work_item；
    - orchestrator 模块导入 is_context_only_finding（类上不再自定义副本）；
    - agent_tasks 落库过滤导入同一 is_context_only_finding。"""
    import app.api.v1.endpoints.agent_tasks as atm
    import app.services.agent.agents.orchestrator as omod
    import app.services.agent.agents.verification as vmod
    import app.services.agent.strict_finding as sf

    assert vmod.is_verification_work_item is sf.is_verification_work_item
    assert omod.is_context_only_finding is sf.is_context_only_finding
    assert atm.is_context_only_finding is sf.is_context_only_finding
    # 旧副本已删除
    assert not hasattr(OrchestratorAgent, "_is_context_only_finding")
    assert not hasattr(sf, "_is_verification_work_item")

    # 行为矩阵
    recon_str = {"source": "recon", "severity": "medium", "needs_verification": True}
    recon_high = {"source": "recon_high_risk", "severity": "high", "needs_verification": True}
    fallback = {"source": "semgrep_fallback", "severity": "medium", "needs_verification": True}
    analysis_candidate = {"source": "analysis", "severity": "medium", "needs_verification": True}
    no_source = {"severity": "high"}

    assert sf.is_context_only_finding(recon_str) is True
    assert sf.is_context_only_finding(recon_high) is True
    assert sf.is_context_only_finding(fallback) is False
    assert sf.is_context_only_finding(analysis_candidate) is False
    assert sf.is_context_only_finding(no_source) is False
    assert sf.is_context_only_finding("not-a-dict") is False
    assert sf.is_context_only_finding(None) is False

    assert sf.is_verification_work_item(recon_str) is False
    assert sf.is_verification_work_item(recon_high) is False
    assert sf.is_verification_work_item(fallback) is True
    assert sf.is_verification_work_item(analysis_candidate) is True
    assert sf.is_verification_work_item(no_source) is True
    assert sf.is_verification_work_item("not-a-dict") is False

    # orchestrator 可验证产出口径与共享谓词逐条等价
    agent = _make_orch()
    mixed = [recon_high, analysis_candidate, "junk", recon_str, fallback, no_source]
    agent._all_findings = list(mixed)
    actionable = agent._actionable_findings()
    expected = [f for f in mixed if isinstance(f, dict) and sf.is_verification_work_item(f)]
    assert actionable == expected
