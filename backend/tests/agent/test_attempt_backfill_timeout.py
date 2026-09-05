"""Task19 缺陷修复：Verification 沙箱 attempt 回填共享对象 + 调度超时早退 merge。

生产任务 9344d5dd 实证：40 条 findings 的 sandbox_attempts 全 null。两个叠加断点：

A. Verification 证据绑定落在 dict 副本上——
   _finalize_findings_without_final_answer 的 ``{**f}`` 副本与
   _bind_runtime_evidence_to_all 的 ``dict(orig)`` 副本/LLM 条目 continue 跳过，
   使 findings_to_verify 元素（orchestrator 经 previous_results["findings"]
   直传的 _all_findings 同引用对象）持续裸奔；
B. Orchestrator 调度超时/取消早退直接返回文本——
   verification cancel 收口（_finalize_findings_without_final_answer）返回的
   AgentResult(success=False, data.findings 已绑证据) 随 TimeoutError/CancelledError
   整体丢弃，走不到 merge 段（生产两次超时：1800s + 551s）。

本文件锁定：
- A：三处绑定路径（空卷收口 / R2 全量绑定 LLM 已报+漏报 / REQ-ER-2 兜底）
  必须把 sandbox_attempts 写回待验 finding 本体，且本体与返回结果证据一致；
- B：超时/取消早退前必须抢救 merge 子 Agent 已声明 findings（复用
  _merge_failed_result_findings 管线），正常路径不回归；
- 双计守护：本体已绑证据 + salvage merge 同源结果时，attempts 语义去重，
  不得拼接重复。
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.base import AgentResult
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.verification import VerificationAgent


# ---------- 公共构造助手 ----------

def _attempt(fid: str) -> dict:
    """一条已执行的沙箱 attempt（确定性 PoC 落账格式）。"""
    return {
        "success": True,
        "exit_code": 0,
        "finding_id": fid,
        "target_ref": f"src/{fid}.py:10",
        "command": f"python3 poc_{fid}.py",
        "evidence_summary": "VULNERABILITY_CONFIRMED: sql injection triggered",
    }


def _finding(fid: str, line: int = 10) -> dict:
    """一条待验证 finding（与 orchestrator._all_findings 元素同构）。"""
    return {
        "_sandbox_finding_id": fid,
        "file_path": f"src/{fid}.py",
        "line_start": line,
        "vulnerability_type": "sql_injection",
        "title": f"SQLi {fid}",
        "description": f"desc {fid}",
        "severity": "high",
        "confidence": 0.8,
        "needs_verification": True,
    }


def _verification_agent() -> VerificationAgent:
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}
    agent._all_findings = []
    return agent


# ========== A. Verification 绑定必须落共享对象本体 ==========

def test_finalize_without_final_answer_binds_shared_object():
    """空 Final Answer / cancel 收口：证据绑定到 f 本体而非 {**f} 副本，
    且本体与返回结果的 sandbox_attempts 内容一致（副本 vs 共享对象一致性）。"""
    agent = _verification_agent()
    f1, f2 = _finding("f-1"), _finding("f-2", line=20)
    agent._runtime_attempts_by_finding_id = {
        "f-1": [_attempt("f-1")],
        "f-2": [_attempt("f-2")],
    }

    results = agent._finalize_findings_without_final_answer([f1, f2])

    assert results[0].get("sandbox_attempts"), "返回结果应带沙箱证据"
    assert f1.get("sandbox_attempts"), (
        "断点A1: cancel/空卷收口把证据写在 {**f} 副本上，共享本体裸奔"
    )
    assert f2.get("sandbox_attempts")
    # ④ 一致性：同一 finding 在 verification 内（本体）与返回后（results）证据相同
    assert f1["sandbox_attempts"] == results[0]["sandbox_attempts"]


def test_bind_to_all_llm_reported_path_syncs_back_to_original():
    """R2 全量绑定：LLM 已报告条目（verified_findings）带证据时，
    旧逻辑 `if target.get('sandbox_attempts'): continue` 跳过——
    共享本体 orig 必须同步获得证据。"""
    agent = _verification_agent()
    orig = _finding("f-1")
    strict = {
        **orig,
        "sandbox_attempts": [_attempt("f-1")],
        "verification_status": "confirmed",
    }

    agent._bind_runtime_evidence_to_all([strict], [orig])

    assert orig.get("sandbox_attempts"), (
        "断点A2: LLM 条目已有证据时 continue 跳过，共享本体未回写"
    )


def test_bind_to_all_missing_finding_path_syncs_back_to_original():
    """R2 全量绑定：LLM 漏报路径（target = dict(orig) 副本）绑定证据后，
    共享本体 orig 必须同步，副本 verified 条目也保留。"""
    agent = _verification_agent()
    orig = _finding("f-1")
    agent._runtime_attempts_by_finding_id = {"f-1": [_attempt("f-1")]}
    verified: list = []

    agent._bind_runtime_evidence_to_all(verified, [orig])

    assert verified and verified[0].get("sandbox_attempts"), "漏报兜底条目应带证据"
    assert orig.get("sandbox_attempts"), (
        "断点A3: 漏报路径证据绑在 dict(orig) 副本上，共享本体未同步"
    )


def test_bind_unbound_runtime_evidence_syncs_back_to_original():
    """REQ-ER-2 成功路径最终兜底：给 verified 条目绑上证据后，
    同路径的待验本体（self._all_findings 元素）必须同步回写。"""
    agent = _verification_agent()
    orig = _finding("f-1")
    agent._all_findings = [orig]
    agent._runtime_attempts_by_finding_id = {"f-1": [_attempt("f-1")]}
    vf = {**orig}  # verified 条目尚无证据

    agent._bind_unbound_runtime_evidence([vf])

    assert vf.get("sandbox_attempts"), "兜底绑定应给 verified 条目附上证据"
    assert orig.get("sandbox_attempts"), (
        "断点A4: REQ-ER-2 兜底证据只落在 verified 条目，共享本体未同步"
    )


# ========== B. Orchestrator 超时/取消早退抢救 merge ==========

class _FakeVerificationAgent:
    """模拟 VerificationAgent 调度：挂起直到被 cancel；cancel 收口复刻
    verification.py:1385 (LLM 调用 CancelledError -> break) ->
    :1669 (_finalize_findings_without_final_answer) 返回
    AgentResult(success=False, data.findings 已绑沙箱证据)。"""

    def __init__(self, payload: list, *, bind_shared: bool = False, delay: float = 5.0):
        self._payload = payload
        self._bind_shared = bind_shared
        self._delay = delay
        self.name = "verification"
        self._registered = False

    async def run(self, input_data: dict) -> AgentResult:
        shared = (input_data.get("previous_results") or {}).get("findings") or []
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            if self._bind_shared:
                # 模拟修复 A：证据直接绑定到共享本体（与 orchestrator._all_findings
                # 同引用的 findings_to_verify 元素）
                for f in shared:
                    if isinstance(f, dict) and not f.get("sandbox_attempts"):
                        fid = f.get("_sandbox_finding_id")
                        f["sandbox_attempts"] = [_attempt(str(fid))]
            return AgentResult(
                success=False,
                error="任务已取消",
                data={"findings": [dict(f) for f in self._payload]},
            )
        return AgentResult(
            success=True,
            data={"findings": [dict(f) for f in self._payload]},
        )

    # BaseAgent 接口（调度路径会调用）
    def set_parent_id(self, _pid):
        pass

    def _register_to_registry(self, task=None):
        self._registered = True

    def reset_dispatch_cancel(self):
        pass

    def cancel(self):
        pass


def _make_orch(findings: list, *, timeout: float = 0.4) -> OrchestratorAgent:
    """绕过 __init__ 构造 orchestrator，手动装配 _dispatch_agent 走到
    超时/merge 段所需的最小属性集。_runtime_context 不含 project_root，
    使 _normalize_finding 跳过文件存在性校验。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.config = SimpleNamespace(name="orchestrator")
    agent._agent_id = "orch-test"
    agent._cancelled = False
    agent._user_cancelled = False
    agent._cancel_callback = None
    agent._all_findings = findings
    agent._semgrep_findings = []
    agent._agent_results = {}
    agent._agent_handoffs = {}
    agent._dispatched_tasks = {}
    agent._runtime_context = {}
    agent._search_registry = {"grep_patterns": set()}
    agent._sub_agent_total_iterations = 0
    agent._sub_agent_total_tool_calls = 0
    agent._sub_agent_total_tokens = 0
    agent._dispatch_failures = 0
    agent._tool_calls = 0
    agent.context_manager = None
    agent.trace_manager = None
    agent.sub_agents = {}
    agent.emit_event = AsyncMock()
    agent._budget_refusal = lambda _name: None
    agent._maybe_request_soft_stop = lambda _a, _name: False
    agent._resolve_dispatch_timeout = lambda _name: timeout
    agent._build_handoff_for_agent = lambda *_a, **_k: None
    agent._build_trace_summary = lambda: ""
    cov = MagicMock()
    cov.statuses = {}
    cov.gaps = []
    agent._evaluate_current_coverage = lambda: cov
    return agent


def _verified_payload(shared: list) -> list:
    """模拟 verification 返回的 findings：携带 sandbox_attempts 与验证终态。"""
    return [
        {
            **f,
            "sandbox_attempts": [_attempt(str(f["_sandbox_finding_id"]))],
            "verification_status": "confirmed",
            "verdict": "confirmed",
            "is_verified": True,
        }
        for f in shared
    ]


@pytest.mark.asyncio
async def test_dispatch_timeout_salvages_verification_findings():
    """① 确定性 2 finding + 调度超时：早退前必须抢救 merge cancel 收口的
    findings，sandbox_attempts 不得随超时整体丢弃。"""
    findings = [_finding("f-1"), _finding("f-2", line=20)]
    payload = _verified_payload(findings)
    orch = _make_orch(findings, timeout=0.4)
    orch.sub_agents = {"verification": _FakeVerificationAgent(payload, bind_shared=False)}

    msg = await orch._dispatch_agent({"agent": "verification", "task": "验证 2 个 finding", "context": ""})

    assert "超时" in msg
    assert len(orch._all_findings) == 2
    for f in orch._all_findings:
        assert f.get("sandbox_attempts"), (
            "断点B1: 调度超时早退直接返回文本，已执行沙箱证据整体丢弃"
        )


@pytest.mark.asyncio
async def test_dispatch_cancel_salvages_verification_findings():
    """② 用户取消路径（CancelledError 早退）：cancel 收口的 findings 同样
    必须抢救 merge。"""
    findings = [_finding("f-1"), _finding("f-2", line=20)]
    payload = _verified_payload(findings)
    orch = _make_orch(findings, timeout=10.0)
    orch.sub_agents = {"verification": _FakeVerificationAgent(payload, bind_shared=False)}

    # 调度前检查（第 1 次）返回 False；轮询期（0.5s 周期）第 3 次命中取消
    calls = {"n": 0}

    def _cancel_cb():
        calls["n"] += 1
        return calls["n"] >= 3

    orch._cancel_callback = _cancel_cb

    msg = await orch._dispatch_agent({"agent": "verification", "task": "验证 2 个 finding", "context": ""})

    assert "取消" in msg
    for f in orch._all_findings:
        assert f.get("sandbox_attempts"), (
            "断点B2: 取消早退直接返回文本，cancel 收口证据整体丢弃"
        )


@pytest.mark.asyncio
async def test_dispatch_normal_path_keeps_attempts_no_duplication():
    """③ 正常（非超时）路径不回归：verification 成功返回的 attempts 正常
    merge 保留。"""
    findings = [_finding("f-1"), _finding("f-2", line=20)]
    payload = _verified_payload(findings)
    orch = _make_orch(findings, timeout=10.0)
    # 正常路径：不挂起，立即返回成功结果
    fake = _FakeVerificationAgent(payload, bind_shared=False, delay=0.0)
    orch.sub_agents = {"verification": fake}

    msg = await orch._dispatch_agent({"agent": "verification", "task": "验证 2 个 finding", "context": ""})

    assert "成功" in msg
    for f in orch._all_findings:
        assert f.get("sandbox_attempts"), "正常 merge 路径必须保留沙箱证据"


@pytest.mark.asyncio
async def test_shared_object_binding_survives_timeout_without_duplication():
    """④ 双保险 + 双计守护：修复 A 后证据直接绑在共享本体上（超时不 merge
    也存活）；salvage merge 同源结果时 attempts 必须语义去重，不得拼接重复。"""
    findings = [_finding("f-1"), _finding("f-2", line=20)]
    payload = _verified_payload(findings)
    orch = _make_orch(findings, timeout=0.4)
    orch.sub_agents = {"verification": _FakeVerificationAgent(payload, bind_shared=True)}

    msg = await orch._dispatch_agent({"agent": "verification", "task": "验证 2 个 finding", "context": ""})

    assert "超时" in msg
    for f in orch._all_findings:
        attempts = f.get("sandbox_attempts")
        assert attempts, "共享本体绑定的证据必须在超时后存活"
        assert len(attempts) == 1, (
            f"同源证据 merge 必须语义去重，实际 {len(attempts)} 条（双计）"
        )
