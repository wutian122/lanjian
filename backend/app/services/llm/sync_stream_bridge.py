"""
同步流式 LLM 调用的线程桥接（F2 块 1：事件循环冻结根治）

根因（生产实证：任务 c286b0c1 卡 10h04m，watchdog/三层超时全部失效）：
litellm 1.80.10 的流式封装 ``CustomStreamWrapper.__anext__`` 在
sync-iterable 分支用同步 ``next(self.completion_stream)`` 读 socket
（litellm_core_utils/streaming_handler.py else 分支，注释原文
"temporary patch for non-aiohttp async calls"），整个调用无 await 点。
uvicorn 单 worker（--workers 1）下事件循环线程被这个同步读整体冻结：
``asyncio.sleep``（watchdog 时钟）、``asyncio.wait_for``（per-chunk/
适配器超时）全部失去调度机会，任务一直卡到上游 TCP 断连。

根治方案（A1 线程池剥离）：同步 ``litellm.completion(stream=True)`` 全程
（建连 + 迭代读 socket）在专用 daemon 工作线程执行，产出的每个 chunk 经
``asyncio.Queue``（``loop.call_soon_threadsafe`` 投递）交给事件循环上的
async 消费端。事件循环线程不再出现任何 LLM 网络读，watchdog 与各层
wait_for 的调度不再依赖 LLM 行为。

取消语义：
- 消费端被取消（watchdog cancel / per-chunk 超时 aclose / 正常耗尽）时，
  async generator 的 finally 置 ``threading.Event``；工作线程在**每个
  chunk 边界**检查该事件并退出，finally 关闭同步流（释放连接）；
- 工作线程若正阻塞在同步 socket read 中无法立即检查事件——该 read 由
  kwargs timeout（默认 150s，httpx 同步超时在**线程内**正常生效）兜底，
  read 返回/超时后线程见到事件已置位立即退出；
- 工作线程为 daemon 线程，即便卡在 read 也不阻止进程退出；
- 队列投递用 ``call_soon_threadsafe(put_nowait)``：工作线程永不阻塞在
  事件循环上；队列不加界——token chunk 速率远低于消费速率，取消后最多
  残留"下一个已返回 chunk"，随 generator 一并回收。

非流式路径（litellm.acompletion 非 stream、_native_openai_call 的
openai.AsyncOpenAI、NATIVE_ONLY 适配器的 httpx.AsyncClient）均为真
async httpx 调用，不存在同步读事件循环问题，不走本桥接。
"""

import asyncio
import logging
import threading
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)


class _StreamError:
    """工作线程异常的投递载体（跨线程保留原始异常对象与类型）。"""

    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


_SENTINEL = object()


def _close_sync_stream(stream: Any) -> None:
    """尽力关闭同步流（工作线程 finally 中调用，同线程无并发）。

    litellm 1.80 的 CustomStreamWrapper 本身无 close()；其内部
    ``completion_stream``（litellm make_call 产出的同步 generator /
    openai Stream）有 close()——generator.close() 触发其 finally
    关闭 httpx 响应。任何关闭失败均非致命（连接随客户端 GC 回收）。
    """
    for target in (stream, getattr(stream, "completion_stream", None)):
        close = getattr(target, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("同步 LLM 流 close 失败（非致命）", exc_info=True)


async def iter_sync_stream(
    sync_stream_factory: Callable[[], Any],
    *,
    stream_name: str = "llm-sync-stream",
) -> AsyncIterator[Any]:
    """把同步可迭代流桥接为 async 迭代器（同步调用全程在工作线程）。

    Args:
        sync_stream_factory: 零参可调用，**在工作线程内**调用并返回一个
            同步可迭代对象（litellm.completion(stream=True) 的返回值）。
            建连阶段同样发生在工作线程内（工厂调用本身可能同步阻塞）。
        stream_name: 工作线程名（诊断用，出现在日志/线程转储中）。

    Yields:
        同步迭代产出的原始 item（litellm ModelResponse chunk），由调用方
        按既有逻辑解析。

    Raises:
        工厂/迭代抛出的原始异常（保留类型，供调用方 except 分类——
        litellm.exceptions.RateLimitError 等跨线程投递后类型不变）。
    """
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[Any]" = asyncio.Queue()
    stop_event = threading.Event()

    def _post(item: Any) -> None:
        """跨线程投递到事件循环；loop 已关闭（进程退出）时静默丢弃。"""
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass

    def _worker() -> None:
        stream: Any = None
        try:
            stream = sync_stream_factory()
            for item in stream:
                if stop_event.is_set():
                    break
                _post(item)
        except Exception as exc:
            # 原始异常对象跨线程投递，消费端原样重抛（类型保持）
            _post(_StreamError(exc))
        finally:
            if stream is not None:
                _close_sync_stream(stream)
            _post(_SENTINEL)

    thread = threading.Thread(target=_worker, name=stream_name, daemon=True)
    thread.start()

    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                return
            if isinstance(item, _StreamError):
                raise item.exc
            yield item
    finally:
        # 消费端取消/超时关闭/正常耗尽：通知工作线程停止投递并关闭流。
        # 阻塞在同步 read 中的线程由 kwargs timeout（线程内 httpx 超时）
        # 兜底返回后退出；daemon 线程不阻止进程退出。
        stop_event.set()
