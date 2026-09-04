"""sandbox-verification-hard-gate Task 7（Phase 2 三合一）：弹性退出 / 预算耗尽补跑 / 兜底遍历。

三条豁免路径收口（spec「每个 finding 终态前 SHALL 至少一次沙箱执行或显式豁免标记」）：

- f 弹性退出（verification.py 弹性门禁放行处）：total_attempts 达弹性上限放行
  finish 时，剩余未验证且未自注原因的 finding SHALL 写
  sandbox_skip_reason="elastic_exit"——零尝试 finding 终态 needs_context 并带
  该原因（硬门禁条件 2：显式豁免标记），不再零证据静默收尾；
- g 预算耗尽（token/迭代预算 break、LLM 未交卷）：收口前 SHALL 补跑剩余未执行
  的确定性 PoC（初始确定性执行中异常/中断的命令），runner 幂等——已记录
  attempt 的命令按 finding_id 跳过，不重复执行；
- h LLM 拒调兜底（循环结束 0 次 sandbox_exec）：程序化兜底 SHALL 遍历全部
  sandbox_commands（不再只跑 [0]），复用确定性 runner，证据绑定到全部 finding。

skip_reason 消费裁决（见 test_elastic_exit_consumption_alignment）：
elastic_exit 是验证 Agent 内的显式豁免（硬门禁台账条件 2 满足），终态仍为
needs_context / is_verified=False——orchestrator 全量门禁
（UNVERIFIED_TERMINAL，orchestrator.py）不含 needs_context，仍计未验证可
重派 verification；最终 R4 放行由 Task 8 标 gate_release_after_max_redispatch
并在报告呈现。elastic_exit 不升级验证状态。
"""

import json
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import (
    VerificationAgent,
    compute_verification_status,
)
from app.services.agent.core.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_llm_circuit,
)
from app.services.agent.core.rate_limiter import get_llm_rate_limiter
from app.services.agent.structured_output import BackendCapabilities

_TIMEOUT_CONFIG = {
    "llm_first_token_timeout": 30,
    "llm_stream_timeout": 60,
    "agent_timeout": 1800,
    "sub_agent_timeout": 600,
    "tool_timeout": 60,
}


# ---------- 公共工厂（同 test_empty_response_nudge 模式） ----------


def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    for name in [
        "emit_info", "emit_warning", "emit_error", "emit_thinking",
        "emit_tool_call", "emit_tool_result", "emit_finding", "emit_progress",
        "emit_phase_start", "emit_phase_complete", "emit_task_complete",
    ]:
        setattr(e, name, AsyncMock())
    return e


def _make_service(caps=None):
    service = MagicMock()
    service.backend_capabilities = caps
    service.get_agent_timeout_config = MagicMock(return_value=_TIMEOUT_CONFIG)
    service.config = SimpleNamespace(max_tokens=32768)
    return service


def _reset_resilience():
    c = get_llm_circuit()
    c._state = CircuitState.CLOSED
    c._stats = CircuitStats()
    c._half_open_calls = 0
    c._last_state_change = time.time()
    lim = get_llm_rate_limiter()
    lim.tokens = float(lim.burst)
    lim.last_update = time.monotonic()


@pytest.fixture(autouse=True)
def _reset():
    _reset_resilience()
    yield
    _reset_resilience()


def _tool_call(name, arguments="{}"):
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


def _finding(file_path: str, line: int, **overrides) -> dict:
    f = {
        "title": f"SQL 注入 {file_path}",
        "vulnerability_type": "sql_injection",
        "severity": "high",
        "file_path": file_path,
        "line_start": line,
        "description": "f-string 拼接查询",
        "code_snippet": "query = f\"SELECT * FROM users WHERE id = '{uid}'\"",
        "needs_verification": True,
    }
    f.update(overrides)
    return f


def _agent(caps=None):
    service = _make_service(caps=caps)
    agent = VerificationAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
    agent._check_token_budget_exceeded = lambda: False
    return agent


def _fake_sandbox_manager(execute_impl):
    """execute_impl: async callable(command=, host_project_dir=, timeout=, network_mode=) -> dict"""
    mgr = MagicMock()
    mgr.execute_with_files = AsyncMock(side_effect=execute_impl)
    return mgr


def _success_result():
    return {
        "exit_code": 0,
        "stdout": "Verification Complete\nPoC finished with output " + "x" * 60,
        "stderr": "",
    }


def _patch_sandbox(agent, mgr):
    agent._get_sandbox_manager = lambda: mgr
    agent._prepare_sandbox_files = MagicMock(return_value="/fake/project/root")


# ============ f：弹性退出放行 → 未验证 finding 带 elastic_exit ============


@pytest.mark.asyncio
async def test_elastic_exit_marks_unverified_findings():
    """弹性上限放行 finish：2 个 finding 全部未成功验证（0 真实 attempt 记录），
    LLM 只报告了 A、漏报 B。收口后 A（回填传播）与 B（orig 兜底）都必须带
    sandbox_skip_reason=elastic_exit，终态 needs_context / is_verified=False。"""
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _agent(caps=caps)

    # 确定性执行被桩为"尝试次数达标但无证据落账"（弹性计数器达标即可触发放行）
    async def _fake_det(commands, root):
        agent._sandbox_exec_attempts = 12
        agent._sandbox_exec_calls = 12

    agent._run_deterministic_sandbox_commands = _fake_det

    findings = [_finding("app/a.py", 10), _finding("app/b.py", 20)]
    payload = {
        "summary": {"total": 1, "confirmed": 0, "likely": 0, "false_positive": 0},
        "findings": [{
            "file_path": "app/a.py",
            "line_start": 10,
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "title": "SQL 注入 app/a.py",
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
        "previous_results": {"findings": [dict(f) for f in findings]},
        "config": {},
    })

    assert result.success, f"弹性退出应放行收尾: {result.error}"
    out = result.data["findings"]
    assert len(out) == 2, f"漏报的 B 必须由兜底绑定收回，实际 {len(out)} 条"
    by_path = {(f.get("file_path"), f.get("line_start")): f for f in out}
    for key in [("app/a.py", 10), ("app/b.py", 20)]:
        f = by_path[key]
        assert f.get("sandbox_skip_reason") == "elastic_exit", (
            f"{key} 必须带 elastic_exit 豁免标记，got {f.get('sandbox_skip_reason')!r}"
        )
        assert f.get("verification_status") == "needs_context"
        assert f.get("is_verified") is False
        assert "elastic_exit" in str(f.get("verification_note") or ""), (
            "needs_context 诊断 notes 必须带出 elastic_exit 原因"
        )


@pytest.mark.asyncio
async def test_elastic_exit_preserves_llm_self_marked_reason():
    """LLM 已在 Final Answer 自注 sandbox_skip_reason 的 finding 保留 LLM 原因
    （系统标记不覆盖）；漏报/未标注的 finding 才写 elastic_exit。"""
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _agent(caps=caps)

    async def _fake_det(commands, root):
        agent._sandbox_exec_attempts = 12
        agent._sandbox_exec_calls = 12

    agent._run_deterministic_sandbox_commands = _fake_det

    findings = [_finding("app/a.py", 10), _finding("app/b.py", 20)]
    llm_reason = "沙箱中无法读取目标源码（Source file not found）"
    payload = {
        "summary": {"total": 1, "confirmed": 0, "likely": 0, "false_positive": 0},
        "findings": [{
            "file_path": "app/a.py",
            "line_start": 10,
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "title": "SQL 注入 app/a.py",
            "verdict": "needs_context",
            "confidence": 0.5,
            "is_verified": False,
            "verification_method": "代码阅读",
            "verification_details": "源码不可读",
            "sandbox_skip_reason": llm_reason,
            "sandbox_attempts": [],
        }],
    }
    agent.llm_service.chat_completion_stream = _done_with_tool_calls(
        [_tool_call("submit_findings", json.dumps(payload, ensure_ascii=False))]
    )

    result = await agent.run({
        "previous_results": {"findings": [dict(f) for f in findings]},
        "config": {},
    })

    assert result.success, f"弹性退出应放行收尾: {result.error}"
    by_path = {(f.get("file_path"), f.get("line_start")): f for f in result.data["findings"]}
    assert by_path[("app/a.py", 10)].get("sandbox_skip_reason") == llm_reason, (
        "LLM 自注原因优先，不得被 elastic_exit 覆盖"
    )
    assert by_path[("app/b.py", 20)].get("sandbox_skip_reason") == "elastic_exit"


# ============ g：预算耗尽 → 剩余确定性 PoC 补跑后收口 ============


@pytest.mark.asyncio
async def test_budget_exhaustion_reruns_unattempted_deterministic_pocs():
    """token 预算在首轮 LLM 调用前耗尽 → break 收口。初始确定性执行中前 2 条
    PoC 因瞬时异常未落 attempt，补跑必须只重跑这 2 条（幂等：成功的不重复），
    收口后全部 finding 均有沙箱 attempt。"""
    agent = _agent(caps=None)
    n = 3
    call_count = {"n": 0}

    async def _execute(*, command, host_project_dir, timeout, network_mode):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < 2:
            # 初始确定性执行：前 2 条瞬时故障（runner 逐条 catch，不落 attempt）
            raise RuntimeError("docker daemon transient startup failure")
        return _success_result()

    mgr = _fake_sandbox_manager(_execute)
    _patch_sandbox(agent, mgr)
    # 预算门禁：首轮即耗尽（break 在 LLM 调用之前）
    agent._check_token_budget_exceeded = lambda: True
    agent.llm_service.chat_completion_stream = MagicMock()  # 不应被调用

    findings = [_finding(f"app/f{i}.py", 100 + i) for i in range(n)]
    result = await agent.run({
        "previous_results": {"findings": [dict(f) for f in findings]},
        "config": {},
    })

    assert result.success, f"预算耗尽也应收口成功: {result.error}"
    # 初始 n 次（2 异常 + n-2 成功）+ 补跑 2 次 = n+2；幂等保证成功的不重跑，
    # 且 h 兜底不得再触发（attempts 已非 0）
    assert mgr.execute_with_files.call_count == n + 2, (
        f"应只补跑 2 条未执行 PoC，实际执行 {mgr.execute_with_files.call_count} 次"
    )
    out = result.data["findings"]
    assert len(out) == n
    for f in out:
        assert f.get("sandbox_attempts"), (
            f"{f.get('file_path')} 补跑后必须绑定沙箱 attempt"
        )


# ============ h：0 次 sandbox_exec 兜底遍历全部 commands ============


@pytest.mark.asyncio
async def test_fallback_traverses_all_commands_when_zero_exec():
    """初始确定性执行全部异常（0 attempt 落账），LLM 交卷并为全部 finding 自注
    skip_reason（门禁据此放行）。h 兜底必须遍历全部 sandbox_commands（不再只
    跑 [0]）：n 条 PoC 全部补跑成功，证据绑定到全部 finding，状态由证据推导
    （not_reproducible），LLM 的 skip 声明不阻止程序化执行。"""
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _agent(caps=caps)
    n = 3
    call_count = {"n": 0}

    async def _execute(*, command, host_project_dir, timeout, network_mode):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < n:
            # 初始确定性执行：全部基础设施异常（0 attempt 落账）
            raise RuntimeError("Docker not available: connection aborted")
        return _success_result()

    mgr = _fake_sandbox_manager(_execute)
    _patch_sandbox(agent, mgr)

    findings = [_finding(f"app/h{i}.py", 200 + i) for i in range(n)]
    payload_findings = []
    for i in range(n):
        payload_findings.append({
            "file_path": f"app/h{i}.py",
            "line_start": 200 + i,
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "title": f"SQL 注入 app/h{i}.py",
            "verdict": "needs_context",
            "confidence": 0.4,
            "is_verified": False,
            "verification_method": "代码阅读",
            "verification_details": "无法沙箱验证",
            "sandbox_skip_reason": "沙箱环境不可用，无法动态验证",
            "sandbox_attempts": [],
        })
    payload = {
        "summary": {"total": n, "confirmed": 0, "likely": 0, "false_positive": 0},
        "findings": payload_findings,
    }
    agent.llm_service.chat_completion_stream = _done_with_tool_calls(
        [_tool_call("submit_findings", json.dumps(payload, ensure_ascii=False))]
    )

    result = await agent.run({
        "previous_results": {"findings": [dict(f) for f in findings]},
        "config": {},
    })

    assert result.success, f"兜底后应收口成功: {result.error}"
    # 初始 n 次全异常 + 兜底遍历 n 条 = 2n（旧实现只跑 [0]，应为 n+1 且仅 1 条有证据）
    assert mgr.execute_with_files.call_count == 2 * n, (
        f"兜底必须遍历全部 {n} 条命令，实际执行 {mgr.execute_with_files.call_count} 次"
    )
    out = result.data["findings"]
    assert len(out) == n
    for f in out:
        assert f.get("sandbox_attempts"), (
            f"{f.get('file_path')} 兜底遍历后必须绑定沙箱 attempt（旧实现仅 [0] 有）"
        )
        # 证据优先于 LLM 的 skip 声明：PoC 真实跑完未复现 → not_reproducible
        assert f.get("verification_status") == "not_reproducible", (
            f"{f.get('file_path')} 有真实 attempt 未复现应判 not_reproducible，"
            f"got {f.get('verification_status')}"
        )


# ============ ⑤ runner 幂等：三路径互不重复执行 ============


@pytest.mark.asyncio
async def test_deterministic_runner_idempotent_skips_executed_commands():
    """_run_deterministic_sandbox_commands 重复调用时，已记录 attempt 的命令
    按 finding_id 跳过（g 补跑 / h 兜底复用同一 runner 的幂等基础）。"""
    agent = _agent(caps=None)
    agent._init_sandbox_counters()

    async def _ok(**kwargs):
        return _success_result()

    mgr = _fake_sandbox_manager(_ok)
    _patch_sandbox(agent, mgr)

    findings = [_finding(f"app/i{i}.py", 300 + i) for i in range(3)]
    commands = agent._build_sandbox_commands(findings)
    assert len(commands) == 3

    await agent._run_deterministic_sandbox_commands(commands, "/fake/project/root")
    assert mgr.execute_with_files.call_count == 3
    assert agent._sandbox_exec_attempts == 3

    # 第二次调用（模拟 g 补跑 / h 兜底）：全部已落账 → 0 次重复执行
    await agent._run_deterministic_sandbox_commands(commands, "/fake/project/root")
    assert mgr.execute_with_files.call_count == 3, (
        "已成功记录 attempt 的命令不得重复执行"
    )
    assert agent._sandbox_exec_attempts == 3


# ============ ④ skip_reason 消费对齐（弹性豁免语义裁决） ============


def test_elastic_exit_consumption_alignment():
    """elastic_exit 消费裁决（与 orchestrator 全量门禁口径对齐）：

    1. 状态引擎：零 attempt + elastic_exit → needs_context / is_verified=False，
       notes 带出原因（spec 语义 needs_context(elastic_exit)）；
    2. 硬门禁台账：skip_reason 非空满足"显式豁免"条件（条件 2），finding 不再
       是零证据静默收尾；
    3. orchestrator 全量门禁：UNVERIFIED_TERMINAL 不含 needs_context →
       elastic_exit finding 仍计未验证，可触发重派（最终 R4 放行归 Task 8）；
       elastic_exit 绝不升级为已验证。
    """
    finding = {"sandbox_skip_reason": "elastic_exit"}
    status, is_verified, notes = compute_verification_status(finding, [])
    assert status == "needs_context"
    assert is_verified is False
    assert "elastic_exit" in str(notes.get("sandbox_skip_reason") or "")

    # 硬门禁三条件：attempts 非空 / skip_reason 非空 / 全 infra_error
    hard_gate_satisfied = bool(
        finding.get("sandbox_attempts")
        or finding.get("sandbox_skip_reason")
    )
    assert hard_gate_satisfied is True

    # orchestrator 全量门禁谓词（orchestrator.py UNVERIFIED_TERMINAL 同口径）
    UNVERIFIED_TERMINAL = {
        "confirmed", "static_confirmed", "not_reproducible", "false_positive",
    }
    normalized = {
        "verification_status": "needs_context",
        "is_verified": False,
        "sandbox_skip_reason": "elastic_exit",
    }
    counts_as_unverified = (
        normalized.get("verification_status") not in UNVERIFIED_TERMINAL
        and normalized.get("is_verified") is not True
    )
    assert counts_as_unverified is True, (
        "elastic_exit 终态 needs_context 仍须被 orchestrator 计为未验证（可重派），"
        "不得被豁免标记洗白成已验证"
    )
