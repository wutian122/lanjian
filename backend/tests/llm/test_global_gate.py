"""P5-2 跨任务全局 LLM 并发闸（2026-10-04）。

生产实证（10-06 四任务并发）：四任务同时发起，各 3 路并发 = 12 路打单卡
SGLang → 过载 abort → 部分请求空返回（prompt=0/compl=0）→ 连续止损 →
编排提前收口（任务 1fe2d9ce）。

契约：
- 全局闸限并发持有个数 = LLM_GLOBAL_CONCURRENCY（默认 6）；
- 超出者排队等待，释放后按序进入；
- 提供计数观测（active/waiting）供日志；
- reset_gate() 支持配置变更后重建（测试用）。
"""
import asyncio

import pytest

from app.core.config import settings
from app.core.llm_global_gate import GlobalLLMGate, reset_gate


@pytest.fixture(autouse=True)
def _fresh_gate():
    reset_gate()
    yield
    reset_gate()


class TestGlobalGate:
    async def test_limits_concurrent_holders(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "LLM_GLOBAL_CONCURRENCY", 3, raising=False)
            reset_gate()
            gate = GlobalLLMGate()
            active_peak = 0
            active = 0

            async def holder():
                nonlocal active, active_peak
                async with gate.slot():
                    active += 1
                    active_peak = max(active_peak, active)
                    await asyncio.sleep(0.05)
                    active -= 1

            await asyncio.gather(*(holder() for _ in range(8)))
            assert active_peak <= 3, f"峰值 {active_peak} 超过全局并发限制 3"

    async def test_waiters_enter_after_release(self):
        gate = GlobalLLMGate()
        order = []

        async def worker(n):
            async with gate.slot():
                order.append(n)
                await asyncio.sleep(0.02)

        await asyncio.gather(*(worker(i) for i in range(4)))
        assert len(order) == 4

    def test_observation_counters(self):
        gate = GlobalLLMGate()
        assert gate.observation() == {"active": 0, "waiting": 0}
