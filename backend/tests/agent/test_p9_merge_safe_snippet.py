"""P9-6 / P9-8（绑定簇批次 2，T3）。

P9-6：verification 按 prompt 模板把 poc 输出为结构化 dict（description/
steps/payload/harness_code），merge 循环内 ``dict[:300]`` 抛
``KeyError: slice(None, 300, None)``——每轮仅第一条 finding merge 成功、
dispatch_complete 永不发射，orchestrator 反复重调度 verification（生产
B·nacos 三次空烧约 197 万 token）。修正：poc/code_snippet/description
三字段统一 ``_safe_snippet`` 安全归一 + 循环逐条 try/except 隔离，单条
异常记 gate observation "per_finding_trace_error" 后继续，dispatch_complete
必达。

P9-8：verification 自报 verdict（confirmed/likely/false_positive/
not_reproducible）而 finding 未带权威 verification_status 时，merge 回关
补 verdict→verification_status 归一映射。confirmed/likely 按系统口径落
static_confirmed，**证据前置（阻断级防护）**：仅当该 finding 已有 ≥1 个
非 fabricated、非 infra_error 的真实 sandbox attempt 时才采信；零 attempt/
全 fabricated/全 infra → 维持 needs_context（防洗白通道）。
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.services.agent.agents.base import AgentResult
from app.services.agent.agents.orchestrator import (
    OrchestratorAgent,
    _safe_snippet,
)

# ========== P9-6a: _safe_snippet 直接单测 ==========

class TestSafeSnippet:
    def test_str_sliced_as_is(self):
        assert _safe_snippet("x" * 500, 300) == "x" * 300

    def test_short_str_unchanged(self):
        assert _safe_snippet("hello", 300) == "hello"

    def test_none_returns_none(self):
        assert _safe_snippet(None, 300) is None

    def test_dict_json_serialized(self):
        assert _safe_snippet({"a": 1, "b": 2}, 300) == '{"a": 1, "b": 2}'

    def test_list_json_serialized(self):
        assert _safe_snippet(["line1", "line2"], 300) == '["line1", "line2"]'

    def test_unicode_not_escaped(self):
        out = _safe_snippet({"t": "中文描述"}, 300)
        assert "中文描述" in out

    def test_dict_json_truncated_at_limit(self):
        payload = {"steps": ["x" * 100 for _ in range(10)]}
        out = _safe_snippet(payload, 300)
        assert out is not None and len(out) == 300
        assert out == __import__("json").dumps(payload, ensure_ascii=False)[:300]

    def test_other_type_str_fallback(self):
        assert _safe_snippet(12345, 300) == "12345"

    def test_unserializable_value_str_fallback(self):
        # set 不可 JSON 序列化 → 回落 str(value)，不得抛异常
        out = _safe_snippet({"x": {1, 2}}, 300)
        assert "x" in out and "1" in out


# ========== P9-6b: merge 循环逐条隔离（spy 集成） ==========

class _FakeVerification:
    """立即返回 payload 的假 Verification Agent（success 路径）。"""

    def __init__(self, payload: list):
        self._payload = payload
        self.name = "verification"
        self._registered = False

    async def run(self, _input_data: dict) -> AgentResult:
        return AgentResult(
            success=True,
            data={"findings": [dict(f) for f in self._payload]},
            iterations=3,
            tool_calls=5,
            tokens_used=100,
            duration_ms=20,
        )

    def set_parent_id(self, _pid: str) -> None:
        pass

    def _register_to_registry(self, task=None) -> None:
        self._registered = True

    def reset_dispatch_cancel(self) -> None:
        pass

    def cancel(self) -> None:
        pass


def _merge_finding(fid: str, **overrides: Any) -> dict:
    base = {
        "file_path": f"src/{fid}.py",
        "line_start": 10,
        "vulnerability_type": "sql_injection",
        "title": f"SQLi {fid}",
        "description": f"desc {fid}",
        "severity": "high",
        "confidence": 0.9,
        "poc": f"python3 poc_{fid}.py",
        "code_snippet": f"snippet {fid}",
    }
    base.update(overrides)
    return base


def _make_orch(payload: list, *, trace_raises: str | None = None) -> OrchestratorAgent:
    """装配 _dispatch_agent 走到 merge 段所需最小属性集（_runtime_context
    无 project_root，跳过文件存在性校验）。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.config = SimpleNamespace(name="orchestrator")
    agent._agent_id = "orch-test"
    agent._cancelled = False
    agent._user_cancelled = False
    agent._cancel_callback = None
    agent._all_findings = []
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
    agent._gate_observations = []
    agent.sub_agents = {"verification": _FakeVerification(payload)}
    agent.emit_event = AsyncMock()
    agent._budget_refusal = lambda _name: None
    agent._maybe_request_soft_stop = lambda _a, _name: False
    agent._resolve_dispatch_timeout = lambda _name: 30.0
    agent._build_handoff_for_agent = lambda *_a, **_k: None
    agent._build_trace_summary = lambda: ""
    cov = MagicMock()
    cov.statuses = {}
    cov.gaps = []
    agent._evaluate_current_coverage = lambda: cov

    trace = MagicMock()

    def _add_finding(**kwargs):
        if trace_raises and kwargs.get("title", "").endswith(trace_raises):
            raise RuntimeError("trace backend unavailable")
        return None

    trace.add_finding = MagicMock(side_effect=_add_finding)
    agent.trace_manager = trace
    return agent


def _six_findings() -> list:
    return [
        _merge_finding("f-1"),
        # dict poc：旧代码 dict[:300] → KeyError 硬断点
        _merge_finding(
            "f-2",
            poc={
                "description": "PoC for f-2",
                "steps": ["step a", "step b"],
                "payload": {"id": 1},
            },
        ),
        # list code_snippet：同型切片炸弹
        _merge_finding("f-3", code_snippet=["line1", "line2", "line3"]),
        _merge_finding("f-4"),
        _merge_finding("f-5"),  # trace 对此条抛异常，验逐条隔离
        _merge_finding("f-6"),
    ]


def test_merge_loop_six_findings_all_merged_traced_and_complete_emitted():
    payload = _six_findings()
    orch = _make_orch(payload, trace_raises="SQLi f-5")

    msg = asyncio.run(
        orch._dispatch_agent(
            {"agent": "verification", "task": "验证 6 个 finding", "context": ""}
        )
    )

    # merge 计数=6：无一条因前面异常被丢
    assert len(orch._all_findings) == 6
    # trace 计数=6：每条都尝试落 trace（f-5 抛异常被隔离）
    assert orch.trace_manager.add_finding.call_count == 6
    # dispatch_complete 必发（旧实现永不发射 → 重调度空烧）
    emitted = [c.args[0] for c in orch.emit_event.call_args_list]
    assert "dispatch_complete" in emitted
    # per_finding_trace_error gate=1
    gates = [o for o in orch._gate_observations if o.get("gate") == "per_finding_trace_error"]
    assert len(gates) == 1
    assert "f-5" in gates[0].get("finding_ref", "")
    assert "RuntimeError" in gates[0].get("error_type", "")
    # 成功路径返回正常 observation，而非"## 调度失败"
    assert "调度失败" not in msg
    assert "成功" in msg


def test_dict_poc_and_list_snippet_serialized_in_trace():
    payload = _six_findings()
    orch = _make_orch(payload)

    asyncio.run(
        orch._dispatch_agent(
            {"agent": "verification", "task": "验证 6 个 finding", "context": ""}
        )
    )

    by_title = {
        c.kwargs.get("title"): c.kwargs
        for c in orch.trace_manager.add_finding.call_args_list
    }
    poc_f2 = by_title["SQLi f-2"]["poc"]
    assert isinstance(poc_f2, str)
    assert poc_f2.startswith("{") and "PoC for f-2" in poc_f2
    snippet_f3 = by_title["SQLi f-3"]["code_snippet"]
    assert isinstance(snippet_f3, str)
    assert snippet_f3.startswith("[") and "line1" in snippet_f3
    # description 同样安全归一
    assert by_title["SQLi f-1"]["description"] == "desc f-1"


# ========== P9-8: verdict→verification_status 归一 + 证据前置 ==========

def _normalize_agent() -> OrchestratorAgent:
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._runtime_context = {}
    agent._gate_observations = []
    return agent


def _verdict_finding(verdict: str, attempts: list | None, **overrides: Any) -> dict:
    f = _merge_finding("v-1", title="SQLi v-1")
    f["verdict"] = verdict
    f["sandbox_attempts"] = attempts if attempts is not None else []
    f.pop("verification_status", None)
    f.update(overrides)
    return f


def _real_attempt(summary: str = "static analysis evidence") -> dict:
    return {
        "success": True,
        "exit_code": 0,
        "evidence_summary": summary,
        "command": "python3 poc.py",
    }


class TestVerdictMapping:
    def test_confirmed_with_real_attempt_becomes_static_confirmed(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(
            _verdict_finding("confirmed", [_real_attempt()])
        )
        # 动态 confirmed 仅由确定性证据引擎给出；merge 关口按系统口径落 static_confirmed
        assert out["verification_status"] == "static_confirmed"
        assert out["verdict"] == "static_confirmed"
        assert out["is_verified"] is True

    def test_confirmed_uppercase_normalized(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(
            _verdict_finding("Confirmed", [_real_attempt()])
        )
        assert out["verification_status"] == "static_confirmed"

    def test_confirmed_zero_attempts_blocked_needs_context(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(_verdict_finding("confirmed", []))
        assert out["verification_status"] == "needs_context"
        assert out["verdict"] == "needs_context"
        assert out["is_verified"] is False
        assert out.get("verification_note"), "前置拦截必须留 verification_note"

    def test_confirmed_none_attempts_blocked_needs_context(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(_verdict_finding("confirmed", None))
        assert out["verification_status"] == "needs_context"
        assert out.get("verification_note")

    def test_likely_all_infra_attempts_blocked_needs_context(self):
        agent = _normalize_agent()
        infra = {
            "infra_error": True,
            "exit_code": None,
            "command": "connection aborted",
            "evidence_summary": "",
        }
        out = agent._normalize_finding(
            _verdict_finding("likely", [infra, dict(infra)])
        )
        assert out["verification_status"] == "needs_context"
        assert out.get("verification_note")

    def test_likely_with_real_attempt_static_confirmed(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(
            _verdict_finding("likely", [_real_attempt()])
        )
        assert out["verification_status"] == "static_confirmed"

    def test_mixed_fabricated_and_real_attempt_accepted(self):
        agent = _normalize_agent()
        fabricated = {"fabricated": True, "success": True, "exit_code": 0}
        out = agent._normalize_finding(
            _verdict_finding("confirmed", [fabricated, _real_attempt()])
        )
        assert out["verification_status"] == "static_confirmed"

    def test_all_fabricated_attempts_blocked(self):
        agent = _normalize_agent()
        fabricated = {"fabricated": True, "success": True, "exit_code": 0}
        out = agent._normalize_finding(
            _verdict_finding("confirmed", [fabricated])
        )
        assert out["verification_status"] == "needs_context"
        assert out.get("verification_note")

    def test_false_positive_lands_directly(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(_verdict_finding("false_positive", []))
        assert out["verification_status"] == "false_positive"
        assert out["verdict"] == "false_positive"

    def test_explicit_false_positive_maintained_despite_real_evidence(self):
        # D10：显式标注 FP 遇新证据维持 FP（产品口径）
        agent = _normalize_agent()
        out = agent._normalize_finding(
            _verdict_finding(
                "false_positive", [_real_attempt("VULNERABILITY_CONFIRMED: boom")]
            )
        )
        assert out["verification_status"] == "false_positive"
        assert out["verdict"] == "false_positive"

    def test_not_reproduced_maps_to_not_reproducible(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(_verdict_finding("not_reproduced", []))
        assert out["verification_status"] == "not_reproducible"
        assert out["verdict"] == "not_reproducible"

    def test_not_reproducible_preserved(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(
            _verdict_finding("not_reproducible", [_real_attempt("no vuln output")])
        )
        assert out["verification_status"] == "not_reproducible"

    def test_unknown_verdict_becomes_needs_context(self):
        agent = _normalize_agent()
        out = agent._normalize_finding(_verdict_finding("maybe_so", []))
        assert out["verification_status"] == "needs_context"

    def test_existing_verification_status_not_overridden(self):
        # 权威 verification_status（确定性引擎产出）存在时不做补位映射
        agent = _normalize_agent()
        f = _verdict_finding("confirmed", [])
        f["verification_status"] = "confirmed"
        out = agent._normalize_finding(f)
        assert out["verification_status"] == "confirmed"

    def test_no_verdict_no_status_left_unset(self):
        agent = _normalize_agent()
        f = _merge_finding("v-2", title="SQLi v-2")
        f["sandbox_attempts"] = []
        out = agent._normalize_finding(f)
        assert not out.get("verification_status")
        assert not out.get("verdict")
