from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.agents.orchestrator import AgentExecutionPaused, OrchestratorAgent


def _make_agent(monkeypatch) -> OrchestratorAgent:
    agent = OrchestratorAgent(llm_service=SimpleNamespace(), tools={})
    monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
    monkeypatch.setattr(
        agent,
        "_run_semgrep_prescan",
        AsyncMock(return_value={"findings": [], "hot_files": [], "scan_success": False}),
    )
    monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "emit_event", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_decision", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_thought", AsyncMock())
    monkeypatch.setattr(agent, "check_messages", lambda: [])
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    return agent


def test_load_resume_state_initializes_missing_semgrep_state(monkeypatch):
    agent = _make_agent(monkeypatch)

    start_iteration = agent.load_resume_state({
        "iteration_index": 2,
        "conversation_history": [],
        "steps": [],
        "all_findings": [],
        "agent_results": {},
        "dispatched_tasks": {},
    })

    assert start_iteration == 2
    assert agent._semgrep_findings == []
    assert agent._semgrep_hot_files == []


def test_export_resume_state_includes_semgrep_state(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._semgrep_findings = [{"path": "app.py", "check_id": "x"}]
    agent._semgrep_hot_files = ["app.py"]

    state = agent.export_resume_state()

    assert state["semgrep_findings"] == [{"path": "app.py", "check_id": "x"}]
    assert state["semgrep_hot_files"] == ["app.py"]


@pytest.mark.asyncio
async def test_api_circuit_open_pauses_without_counting_as_format_error(monkeypatch):
    agent = _make_agent(monkeypatch)
    monkeypatch.setattr(
        agent,
        "stream_llm_call",
        AsyncMock(return_value=("[API_ERROR:circuit_open] breaker open", 0)),
    )

    pause_mock = AsyncMock(
        side_effect=AgentExecutionPaused(
            checkpoint_id="ckpt-circuit",
            reason="llm_error",
            error_code="circuit_open",
        )
    )
    monkeypatch.setattr(agent, "_pause_for_recoverable_error", pause_mock)

    with pytest.raises(AgentExecutionPaused) as exc:
        await agent.run({"project_info": {}, "config": {}, "task_id": "task-1"})

    pause_mock.assert_awaited_once()
    assert pause_mock.await_args.kwargs["reason"] == "llm_error"
    assert pause_mock.await_args.kwargs["error_code"] == "circuit_open"
    assert "熔断" in pause_mock.await_args.kwargs["user_message"]
    assert getattr(agent, "_format_retry_count", 0) == 0
    assert exc.value.checkpoint_id == "ckpt-circuit"
    assert exc.value.reason == "llm_error"
    assert exc.value.error_code == "circuit_open"


@pytest.mark.asyncio
async def test_format_error_threshold_pauses_task(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._format_retry_count = 4
    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(return_value=("not-parseable", 0)))
    monkeypatch.setattr(agent, "_parse_llm_response", lambda _: None)

    pause_mock = AsyncMock(
        side_effect=AgentExecutionPaused(
            checkpoint_id="ckpt-format",
            reason="format_error",
            error_code="format_error",
        )
    )
    monkeypatch.setattr(agent, "_pause_for_recoverable_error", pause_mock)

    with pytest.raises(AgentExecutionPaused) as exc:
        await agent.run({"project_info": {}, "config": {}, "task_id": "task-2"})

    pause_mock.assert_awaited_once()
    assert pause_mock.await_args.kwargs["reason"] == "format_error"
    assert pause_mock.await_args.kwargs["error_code"] == "format_error"
    # 生产暂停文案为“连续 N 次格式错误，任务已暂停…”，按语义匹配
    assert "连续" in pause_mock.await_args.kwargs["user_message"]
    assert "格式错误" in pause_mock.await_args.kwargs["user_message"]
    assert exc.value.checkpoint_id == "ckpt-format"
    assert exc.value.reason == "format_error"
    assert exc.value.error_code == "format_error"


@pytest.mark.asyncio
async def test_request_pause_forces_checkpoint_on_timeout(monkeypatch):
    """request_pause 超时后必须强制落 checkpoint 并返回，不抛 TimeoutError。"""
    import asyncio

    agent = _make_agent(monkeypatch)
    # 模拟超时：_pause_future 永不完成，wait_for 立即超时
    agent._pause_task_id = "task-timeout"
    agent._pause_db_session_factory = lambda: None

    flush_mock = AsyncMock(return_value="ckpt-forced")
    monkeypatch.setattr(agent, "_flush_pause_checkpoint", flush_mock)

    # 用极短 timeout 触发超时分支
    checkpoint_id = await agent.request_pause(
        task_id="task-timeout",
        db_session_factory=agent._pause_db_session_factory,
        timeout_seconds=0.01,
    )

    # 超时后强制落 checkpoint，返回 checkpoint_id，不抛异常
    assert checkpoint_id == "ckpt-forced"
    flush_mock.assert_awaited()
    assert agent._pause_requested is False
