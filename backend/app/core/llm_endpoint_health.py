"""P9 稳定性簇（2026-10-08）：LLM 端点软熔断，按 base_url 滑动窗口记账。

生产实证：四任务并发共用单卡 SGLang，急性过载窗口空响应率 100%，
既有熔断器对空响应零感知（空响应被记为成功）→ 永不打开 → 各任务
独立烧 5 轮（含 15/30s 退避），百秒止损、覆盖不足。

设计（方案簇 C / 裁决 D3，降速 + 30s 有序收口）：

- ``record(endpoint, empty)``：按端点 base_url 维护固定容量滑窗，
  deque[(ts, empty)]（窗口 20 / min_samples 10 / 空率阈值 0.5）；
- ``is_degraded``：样本足够且空率 ≥ 阈值；
- ``maybe_throttle``：degraded 时 sleep LLM_ENDPOINT_THROTTLE_SECONDS
  全局降速（sleep 可注入）；
- ``backoff_factor``：空率 0→1.0、0.5→3.0、1.0→4.0，放大救援退避；
- ``should_orderly_stop``：degraded 持续超 30s → True（仅 Orchestrator
  主循环顶消费），冷却 120s 内不重复；
- 记账修正：无物理调用帧（既有熔断 OPEN / critical stream error）
  不入窗——base.py 在这两支置 ``_last_empty_kind="no_physical_call"``，
  ``_record_endpoint_outcome`` 跳过 record；
- 单收费点：嵌套 _last_ditch 救援帧经 ``rescue_frame()`` contextvar
  标记免收 throttle，防 5s 降速与救援退避自激励叠加。

参数经 ``app.core.config.settings`` 的 LLM_ENDPOINT_* 注入（运行时
读取，测试改配置即时生效）。
"""
import asyncio
import contextvars
import time
from collections import deque
from contextlib import contextmanager
from typing import Deque, Dict, List, Tuple

# 嵌套救援帧标记：True 时 maybe_throttle 免收（contextvar 随调用链传播，
# 救援帧内的任何嵌套调用同样免收）。
_rescue_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "llm_endpoint_rescue_frame", default=False
)


@contextmanager
def rescue_frame():
    """标记 _last_ditch 救援帧：帧内 throttle 单收费点免收。"""
    token = _rescue_context.set(True)
    try:
        yield
    finally:
        _rescue_context.reset(token)


def _settings_value(name: str, default):
    """运行时读取 settings（惰性导入，避免模块级循环依赖）。"""
    try:
        from app.core.config import settings

        return getattr(settings, name, default)
    except Exception:
        return default


class EndpointHealth:
    """按端点（base_url）的空响应软熔断状态。"""

    def __init__(self) -> None:
        # endpoint -> deque[(ts, empty)]，maxlen 在建窗时按配置确定
        self._windows: Dict[str, Deque[Tuple[float, bool]]] = {}
        # endpoint → degraded 起始时刻（状态恢复即清空）
        self._degraded_since: Dict[str, float] = {}
        # endpoint → 上次有序收口时刻（冷却去重）
        self._stop_at: Dict[str, float] = {}
        # 可注入的 sleep（测试用假 sleep，杜绝真实 5s 等待）
        self._sleep = asyncio.sleep
        # 可注入的单调钟
        self._time = time.monotonic

    # ---- 窗口与空率 ----

    def _window_size(self) -> int:
        return max(2, int(_settings_value("LLM_ENDPOINT_WINDOW_SIZE", 20)))

    def _min_samples(self) -> int:
        n = int(_settings_value("LLM_ENDPOINT_MIN_SAMPLES", 10))
        return max(1, min(n, self._window_size()))

    def _empty_rate_threshold(self) -> float:
        v = float(_settings_value("LLM_ENDPOINT_EMPTY_RATE", 0.5))
        return min(1.0, max(0.0, v))

    def _get_window(self, endpoint: str) -> Deque[Tuple[float, bool]]:
        window = self._windows.get(endpoint)
        if window is None:
            window = deque(maxlen=self._window_size())
            self._windows[endpoint] = window
        return window

    def _empty_rate(self, endpoint: str):
        """返回当前空率；样本不足返回 None（不判定）。"""
        window = self._windows.get(endpoint)
        if not window or len(window) < self._min_samples():
            return None
        empties = sum(1 for _ts, empty in window if empty)
        return empties / len(window)

    # ---- 记账 ----

    def record(self, endpoint: str, empty: bool) -> None:
        """记录一帧物理调用的结果（empty=空响应/degenerate）。"""
        self._get_window(endpoint).append((self._time(), bool(empty)))

    # ---- degraded ----

    def is_degraded(self, endpoint: str) -> bool:
        now = self._time()
        rate = self._empty_rate(endpoint)
        degraded = rate is not None and rate >= self._empty_rate_threshold()
        if degraded:
            if endpoint not in self._degraded_since:
                self._degraded_since[endpoint] = now
        else:
            self._degraded_since.pop(endpoint, None)
        return degraded

    # ---- 退避放大（三档手推值）----

    def backoff_factor(self, endpoint: str) -> float:
        """空率 0→1.0、0.5→3.0、1.0→4.0（样本不足按健康口径 1.0）。"""
        rate = self._empty_rate(endpoint)
        if rate is None or rate <= 0:
            return 1.0
        if rate >= 1.0:
            return 4.0
        if rate >= self._empty_rate_threshold():
            return 3.0
        return 1.0

    # ---- 降速 ----

    async def maybe_throttle(self, endpoint: str) -> float:
        """degraded 时 sleep 降速，返回实际等待秒数（救援帧免收）。"""
        if _rescue_context.get():
            return 0.0
        if not self.is_degraded(endpoint):
            return 0.0
        seconds = float(_settings_value("LLM_ENDPOINT_THROTTLE_SECONDS", 5))
        await self._sleep(seconds)
        return seconds

    # ---- 有序收口 ----

    def should_orderly_stop(self, endpoint: str) -> bool:
        """degraded 持续超 LLM_ENDPOINT_DEGRADED_SECONDS → True（冷却内不重复）。"""
        now = self._time()
        if not self.is_degraded(endpoint):
            return False
        since = self._degraded_since.get(endpoint)
        if since is None:
            return False
        duration = float(_settings_value("LLM_ENDPOINT_DEGRADED_SECONDS", 30))
        if now - since < duration:
            return False
        last_stop = self._stop_at.get(endpoint)
        if last_stop is not None:
            cooldown = float(_settings_value("LLM_ENDPOINT_COOLDOWN_SECONDS", 120))
            if now - last_stop < cooldown:
                return False
        self._stop_at[endpoint] = now
        return True

    # ---- 重置 ----

    def reset(self) -> None:
        self._windows.clear()
        self._degraded_since.clear()
        self._stop_at.clear()


# 模块级单例（单 worker 下即进程全局）。
_health = EndpointHealth()


def get_endpoint_health() -> EndpointHealth:
    return _health


def reset_endpoint_health() -> None:
    """测试辅助：重建单例（每个用例前经 conftest autouse fixture 调用）。"""
    global _health
    _health = EndpointHealth()
