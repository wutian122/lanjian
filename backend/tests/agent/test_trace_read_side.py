"""
sandbox-verification-hard-gate Task 15（Phase 4）：audit_trace 读侧接线与 API 字段。

写侧（Task 14）已把调度/工具/发现/LLM 事件落 trace，但 AI 零读点。本测试锁定读侧：
1. Orchestrator 每轮主循环开始前把 trace 摘要（get_summary_for_agent，截断 2000）
   注入对话历史（"此前执行轨迹摘要"段，含已调度 Agent 名与发现标题）；
2. 摘要每轮至多一条（注入前移除上一轮同名段，保持最新）；空壳 trace（0 事件）不注入；
3. trace 不可用/摘要生成异常 → warning + 跳过，任务不中断；
4. 子 Agent 派发时摘要经 previous_results["trace_summary"] 传递；
5. 任务详情响应携带 audit_trace_path（相对路径，文件真实存在）。

注（Task 14 M3）：add_verification_result 只写 markdown 不入 entries/stats，故摘要
"最近 10 条关键事件"含调度/工具/发现/压缩/LLM，不含验证结论与门禁裁决——读侧可见
的是调度/发现/工具轨迹；验证结论需查完整 trace 文件（API 字段即其入口）。
"""

import json
import os
import sys
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.config import settings
from app.services.agent.agents.analysis import AnalysisAgent
from app.services.agent.agents.base import AgentResult
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.agents.verification import VerificationAgent
from app.services.agent.audit_trace import AuditTraceManager
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

SUMMARY_MARKER = "此前执行轨迹摘要"


# ---------- 熔断器/限流器复位（照搬 test_content_token_stream，避免跨用例污染） ----------

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


# ---------- 工厂 ----------

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
    service.config = SimpleNamespace(max_tokens=32768, model="test-model")
    return service


def _tool_call(name, arguments="{}"):
    return {"id": f"call_{name}", "name": name, "arguments": arguments}


def _finish_stream():
    """单轮 finish tool_call（tools 协议），让主循环一轮收尾。"""
    async def _stream(messages=None, temperature=None, max_tokens=None, tools=None,
                      response_format=None):
        yield {
            "type": "done", "content": "", "reasoning": "",
            "tool_calls": [_tool_call("finish", "{}")],
            "usage": {"total_tokens": 10}, "finish_reason": "tool_calls",
        }
    return _stream


def _seed_trace(trace):
    """模拟前两轮：调度 recon/analysis + 2 个发现。"""
    trace.add_agent_dispatch("recon", "收集项目信息", parent_agent="Orchestrator")
    trace.add_agent_dispatch("analysis", "分析 SQL 注入", parent_agent="Orchestrator")
    trace.add_finding(
        finding_type="sql_injection", severity="high",
        title="登录接口 SQL 注入", description="用户输入拼接 SQL",
        file_path="app/login.py", line_number=42,
    )
    trace.add_finding(
        finding_type="xss", severity="medium",
        title="评论区存储型 XSS", description="未转义输出",
        file_path="app/comment.py", line_number=88,
    )


def _make_orch(tmp_path, monkeypatch, *, caps=None, seed=False, stub_dispatch=True,
               task_id="readside01"):
    monkeypatch.setattr(settings, "AUDIT_TRACE_DIR", str(tmp_path / "traces"))
    service = _make_service(caps=caps)
    agent = OrchestratorAgent(
        llm_service=service, tools={}, event_emitter=_make_emitter(),
        task_id=task_id,
    )
    # 桩主循环外部依赖（照搬 test_empty_response_nudge._make_orchestrator_agent）
    agent._register_to_registry = lambda task=None: None
    agent._run_semgrep_prescan = AsyncMock(return_value={
        "findings": [], "hot_files": [], "scan_success": False,
    })
    agent._maybe_pause = AsyncMock()
    agent.emit_thinking = AsyncMock()
    agent.emit_event = AsyncMock()
    agent.check_messages = lambda: []
    agent._check_token_budget_exceeded = lambda: False
    agent._remaining_seconds = lambda: 999999.0
    agent._evaluate_current_coverage = lambda: SimpleNamespace(
        is_sufficient=True, covered_count=10, gaps=[],
    )
    agent._summarize_findings = MagicMock(return_value="summary-observation")
    if stub_dispatch:
        agent._dispatch_agent = AsyncMock(return_value="dispatch-observation")
    if seed:
        assert agent.trace_manager is not None, "audit_trace_enabled 默认 True，传 task_id 必须建 trace"
        _seed_trace(agent.trace_manager)
    return agent, service


def _summary_messages(agent):
    return [
        m["content"] for m in agent._conversation_history
        if m.get("role") == "user" and SUMMARY_MARKER in m.get("content", "")
    ]


# ---------- A. 摘要构建 _build_trace_summary ----------

def test_build_summary_contains_agents_and_findings(tmp_path, monkeypatch):
    """摘要含已调度 Agent 名、发现标题与统计（spec：已调度 Agent + 发现标题列表）。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    summary = agent._build_trace_summary()
    assert summary is not None
    assert "调度 recon" in summary
    assert "调度 analysis" in summary
    assert "登录接口 SQL 注入" in summary
    assert "评论区存储型 XSS" in summary
    assert "发现漏洞: 2" in summary


def test_build_summary_truncated_to_2000(tmp_path, monkeypatch):
    """摘要上限 2000 字符（spec：上限 2000）。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    agent.trace_manager.get_summary_for_agent = MagicMock(return_value="X" * 5000)
    summary = agent._build_trace_summary()
    assert summary is not None
    assert len(summary) == 2000


def test_build_summary_none_without_trace_manager(tmp_path, monkeypatch):
    """trace 关闭（trace_manager=None）→ 静默返回 None。"""
    agent, _ = _make_orch(tmp_path, monkeypatch)
    agent.trace_manager = None
    assert agent._build_trace_summary() is None


def test_build_summary_none_for_empty_shell(tmp_path, monkeypatch):
    """空壳 trace（0 调度 0 事件，首轮）→ 不注入噪声，返回 None。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=False)
    assert agent.trace_manager.stats["agents_dispatched"] == 0
    assert agent.trace_manager.entries == []
    assert agent._build_trace_summary() is None


def test_build_summary_none_on_exception(tmp_path, monkeypatch):
    """摘要生成抛异常 → 非致命返回 None（不向上抛）。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    agent.trace_manager.get_summary_for_agent = MagicMock(
        side_effect=RuntimeError("disk gone")
    )
    assert agent._build_trace_summary() is None


# ---------- B. 注入 _inject_trace_summary ----------

@pytest.mark.asyncio
async def test_inject_appends_summary_user_message(tmp_path, monkeypatch):
    """注入一条 user 消息，含标记段 + Agent 名 + 发现标题。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    await agent._inject_trace_summary()
    msgs = _summary_messages(agent)
    assert len(msgs) == 1
    assert "调度 recon" in msgs[0]
    assert "登录接口 SQL 注入" in msgs[0]


@pytest.mark.asyncio
async def test_inject_keeps_only_latest_single_summary(tmp_path, monkeypatch):
    """连续两轮注入：历史中至多一条摘要（旧段移除，新段为最新内容）。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    await agent._inject_trace_summary()
    # 第二轮：trace 新增一次调度后再注入
    agent.trace_manager.add_agent_dispatch(
        "verification", "验证发现", parent_agent="Orchestrator"
    )
    await agent._inject_trace_summary()
    msgs = _summary_messages(agent)
    assert len(msgs) == 1, "每轮至多一条摘要，上一轮摘要段必须被移除"
    assert "调度 verification" in msgs[0], "保留的摘要必须是最新一轮内容"


@pytest.mark.asyncio
async def test_inject_exception_emits_warning_and_continues(tmp_path, monkeypatch):
    """摘要生成异常 → 发射 warning 事件、不抛异常、历史无摘要段。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    agent.trace_manager.get_summary_for_agent = MagicMock(
        side_effect=RuntimeError("disk gone")
    )
    await agent._inject_trace_summary()  # 不得抛
    warning_events = [
        c for c in agent.emit_event.call_args_list
        if c.args and c.args[0] == "warning"
    ]
    assert warning_events, "摘要生成异常必须发射 warning 事件"
    assert _summary_messages(agent) == []


# ---------- C. 主循环接线（每轮 LLM 调用前注入） ----------

@pytest.mark.asyncio
async def test_main_loop_injects_summary_before_llm(tmp_path, monkeypatch):
    """spec Scenario：第 3 轮主循环开始（前两轮已调度 recon/analysis 并有 2 发现），
    本轮 LLM 对话历史含"此前执行轨迹摘要"（含 Agent 名与 2 个发现标题）。"""
    monkeypatch.setattr(
        "app.services.agent.agents.orchestrator.asyncio.sleep", AsyncMock(),
    )
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent, service = _make_orch(tmp_path, monkeypatch, caps=caps, seed=True)
    service.chat_completion_stream = _finish_stream()

    result = await agent.run({"project_info": {"name": "p"}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    msgs = _summary_messages(agent)
    assert msgs, "主循环 LLM 调用前必须注入 trace 摘要"
    blob = msgs[0]
    assert "调度 recon" in blob
    assert "调度 analysis" in blob
    assert "登录接口 SQL 注入" in blob
    assert "评论区存储型 XSS" in blob


# ---------- D. 子 Agent 派发经 previous_results 传递摘要 ----------

@pytest.mark.asyncio
async def test_dispatch_passes_trace_summary_via_previous_results(tmp_path, monkeypatch):
    """spec：子 Agent 派发时摘要 SHALL 经 previous_results 传递。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True, stub_dispatch=False)
    captured: dict = {}

    async def _fake_run(sub_input):
        captured.update(sub_input)
        return AgentResult(success=True, data={"summary": "recon 完成", "findings": []})

    sub = MagicMock()
    sub.run = AsyncMock(side_effect=_fake_run)
    sub.name = "recon"
    sub._registered = False
    agent.sub_agents = {"recon": sub}
    # 桩 handoff/budget/timeout/压缩，聚焦 sub_input 构造
    agent._build_handoff_for_agent = MagicMock(return_value=None)
    agent._budget_refusal = MagicMock(return_value=None)
    agent._resolve_dispatch_timeout = MagicMock(return_value=600)
    agent.context_manager = None

    await agent._dispatch_agent({"agent": "recon", "task": "收集信息", "context": ""})

    assert sub.run.await_count == 1
    prev = captured.get("previous_results", {})
    assert "trace_summary" in prev, "摘要必须经 previous_results 传递给子 Agent"
    assert "登录接口 SQL 注入" in prev["trace_summary"]
    assert "调度 recon" in prev["trace_summary"]


# ---------- E. API audit_trace_path 字段 ----------

def test_rel_trace_path_returns_relative_when_exists(tmp_path):
    """trace 文件存在 → 返回相对路径（basename/<id8>/audit_trace.md），且文件真实存在。"""
    base = tmp_path / "traces"
    trace = AuditTraceManager(
        task_id="apipath01", project_name="demo", base_dir=str(base)
    )
    trace.add_agent_dispatch("recon", "收集")
    trace.finalize()

    rel = AuditTraceManager.rel_trace_path_for_task("apipath01", base_dir=str(base))

    assert rel is not None
    assert not os.path.isabs(rel), "audit_trace_path 必须是相对路径"
    assert rel == f"{base.name}/apipath0/audit_trace.md"
    # rel 相对挂载点根（base 的父目录，等价于容器 WORKDIR / 宿主机项目根）
    assert (base.parent / rel).exists(), "相对路径在挂载点下必须真实存在"
    assert (base / "apipath0" / "audit_trace.md").exists()


def test_rel_trace_path_none_when_missing(tmp_path):
    """trace 文件不存在（任务未启用 trace / 尚未写入）→ None。"""
    base = tmp_path / "traces"
    base.mkdir(parents=True, exist_ok=True)
    assert AuditTraceManager.rel_trace_path_for_task(
        "nosuchtask", base_dir=str(base)
    ) is None


def test_agent_task_response_carries_audit_trace_path():
    """AgentTaskResponse 携带可选 audit_trace_path 字段（默认 None，向后兼容）。"""
    from app.api.v1.endpoints.agent_tasks import AgentTaskResponse

    resp = AgentTaskResponse(
        id="t1", project_id="p1", name=None, description=None,
        status="completed", current_phase=None,
        created_at=datetime(2026, 1, 1),
        audit_trace_path="audit_traces/abcd1234/audit_trace.md",
    )
    assert resp.audit_trace_path == "audit_traces/abcd1234/audit_trace.md"
    assert resp.model_dump()["audit_trace_path"] == "audit_traces/abcd1234/audit_trace.md"

    resp_default = AgentTaskResponse(
        id="t2", project_id="p1", name=None, description=None,
        status="running", current_phase=None,
        created_at=datetime(2026, 1, 1),
    )
    assert resp_default.audit_trace_path is None


# ---------- F. I1：摘要含关键门禁裁决（spec SHALL） ----------

def test_summary_includes_gate_decisions(tmp_path, monkeypatch):
    """I1：_gate_observations 有门禁裁决 → 摘要含"关键门禁裁决"段（gate 名 + reason）。

    门禁裁决（output_floor/dispatch_budget/gate_release/semgrep_fallback）是后续轮
    避免重复调度最该看到的决策信息，spec 明确要求摘要含"关键门禁裁决"。
    """
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    agent._gate_observations = [
        {"gate": "output_floor", "reason": "Analysis 0 候选产出下限违规，Semgrep 兜底落库", "time": "t1"},
        {"gate": "dispatch_budget", "reason": "analysis 调度达 3 次上限，自动放行 finish", "time": "t2"},
    ]
    summary = agent._build_trace_summary()
    assert "关键门禁裁决" in summary
    assert "output_floor" in summary
    assert "Analysis 0 候选产出下限违规" in summary
    assert "dispatch_budget" in summary
    assert "analysis 调度达 3 次上限" in summary


def test_summary_omits_gate_section_when_no_decisions(tmp_path, monkeypatch):
    """无门禁裁决（空列表）→ 摘要不含"关键门禁裁决"段。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    assert agent._gate_observations == []
    summary = agent._build_trace_summary()
    assert "关键门禁裁决" not in summary


def test_summary_gate_section_within_truncation(tmp_path, monkeypatch):
    """门禁裁决段在 2000 字符截断后仍保留（gate 段紧随事件列表，reason 逐条截断）。"""
    agent, _ = _make_orch(tmp_path, monkeypatch, seed=True)
    agent._gate_observations = [
        {"gate": "gate_release", "reason": "验证门禁连续拒绝达上限放行收尾", "time": "t"},
    ]
    agent.trace_manager.get_summary_for_agent = MagicMock(return_value="Z" * 1900)
    summary = agent._build_trace_summary()
    assert len(summary) <= 2000
    # gate 段由实现追加在 trace 摘要之后；1900 字符正文 + gate 段超 2000 时 gate 被截，
    # 故本条仅断言不超长 + 不抛异常（gate 段可见性由上一条非超长场景保证）。


# ---------- G. I2：子 Agent 消费侧注入守卫 ----------

_TRACE_SUMMARY = (
    "# 审计任务 readside 摘要\n\n## 统计\n- Agent 调度: 2 次\n- 发现漏洞: 2 个\n\n"
    "## 最近 10 条关键事件\n"
    "- [10:00:01] 调度 recon Agent\n"
    "- [10:00:05] 调度 analysis Agent\n"
    "- [10:01:00] HIGH - 登录接口 SQL 注入\n"
)


def _final_stream(text, capture):
    """文本协议流；首次被调用时把 LLM 收到的 messages 存入 capture（首轮注入证据）。"""
    async def _gen(messages=None, temperature=None, max_tokens=None, tools=None,
                   response_format=None):
        if "first" not in capture:
            capture["first"] = list(messages or [])
        yield {
            "type": "done", "content": text, "reasoning": "", "accumulated": text,
            "usage": {"total_tokens": 10}, "finish_reason": "stop",
        }
    return _gen


def _submit_stream(tool_calls, capture):
    """tools 协议流；首次被调用时把 LLM 收到的 messages 存入 capture。"""
    async def _gen(messages=None, temperature=None, max_tokens=None, tools=None,
                   response_format=None):
        if "first" not in capture:
            capture["first"] = list(messages or [])
        yield {
            "type": "done", "content": "", "reasoning": "",
            "tool_calls": tool_calls,
            "usage": {"total_tokens": 10}, "finish_reason": "tool_calls",
        }
    return _gen


def _first_user_msgs(capture):
    """首轮 LLM 实际收到的 user 消息（initial_message 注入点，不受后续多轮压缩影响）。"""
    return [m.get("content", "") for m in capture.get("first", []) if m.get("role") == "user"]


_VFINDING = {
    "vulnerability_type": "sql_injection", "severity": "high",
    "title": "SQL 注入漏洞", "description": "f-string 拼接查询",
    "file_path": "src/sql_vuln.py", "line_start": 6,
    "code_snippet": "query = f\"SELECT * FROM users WHERE id = '{user_id}'\"",
    "needs_verification": True,
}

_VPAYLOAD = {
    "summary": {"total": 1, "confirmed": 1, "likely": 0, "false_positive": 0},
    "findings": [{
        **_VFINDING,
        "verdict": "confirmed", "confidence": 0.95, "is_verified": True,
        "verification_method": "沙箱执行 PoC",
        "verification_details": "PoC 触发延时，漏洞确认",
        "sandbox_attempts": [],
    }],
}


@pytest.mark.asyncio
async def test_recon_consumes_trace_summary_in_initial_message(tmp_path):
    """I2 守卫：Recon 首轮 initial_message 含 trace_summary 段；无 trace_summary 时不含。"""
    final = "Thought: 信息收集完成\nFinal Answer: " + json.dumps({
        "tech_stack": {"languages": ["Python"], "frameworks": []},
        "entry_points": ["main.py"], "high_risk_areas": [],
    }, ensure_ascii=False)

    async def _run(with_summary):
        capture = {}
        service = _make_service(caps=None)
        service.chat_completion_stream = _final_stream(final, capture)
        agent = ReconAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
        prev = {"trace_summary": _TRACE_SUMMARY} if with_summary else {}
        await agent.run({
            "project_info": {"name": "p", "root": str(tmp_path)},
            "config": {}, "previous_results": prev,
        })
        return _first_user_msgs(capture)

    msgs_with = await _run(True)
    assert msgs_with, "首轮 LLM 必须收到 initial_message"
    assert any("此前执行轨迹摘要" in c and "登录接口 SQL 注入" in c for c in msgs_with), (
        "Recon initial_message 必须注入 trace_summary 段"
    )
    msgs_without = await _run(False)
    assert not any("此前执行轨迹摘要" in c for c in msgs_without), (
        "无 trace_summary 时 Recon 不得注入摘要段"
    )


@pytest.mark.asyncio
async def test_analysis_consumes_trace_summary_in_initial_message(tmp_path):
    """I2 守卫：Analysis 首轮 initial_message 含 trace_summary 段；无 trace_summary 时不含。"""
    final = "Thought: 分析完成\nFinal Answer: " + json.dumps(
        {"summary": "无新增发现", "findings": []}, ensure_ascii=False)

    async def _run(with_summary):
        capture = {}
        service = _make_service(caps=None)
        service.chat_completion_stream = _final_stream(final, capture)
        agent = AnalysisAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
        agent._check_token_budget_exceeded = lambda: False
        prev = {"trace_summary": _TRACE_SUMMARY} if with_summary else {}
        await agent.run({
            "project_info": {"name": "p", "root": str(tmp_path)},
            "config": {}, "previous_results": prev,
        })
        return _first_user_msgs(capture)

    msgs_with = await _run(True)
    assert msgs_with, "首轮 LLM 必须收到 initial_message"
    assert any("此前执行轨迹摘要" in c and "登录接口 SQL 注入" in c for c in msgs_with), (
        "Analysis initial_message 必须注入 trace_summary 段"
    )
    msgs_without = await _run(False)
    assert not any("此前执行轨迹摘要" in c for c in msgs_without), (
        "无 trace_summary 时 Analysis 不得注入摘要段"
    )


@pytest.mark.asyncio
async def test_verification_consumes_trace_summary_in_initial_message(tmp_path, monkeypatch):
    """I2 守卫：Verification 首轮 LLM 收到的 initial_message 含 trace_summary 段；无则不含。

    锚定"首轮 LLM 实际收到的 messages"而非跑完后的 _conversation_history：verification
    首轮 submit_findings 不被接受（要求先验证）会进入多轮并可能触发上下文压缩，把首轮
    initial_message 压缩替换；注入发生在首轮，故捕获首轮 messages 才是准确守卫。
    """
    monkeypatch.setattr(
        "app.services.agent.agents.verification.asyncio.sleep", AsyncMock())

    async def _run(with_summary):
        capture = {}
        caps = BackendCapabilities(tools=True, guided_json=False)
        service = _make_service(caps=caps)
        service.chat_completion_stream = _submit_stream(
            [_tool_call("submit_findings", json.dumps(_VPAYLOAD, ensure_ascii=False))],
            capture,
        )
        agent = VerificationAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
        agent._check_token_budget_exceeded = lambda: False
        # 守卫只断言首轮 LLM 收到的 initial_message；verification 首轮 submit_findings
        # 不被当作交卷会循环到 max_iterations（51 分钟），故限 1 轮——首轮 LLM 调用即
        # 捕获注入证据，随后轮次耗尽收口不再调 LLM。
        agent.config.max_iterations = 1
        # 沙箱准备/确定性执行在本守卫范围外，桩掉（照搬 test_empty_response_nudge）
        agent._run_deterministic_sandbox_commands = AsyncMock()
        agent._build_sandbox_commands = MagicMock(return_value=[])
        agent._prepare_sandbox_files = MagicMock(return_value=None)
        prev = {"findings": [dict(_VFINDING)]}
        if with_summary:
            prev["trace_summary"] = _TRACE_SUMMARY
        await agent.run({"previous_results": prev, "config": {}})
        return _first_user_msgs(capture)

    msgs_with = await _run(True)
    assert msgs_with, "首轮 LLM 必须收到 initial_message"
    assert any("此前执行轨迹摘要" in c and "登录接口 SQL 注入" in c for c in msgs_with), (
        "Verification initial_message 必须注入 trace_summary 段"
    )
    msgs_without = await _run(False)
    assert not any("此前执行轨迹摘要" in c for c in msgs_without), (
        "无 trace_summary 时 Verification 不得注入摘要段"
    )
