"""P5-2（2026-10-04）：跨任务全局 LLM 并发闸。

生产实证（10-06 四任务并发，任务 1fe2d9ce）：四任务各 3 路并发 = 12 路
同时打单卡 SGLang → 过载 abort → 部分请求空返回（usage 全 0）→ 连续
止损 → 编排提前收口。

单 worker 架构下进程内 asyncio.Semaphore 即全局（多 worker 前先解决
编排器内存态问题，见 AGENTS.md 铁律）。闸的粒度 = 单次 HTTP 出站调用
（流式调用全程持有）——这正是 SGLang 并发槽的真实占用。
超出的请求在本进程内排队等待，而非发给服务端被 abort。
"""
import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class GlobalLLMGate:
    """全局 LLM 并发闸（进程内单例语义，经 get_global_gate() 获取）。"""

    def __init__(self) -> None:
        self._sem: asyncio.Semaphore | None = None
        self._built_limit: int = 0
        self._active = 0
        self._waiting = 0
        self._warned_wait_ms = 0

    def _get_sem(self) -> asyncio.Semaphore:
        from app.core.config import settings

        limit = max(1, int(getattr(settings, "LLM_GLOBAL_CONCURRENCY", 6)))
        if self._sem is None or self._built_limit != limit:
            self._sem = asyncio.Semaphore(limit)
            self._built_limit = limit
            self._active = 0
            self._waiting = 0
        return self._sem

    @asynccontextmanager
    async def slot(self):
        sem = self._get_sem()
        if sem.locked():
            self._waiting += 1
            logger.warning(
                f"[LLM全局闸] 并发已满（{self._built_limit}），请求排队等待 "
                f"(waiting={self._waiting})——防止打爆 LLM 服务"
            )
        try:
            await sem.acquire()
            self._active += 1
            try:
                yield self
            finally:
                self._active -= 1
                self._waiting = max(0, self._waiting - 1)
                sem.release()
        except BaseException:
            if sem.locked() is False:
                pass
            raise

    def observation(self) -> dict:
        return {"active": self._active, "waiting": self._waiting}


_gate = GlobalLLMGate()


def get_global_gate() -> GlobalLLMGate:
    return _gate


def reset_gate() -> None:
    """测试辅助：重建闸（配置变更后）。"""
    global _gate
    _gate = GlobalLLMGate()


@asynccontextmanager
async def llm_gate():
    """模块级入口：async with llm_gate(): 包住单次 LLM HTTP 出站调用。"""
    async with _gate.slot():
        yield _gate
