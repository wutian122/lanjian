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
from app.services.agent.agents.base import AgentResult


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
