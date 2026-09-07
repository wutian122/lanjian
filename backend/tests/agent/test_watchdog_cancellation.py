"""
F2 块 2/块 3：任务 watchdog 强杀补丁与 per-chunk 取消坑防堵测试

块 2（agent_tasks._run_orchestrator_with_budget_watchdog）：
- ① mark_deadline_hit() 同时立即 run_task.cancel()：CancelledError 注入当前
  await 点（不等 wait_for 到 task_timeout+grace），生产中卡在等下一个 chunk
  的 LLM 流被立即打断；orchestrator 主循环/子 agent 调度捕获 CancelledError
  走优雅收口（break→finalize，保发现）。
- ② 宽限耗尽 hard-cancel 后的 await run_task 二次时限：内层吞取消时旧代码
  永久挂死；现在用 asyncio.wait 限定收口时间，超时落盘卡住栈后继续兜底
  （不能用 wait_for——其内部 _cancel_and_wait 同样永久等待）。
- ① 的语义边界：用户取消（is_task_cancelled）/自身被取消时 CancelledError
  必须传播（外层标 CANCELLED）；deadline 注入取消未被优雅消费才走超时兜底。

块 3（BaseAgent.stream_llm_call._consume）：
- Py3.12 wait_for 取消坑：内层 async-generator 吞掉 CancelledError 时
  TimeoutError 丢失（实测：吞后 return → wait_for 抛 StopAsyncIteration
  被误判正常结束；吞后继续 → wait_for 返回迟到 chunk）。消费端用
  shield(fetch) 保证超时控制权必然回来，再 cancel fetch + 显式 aclose
  强制生成器收口（线程桥 stop_event 随之置位）。
- 任何退出路径（正常 done/超时/外部取消）都 aclose iterator，无悬挂流。

全部本地 fake，不触真 LLM/Redis。
"""

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import agent_tasks
from app.api.v1.endpoints.agent_tasks import _run_orchestrator_with_budget_watchdog
from app.core.config import settings
from app.services.agent.agents import base as agent_base_module
from app.services.agent.agents.base import AgentResult
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.core.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_llm_circuit,
)
from app.services.agent.core.rate_limiter import get_llm_rate_limiter


# ---------------------------------------------------------------------------
# 块 2：watchdog 取消语义
# ---------------------------------------------------------------------------


def _make_orch(findings=None):
    return SimpleNamespace(
        mark_deadline_hit=MagicMock(),
        _all_findings=findings or [],
        _iteration=1,
        _tool_calls=2,
        _total_tokens=100,
        _sub_agent_total_tokens=0,
    )


def _make_emitter():
    return SimpleNamespace(emit_warning=AsyncMock())


def _watchdog(run_coro, orch, emitter, task_timeout, task_id="t-wd-f2"):
    run_task = asyncio.create_task(run_coro)
    return run_task, _run_orchestrator_with_budget_watchdog(
        orch, run_task, task_timeout=task_timeout,
        event_emitter=emitter, task_id=task_id, task_started_at=time.time(),
    )


class TestDeadlineImmediateCancel:
    @pytest.mark.asyncio
    async def test_deadline_cancels_run_task_without_waiting_for_grace(
        self, monkeypatch
    ):
        """补丁①：task_timeout 到点 run_task 立即被 cancel（不等到 grace 耗尽）。
        run 未优雅消费取消而死亡 → 按 COMPLETED_WITH_GAPS 收口。"""
        monkeypatch.setattr(settings, "TIME_BUDGET_GRACE_SECONDS", 30)

        async def _run():
            await asyncio.sleep(100)
            return AgentResult(success=True, data={"findings": []})

        orch = _make_orch(findings=[{"title": "kept"}])
        t0 = time.monotonic()
        run_task, coro = _watchdog(_run(), orch, _make_emitter(), task_timeout=0.3)
        result, hit = await coro
        elapsed = time.monotonic() - t0

        assert hit is True
        orch.mark_deadline_hit.assert_called_once()
        assert run_task.done() and run_task.cancelled()
        # 必须在 task_timeout 后立即收口，绝不等 grace(30s)
        assert elapsed < 5.0, f"等了 {elapsed:.1f}s，疑似仍在等 grace"
        assert result.success is True
        assert result.metadata["coverage_info"]["reason"] == "task_timeout"
        assert result.data["findings"] == [{"title": "kept"}]

    @pytest.mark.asyncio
    async def test_graceful_cancel_consumption_returns_orchestrator_result(
        self, monkeypatch
    ):
        """补丁①后：orchestrator 风格的 run（捕获 CancelledError → break →
        优雅返回结果）在 deadline 取消后正常收口，结果即 run 的返回值。"""
        monkeypatch.setattr(settings, "TIME_BUDGET_GRACE_SECONDS", 30)

        async def _run():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                # 模拟 orchestrator 主循环：取消后 break → finalize 优雅返回
                return AgentResult(
                    success=True, data={"findings": [{"title": "graceful"}]}, metadata={}
                )
            return AgentResult(success=True, data={"findings": []})

        orch = _make_orch()
        t0 = time.monotonic()
        run_task, coro = _watchdog(_run(), orch, _make_emitter(), task_timeout=0.3)
        result, hit = await coro
        elapsed = time.monotonic() - t0

        assert hit is True
        assert elapsed < 5.0
        assert result.success is True
        assert result.data["findings"] == [{"title": "graceful"}]
        # 优雅返回后由 deadline 元数据注入链补 coverage_bypassed
        assert result.metadata["coverage_bypassed"] is True
        assert result.metadata["coverage_info"]["reason"] == "task_timeout"


class TestHardCancelSecondDeadline:
    @pytest.mark.asyncio
    async def test_swallowed_cancel_settles_at_second_deadline(self, monkeypatch):
        """补丁②：run 吞掉 CancelledError 永久循环 → grace 耗尽 hard-cancel 后
        asyncio.wait 二次时限到点即收口落盘，绝不永久挂死。"""
        monkeypatch.setattr(settings, "TIME_BUDGET_GRACE_SECONDS", 1)
        monkeypatch.setattr(agent_tasks, "_HARD_CANCEL_SETTLE_SECONDS", 0.5)

        cancel_count = 0
        release = asyncio.Event()  # 测试末尾放行，让吞取消协程能收口

        async def _run():
            nonlocal cancel_count
            while not release.is_set():
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    cancel_count += 1
                    continue
            return AgentResult(success=True, data={"findings": []})

        orch = _make_orch(findings=[{"title": "x"}])
        emitter = _make_emitter()
        t0 = time.monotonic()
        run_task = asyncio.create_task(_run())
        result, hit = await _run_orchestrator_with_budget_watchdog(
            orch, run_task, task_timeout=0.2, event_emitter=emitter,
            task_id="t-wd-f2-hang", task_started_at=time.time(),
        )
        elapsed = time.monotonic() - t0

        assert hit is True
        # 0.2(deadline) + 1(grace) + 0.5(二次时限) ≈ 1.7s；放宽到 5s 防 CI 抖动
        assert elapsed < 5.0, f"二次时限未生效，等了 {elapsed:.1f}s"
        assert result.success is True
        assert result.metadata["coverage_info"]["reason"] == "task_timeout"
        assert result.data["findings"] == [{"title": "x"}]
        emitter.emit_warning.assert_awaited()
        # 吞取消的任务在二次时限后仍被放弃等待（语义是落盘后继续兜底，不是杀死任务）
        assert not run_task.done(), "二次时限语义是放弃等待而非杀死任务"
        assert cancel_count >= 2, f"deadline 与 hard 两次 cancel 都应注入，实际 {cancel_count}"
        # 测试末尾放行并 cancel 唤醒，让协程正常收口，避免悬挂任务
        release.set()
        run_task.cancel()
        done, _pending = await asyncio.wait({run_task}, timeout=2.0)
        assert done, "测试清理：放行后协程应能收口"

    @pytest.mark.asyncio
    async def test_stack_logged_when_task_stuck(self, monkeypatch, caplog):
        """补丁②：二次超时必须落盘卡住点（task.get_stack），可诊断。"""
        monkeypatch.setattr(settings, "TIME_BUDGET_GRACE_SECONDS", 1)
        monkeypatch.setattr(agent_tasks, "_HARD_CANCEL_SETTLE_SECONDS", 0.3)

        release = asyncio.Event()

        async def _run():
            while not release.is_set():
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    # 吞取消后继续等另一个 sleep（不 re-raise）
                    continue
            return AgentResult(success=True, data={"findings": []})

        orch = _make_orch()
        run_task = asyncio.create_task(_run())
        try:
            with caplog.at_level(
                logging.ERROR, logger="app.api.v1.endpoints.agent_tasks"
            ):
                await _run_orchestrator_with_budget_watchdog(
                    orch, run_task, task_timeout=0.1,
                    event_emitter=_make_emitter(), task_id="t-wd-f2-stack",
                    task_started_at=time.time(),
                )
        finally:
            release.set()
            run_task.cancel()
            # 用 asyncio.wait 收口（wait_for 对吞取消任务同样挂死）
            await asyncio.wait({run_task}, timeout=2.0)

        error_logs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("did not settle" in r.getMessage() for r in error_logs), (
            "二次超时必须落盘卡住点日志"
        )
        # 日志应含调用栈片段（卡在 asyncio.sleep）
        assert any("sleep" in r.getMessage() for r in error_logs), (
            "卡住点栈应显示具体挂起位置"
        )


class TestWatchdogCancelSemanticsBoundary:
    @pytest.mark.asyncio
    async def test_user_cancel_propagates_as_cancelled_error(self, monkeypatch):
        """用户取消（is_task_cancelled=True）导致 run_task 死亡时，
        CancelledError 必须穿透 watchdog 传播（外层据此标 CANCELLED）。"""
        monkeypatch.setattr(agent_tasks, "is_task_cancelled", lambda task_id: True)

        async def _run():
            await asyncio.sleep(100)

        run_task = asyncio.create_task(_run())
        # 用户取消：标志已置 + run_task 被外部 cancel
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _run_orchestrator_with_budget_watchdog(
                _make_orch(), run_task, task_timeout=100,
                event_emitter=_make_emitter(), task_id="t-wd-f2-user",
                task_started_at=time.time(),
            )

    @pytest.mark.asyncio
    async def test_normal_completion_not_cancelled(self):
        """预算内完成：run_task 不被取消、mark_deadline_hit 不被调用。"""
        async def _run():
            return AgentResult(success=True, data={"findings": []})

        orch = _make_orch()
        emitter = _make_emitter()
        run_task, coro = _watchdog(_run(), orch, emitter, task_timeout=10)
        result, hit = await coro

        assert hit is False
        orch.mark_deadline_hit.assert_not_called()
        assert not run_task.cancelled()
        emitter.emit_warning.assert_not_awaited()


# ---------------------------------------------------------------------------
# 块 3：per-chunk 取消坑防堵（BaseAgent.stream_llm_call._consume）
# ---------------------------------------------------------------------------


class _TrackingStream:
    """包装 async generator 的假流：记录 aclose 调用（_consume 必须显式关闭）。"""

    def __init__(self, gen):
        self._gen = gen
        self.aclosed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._gen.__anext__()

    async def aclose(self):
        # __anext__ 在飞时底层 gen.aclose() 会同步抛 RuntimeError
        # （"async generator is already running"），原样抛出供上层验证
        await self._gen.aclose()
        self.aclosed = True


def _reset_resilience():
    c = get_llm_circuit()
    c._state = CircuitState.CLOSED
    c._stats = CircuitStats()
    c._half_open_calls = 0
    lim = get_llm_rate_limiter()
    lim.tokens = float(lim.burst)
    lim.last_update = time.monotonic()


@pytest.fixture(autouse=True)
def _reset_circuit():
    _reset_resilience()
    yield
    _reset_resilience()


def _make_streaming_agent(tracker, first_token_timeout=0.5):
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    emitter.emit_thinking_start = AsyncMock()
    emitter.emit_thinking_token = AsyncMock()
    emitter.emit_content_token = AsyncMock()
    emitter.emit_thinking_end = AsyncMock()
    emitter.emit_content_end = AsyncMock()
    service = MagicMock()
    service.get_agent_timeout_config = MagicMock(return_value={
        "llm_first_token_timeout": first_token_timeout,
        "llm_stream_timeout": 60,
        "agent_timeout": 1800,
        "sub_agent_timeout": 600,
        "tool_timeout": 60,
    })
    service.config.max_tokens = 8192
    service.chat_completion_stream = MagicMock(return_value=tracker)
    return ReconAgent(llm_service=service, tools={}, event_emitter=emitter), emitter


def _hang_then_done_gen():
    """第一个 chunk 永远不到达（模拟 LLM 卡住），取消语义正常（不吞取消）。"""

    async def _gen():
        await asyncio.sleep(9999)
        yield {"type": "done", "content": "late"}

    return _gen()


def _swallow_cancel_gen():
    """取消坑模拟：__anext__ 内吞掉 CancelledError 后 return（生成器关闭）。

    实测 Py3.12：wait_for 直接包裹时超时取消被吞，TimeoutError 折成
    StopAsyncIteration（误判正常结束），超时防护静默失效。
    """

    async def _gen():
        try:
            await asyncio.sleep(9999)
            yield {"type": "token", "content": "late"}
        except asyncio.CancelledError:
            return

    return _gen()


@pytest.mark.asyncio
async def test_per_chunk_timeout_accloses_iterator(monkeypatch):
    """块 3①：per-chunk 超时后必须显式 aclose in-flight 迭代器（无悬挂流，
    线程桥 stop_event 随之置位），且超时错误照常发射。"""
    monkeypatch.setattr(agent_base_module, "_ST_FETCH_CANCEL_SETTLE_SECONDS", 1.0)
    monkeypatch.setattr(agent_base_module, "_ST_ACLOSE_TIMEOUT_SECONDS", 1.0)

    tracker = _TrackingStream(_hang_then_done_gen())
    agent, emitter = _make_streaming_agent(tracker)

    t0 = time.monotonic()
    content, _tokens = await agent.stream_llm_call(agent._conversation_history)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"per-chunk 超时未收口，等了 {elapsed:.1f}s"
    assert tracker.aclosed, "per-chunk 超时后必须 aclose 迭代器"
    assert "超时" in content, f"超时错误文案应返回给调用方，实际：{content!r}"
    error_calls = [
        c for c in emitter.emit.await_args_list
        if c.args and getattr(c.args[0], "event_type", None) == "error"
    ]
    assert error_calls, "超时必须发射 error 事件"


@pytest.mark.asyncio
async def test_swallowed_cancel_timeout_still_enforced(monkeypatch):
    """块 3②：生成器吞掉超时取消（Py3.12 wait_for 坑）时，超时防护仍生效：
    shield 保证 TimeoutError 回到消费端，error 事件照常、迭代器显式关闭。"""
    monkeypatch.setattr(agent_base_module, "_ST_FETCH_CANCEL_SETTLE_SECONDS", 1.0)
    monkeypatch.setattr(agent_base_module, "_ST_ACLOSE_TIMEOUT_SECONDS", 1.0)

    tracker = _TrackingStream(_swallow_cancel_gen())
    agent, emitter = _make_streaming_agent(tracker)

    t0 = time.monotonic()
    content, _tokens = await agent.stream_llm_call(agent._conversation_history)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"吞取消后超时防护失效，等了 {elapsed:.1f}s"
    assert tracker.aclosed, "吞取消场景仍必须 aclose 迭代器"
    assert "超时" in content, f"超时文案应生效（旧代码静默结束返回空串）：{content!r}"
    error_calls = [
        c for c in emitter.emit.await_args_list
        if c.args and getattr(c.args[0], "event_type", None) == "error"
    ]
    assert error_calls, "吞取消场景超时 error 事件不得丢失"


@pytest.mark.asyncio
async def test_external_cancel_accloses_iterator(monkeypatch):
    """块 3③：watchdog 取消（CancelledError 从外部打入）时，finally 必须
    aclose 迭代器（线程桥 stop_event 置位），CancelledError 照常 re-raise。"""
    monkeypatch.setattr(agent_base_module, "_ST_FETCH_CANCEL_SETTLE_SECONDS", 1.0)
    monkeypatch.setattr(agent_base_module, "_ST_ACLOSE_TIMEOUT_SECONDS", 1.0)

    tracker = _TrackingStream(_hang_then_done_gen())
    agent, _emitter = _make_streaming_agent(tracker)

    async def _call():
        return await agent.stream_llm_call(agent._conversation_history)

    call_task = asyncio.create_task(_call())
    # 等流真正进入 in-flight 等待后取消
    await asyncio.sleep(0.3)
    call_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call_task, timeout=5.0)

    assert tracker.aclosed, "外部取消路径必须 aclose 迭代器（线程桥 stop_event）"


@pytest.mark.asyncio
async def test_hard_interrupt_stream_accloses_iterator():
    """补丁③：BaseAgent.hard_interrupt_stream() 直接 aclose 在飞迭代器；
    无在飞迭代器时 no-op 不抛异常；orchestrator._hard_interrupt 路由本任务
    全部 agent 的同一方法（best-effort 不外抛）。"""
    tracker = _TrackingStream(_swallow_cancel_gen())
    agent, _emitter = _make_streaming_agent(tracker)

    # 无在飞迭代器：no-op
    await agent.hard_interrupt_stream()

    # 模拟 _consume 挂上迭代器引用
    agent._stream_iter = tracker

    async def _call():
        return await agent.stream_llm_call(agent._conversation_history)

    call_task = asyncio.create_task(_call())
    await asyncio.sleep(0.3)
    # watchdog 顺序：先 cancel run_task（在飞 fetch 收到取消），
    # 再 _hard_interrupt 关流
    call_task.cancel()
    await asyncio.wait({call_task}, timeout=2.0)
    await agent.hard_interrupt_stream()

    assert tracker.aclosed, "hard_interrupt_stream 必须 aclose 在飞迭代器"
    if not call_task.done():
        call_task.cancel()

    # orchestrator 路由：_runtime_context 无 task_id 时只关自身，不抛异常
    orch = OrchestratorAgent(llm_service=SimpleNamespace(), tools={})
    orch._stream_iter = None
    await orch._hard_interrupt()

