"""
sandbox-verification-hard-gate Task 14（Phase 4）：audit_trace 写点补全。

AuditTraceManager.add_tool_call / add_llm_call / add_verification_result
此前为零调用死代码（trace 的工具/Token/验证栏目恒 0）。本测试锁定三个写点
在生产路径的接线：
1. BaseAgent.execute_tool 收尾 → add_tool_call（成功与失败调用均记录）
2. BaseAgent.stream_llm_call done 后 → add_llm_call（每轮一次，含 token/耗时/截断标志）
3. VerificationAgent 验证收尾 → 每个 finding 一条 add_verification_result
4. trace 写入失败一律非致命（主流程结果不受影响）
5. OrchestratorAgent 创建 trace_manager 后注入全部子 Agent（含后注册）
"""

import asyncio
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.config import settings
from app.models.agent_task import VerificationStatus
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.agents.verification import VerificationAgent
from app.services.agent.audit_trace import AuditTraceManager
from app.services.agent.core.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_llm_circuit,
)
from app.services.agent.core.rate_limiter import get_llm_rate_limiter
from app.services.agent.tools.base import ToolResult


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


# ---------- 夹具 ----------

def _make_trace(tmp_path):
    return AuditTraceManager(
        task_id="tracewp001",
        project_name="demo",
        base_dir=str(tmp_path / "traces"),
    )


def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    return e


def _done_stream(tokens=128, finish_reason="stop", prompt=100, completion=28):
    async def _gen(messages=None, temperature=None, max_tokens=None, tools=None, response_format=None):
        yield {
            "type": "token", "kind": "content", "content": "答",
            "accumulated": "答", "accumulated_content": "答", "accumulated_reasoning": "",
        }
        yield {
            "type": "done", "content": "答案", "reasoning": "",
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": tokens,
            },
            "finish_reason": finish_reason,
        }
    return _gen


def _make_recon(stream_fn=None):
    service = MagicMock()
    service.get_agent_timeout_config = MagicMock(return_value={
        "llm_first_token_timeout": 30,
        "llm_stream_timeout": 60,
        "agent_timeout": 1800,
        "sub_agent_timeout": 600,
        "tool_timeout": 60,
    })
    service.config.model = "test-model"
    service.config.max_tokens = 8192
    if stream_fn:
        service.chat_completion_stream = stream_fn
    return ReconAgent(llm_service=service, tools={}, event_emitter=_make_emitter())


def _make_verification_agent():
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    agent.emit_event = AsyncMock()
    agent.emit_tool_call = AsyncMock()
    agent.emit_tool_result = AsyncMock()
    agent._timeout_config = {}
    return agent


def _install_dummy_tool(agent, result):
    tool = MagicMock()
    tool.execute = AsyncMock(return_value=result)
    agent.tools["dummy_tool"] = tool
    return tool


# ---------- 1. 工具写点 ----------

def test_execute_tool_records_tool_call(tmp_path):
    """execute_tool 一轮成功调用 → trace 工具栏目 +1，md 统计表非 0。"""
    trace = _make_trace(tmp_path)
    agent = _make_verification_agent()
    agent.trace_manager = trace
    _install_dummy_tool(agent, ToolResult(success=True, data="ok-output", metadata=None))

    result = asyncio.run(agent.execute_tool("dummy_tool", {"q": "hello"}))

    assert "ok-output" in result
    assert trace.stats["tools_called"] == 1
    entry = trace.entries[-1]
    assert entry.type == "tool_call"
    assert entry.content["tool"] == "dummy_tool"
    assert entry.content["success"] is True
    assert entry.content["duration_ms"] >= 0
    trace.finalize()
    md = trace.trace_md.read_text(encoding="utf-8")
    assert "| 工具调用次数 | 1 |" in md
    assert "dummy_tool" in md


def test_execute_tool_failed_call_also_recorded(tmp_path):
    """业务失败（result.success=False）的工具调用同样落 trace。"""
    trace = _make_trace(tmp_path)
    agent = _make_verification_agent()
    agent.trace_manager = trace
    _install_dummy_tool(agent, ToolResult(success=False, error="boom"))

    asyncio.run(agent.execute_tool("dummy_tool", {"q": "hello"}))

    assert trace.stats["tools_called"] == 1
    assert trace.entries[-1].content["success"] is False


def test_execute_tool_exception_recorded(tmp_path):
    """工具执行抛异常（外层 except 分支）也落 trace（success=False），且错误信息照常返回。"""
    trace = _make_trace(tmp_path)
    agent = _make_verification_agent()
    agent.trace_manager = trace
    tool = MagicMock()

    async def _boom(**kwargs):
        raise RuntimeError("tool crashed")

    tool.execute = _boom
    agent.tools["dummy_tool"] = tool

    result = asyncio.run(agent.execute_tool("dummy_tool", {"q": "hello"}))

    assert "工具执行异常" in result
    assert trace.stats["tools_called"] == 1
    assert trace.entries[-1].content["success"] is False
    assert "dummy_tool" in trace.entries[-1].content["tool"]


# ---------- 2. LLM 写点 ----------

def test_stream_llm_call_records_llm_call(tmp_path):
    """stream_llm_call 一轮 → trace LLM/Token 栏目非 0，token 拆分与模型名落 entry。"""
    trace = _make_trace(tmp_path)
    agent = _make_recon(_done_stream(tokens=128, prompt=100, completion=28))
    agent.trace_manager = trace

    content, tokens = asyncio.run(
        agent.stream_llm_call([{"role": "user", "content": "hi"}])
    )

    assert content == "答案"
    assert tokens == 128
    assert trace.stats["llm_calls"] == 1
    assert trace.stats["total_tokens"] == 128
    entry = trace.entries[-1]
    assert entry.type == "llm_call"
    assert entry.content["model"] == "test-model"
    assert entry.content["prompt_tokens"] == 100
    assert entry.content["completion_tokens"] == 28
    assert entry.content["total_tokens"] == 128
    assert entry.content["duration_ms"] >= 0
    assert entry.content["truncated"] is False
    trace.finalize()
    md = trace.trace_md.read_text(encoding="utf-8")
    assert "| LLM 调用次数 | 1 |" in md
    assert "| Token 消耗 | 128 |" in md


def test_stream_llm_call_truncated_flag_recorded(tmp_path):
    """finish_reason=length → entry 标记 truncated=True。"""
    trace = _make_trace(tmp_path)
    agent = _make_recon(_done_stream(tokens=4096, finish_reason="length"))
    agent.trace_manager = trace

    asyncio.run(agent.stream_llm_call([{"role": "user", "content": "hi"}]))

    assert trace.entries[-1].content["truncated"] is True


# ---------- 3. 验证写点 ----------

def test_verification_results_recorded_per_finding(tmp_path):
    """验证收尾：每个 finding 一条验证结果，md 时间线出现通过/失败条目。"""
    trace = _make_trace(tmp_path)
    agent = _make_verification_agent()
    agent.trace_manager = trace
    findings = [
        {
            "id": "f-1", "title": "SQL 注入", "vulnerability_type": "sql_injection",
            "verification_status": VerificationStatus.CONFIRMED,
            "sandbox_attempts": [
                {"exit_code": 0, "evidence_summary": "vuln confirmed", "fabricated": False}
            ],
        },
        {
            "id": "f-2", "title": "XSS", "vulnerability_type": "xss",
            "verification_status": VerificationStatus.NOT_REPRODUCIBLE,
            "sandbox_attempts": [
                {"exit_code": 1, "evidence_summary": "no vuln", "fabricated": False}
            ],
            "verification_note": "PoC 未复现",
        },
    ]

    agent._trace_verification_results(findings)

    md = trace.trace_md.read_text(encoding="utf-8")
    assert "验证通过" in md
    assert "f-1" in md
    assert "验证失败" in md
    assert "f-2" in md
    assert "verification_status=confirmed" in md
    assert "verification_status=not_reproducible" in md


def test_verification_static_confirmed_counts_as_passed(tmp_path):
    """static_confirmed（代码推理确认）在 trace 中记为验证通过，状态原文保留在证据字段。"""
    trace = _make_trace(tmp_path)
    agent = _make_verification_agent()
    agent.trace_manager = trace

    agent._trace_verification_results([
        {
            "id": "f-3", "title": "弱加密", "vulnerability_type": "weak_crypto",
            "verification_status": VerificationStatus.STATIC_CONFIRMED,
            "sandbox_attempts": [],
        }
    ])

    md = trace.trace_md.read_text(encoding="utf-8")
    assert "验证通过" in md
    assert "verification_status=static_confirmed" in md


# ---------- 4. 非致命 ----------

def test_trace_write_failure_never_breaks_main_flow():
    """trace 三个写方法全部抛异常时，工具/LLM/验证主流程结果不受影响。"""
    broken = MagicMock()
    broken.add_tool_call.side_effect = RuntimeError("disk full")
    broken.add_llm_call.side_effect = RuntimeError("disk full")
    broken.add_verification_result.side_effect = RuntimeError("disk full")

    # 工具路径：结果正常返回
    tool_agent = _make_verification_agent()
    tool_agent.trace_manager = broken
    _install_dummy_tool(tool_agent, ToolResult(success=True, data="ok", metadata=None))
    result = asyncio.run(tool_agent.execute_tool("dummy_tool", {}))
    assert "ok" in result
    broken.add_tool_call.assert_called_once()

    # LLM 路径：内容与 token 正常返回
    llm_agent = _make_recon(_done_stream(tokens=55))
    llm_agent.trace_manager = broken
    content, tokens = asyncio.run(
        llm_agent.stream_llm_call([{"role": "user", "content": "hi"}])
    )
    assert content == "答案"
    assert tokens == 55
    broken.add_llm_call.assert_called_once()

    # 验证路径：不抛异常
    tool_agent._trace_verification_results([
        {"id": "f-9", "title": "t", "verification_status": VerificationStatus.CONFIRMED}
    ])
    broken.add_verification_result.assert_called_once()


def test_no_trace_manager_is_noop():
    """trace_manager 为 None（审计追踪关闭）时三个挂点静默跳过。"""
    agent = _make_verification_agent()
    assert agent.trace_manager is None
    _install_dummy_tool(agent, ToolResult(success=True, data="ok", metadata=None))
    result = asyncio.run(agent.execute_tool("dummy_tool", {}))
    assert "ok" in result
    agent._trace_verification_results([{"id": "x", "verification_status": VerificationStatus.CONFIRMED}])

    llm_agent = _make_recon(_done_stream())
    assert llm_agent.trace_manager is None
    content, _ = asyncio.run(llm_agent.stream_llm_call([{"role": "user", "content": "hi"}]))
    assert content == "答案"


# ---------- 5. 写方法被实际调用（调用断言） ----------

def test_write_methods_invoked_with_real_args():
    """mock trace_manager 断言三个写方法在真实执行路径被调用且参数形态正确。"""
    tm = MagicMock()

    tool_agent = _make_verification_agent()
    tool_agent.trace_manager = tm
    _install_dummy_tool(tool_agent, ToolResult(success=True, data="ok", metadata=None))
    asyncio.run(tool_agent.execute_tool("dummy_tool", {"q": "hi"}))
    tm.add_tool_call.assert_called_once()
    tool_kwargs = tm.add_tool_call.call_args.kwargs
    assert tool_kwargs["tool_name"] == "dummy_tool"
    assert tool_kwargs["success"] is True
    assert isinstance(tool_kwargs["duration_ms"], int)
    assert tool_kwargs["input_params"] == {"q": "hi"}

    tm2 = MagicMock()
    llm_agent = _make_recon(_done_stream(tokens=77, prompt=60, completion=17))
    llm_agent.trace_manager = tm2
    asyncio.run(llm_agent.stream_llm_call([{"role": "user", "content": "hi"}]))
    tm2.add_llm_call.assert_called_once()
    llm_kwargs = tm2.add_llm_call.call_args.kwargs
    assert llm_kwargs["model"] == "test-model"
    assert llm_kwargs["prompt_tokens"] == 60
    assert llm_kwargs["completion_tokens"] == 17
    assert llm_kwargs["truncated"] is False
    assert "Verification" in llm_kwargs["purpose"] or "Recon" in llm_kwargs["purpose"]

    tm3 = MagicMock()
    ver_agent = _make_verification_agent()
    ver_agent.trace_manager = tm3
    ver_agent._trace_verification_results([
        {"id": "f-1", "title": "SQLi", "verification_status": VerificationStatus.CONFIRMED,
         "sandbox_attempts": [{"exit_code": 0, "evidence_summary": "pwned"}]}
    ])
    tm3.add_verification_result.assert_called_once()
    ver_kwargs = tm3.add_verification_result.call_args.kwargs
    assert ver_kwargs["finding_id"] == "f-1"
    assert ver_kwargs["verified"] is True
    assert "pwned" in (ver_kwargs["sandbox_output"] or "")


# ---------- 6. Orchestrator 注入 ----------

def test_orchestrator_injects_trace_manager_to_subagents(tmp_path, monkeypatch):
    """OrchestratorAgent 创建 trace_manager 后注入构造时子 Agent 与后注册子 Agent。"""
    from app.services.agent.agents.orchestrator import OrchestratorAgent

    monkeypatch.setattr(settings, "AUDIT_TRACE_DIR", str(tmp_path / "orch"))
    sub = SimpleNamespace(trace_manager=None)

    agent = OrchestratorAgent(
        llm_service=SimpleNamespace(),
        tools={},
        sub_agents={"recon": sub},
        task_id="injtask01",
    )

    assert agent.trace_manager is not None
    assert sub.trace_manager is agent.trace_manager

    late = SimpleNamespace(trace_manager=None)
    agent.register_sub_agent("late", late)
    assert late.trace_manager is agent.trace_manager
