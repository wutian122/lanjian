"""
F2 块 1：litellm 出站线程池剥离（事件循环冻结根治）测试

根因（生产实证 c286b0c1 卡 10h04m，watchdog/三层超时全失效）：
litellm 1.80.10 流式封装 CustomStreamWrapper.__anext__ 的 sync-iterable
分支用同步 ``next(self.completion_stream)`` 读 socket
（streaming_handler.py else 分支，"temporary patch for non-aiohttp
async calls"），无 await 点——uvicorn 单 worker（--workers 1）事件循环
被整体冻结：asyncio.sleep(watchdog)/wait_for/适配器超时全部失去调度机会。

根治（A1 线程池剥离）：同步 litellm.completion(stream=True) 全程在专用
后台线程执行（建连 + 迭代读 socket），chunk 经 asyncio.Queue
（loop.call_soon_threadsafe 投递）喂给事件循环上的 async 消费端——事件
循环线程不再出现任何 LLM 网络读。消费端取消/超时 → async generator
finally 置 threading.Event，工作线程在下个 chunk 边界退出并在 finally
关闭流；阻塞在同步 read 中的线程由 kwargs timeout（线程内 httpx 同步
超时正常生效）兜底，线程为 daemon，不阻止进程退出。

本文件全部本地 mock，不触真 LLM。
"""

import asyncio
import threading
import time
from typing import Any, List
from unittest.mock import MagicMock, patch

import litellm
import pytest

from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.sync_stream_bridge import iter_sync_stream
from app.services.llm.types import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMRequest,
)


# ---------------------------------------------------------------------------
# 假的同步流（模拟 litellm.completion(stream=True) 返回的同步可迭代对象）
# ---------------------------------------------------------------------------


class _BlockingSyncStream:
    """第一个 next() 同步阻塞 block_seconds（模拟 sync socket read 卡死）。

    生产事故中事件循环冻结的直接等价物：若在事件循环线程调用 next()，
    整个 loop 阻塞 block_seconds；线程桥方案下它只阻塞工作线程。
    """

    def __init__(self, n_chunks: int = 10, block_seconds: float = 30.0):
        self._n = n_chunks
        self._block = block_seconds
        self.consumed = 0
        self.closed = False
        self.worker_thread: str | None = None

    def __iter__(self):
        return self

    def __next__(self):
        if self.consumed == 0:
            self.worker_thread = threading.current_thread().name
            time.sleep(self._block)  # 模拟同步 socket read，无 await 点
        if self.consumed >= self._n:
            raise StopIteration
        value = self.consumed
        self.consumed += 1
        return value

    def close(self) -> None:
        self.closed = True


class _SlowSyncStream:
    """每个 next() 同步 sleep per_chunk_sleep，可被 stop_event 在 chunk 边界中断。"""

    def __init__(self, n_chunks: int = 100, per_chunk_sleep: float = 0.05):
        self._n = n_chunks
        self._sleep = per_chunk_sleep
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.consumed >= self._n:
            raise StopIteration
        time.sleep(self._sleep)
        value = self.consumed
        self.consumed += 1
        return value

    def close(self) -> None:
        self.closed = True


class _BoomError(Exception):
    """测试用异常类型（模拟 litellm 抛出的连接/认证错误）。"""


# ---------------------------------------------------------------------------
# ① 事件循环不被同步读冻结（生产事故回归测试）
# ---------------------------------------------------------------------------


class TestEventLoopNotFrozen:
    @pytest.mark.asyncio
    async def test_blocking_sync_read_does_not_freeze_event_loop(self):
        """同步流首块阻塞 30s 期间：事件循环上的并发心跳协程照常调度，
        消费端可在 2s 内被取消（旧 async-for-acompletion 路径会冻结整个 loop，
        watchdog 的 asyncio.sleep 都无法运行——c286b0c1 卡 10h 的机制）。"""
        stream = _BlockingSyncStream(n_chunks=10, block_seconds=30.0)
        heartbeat = 0

        async def _beat():
            nonlocal heartbeat
            while True:
                await asyncio.sleep(0.1)
                heartbeat += 1

        beat_task = asyncio.create_task(_beat())

        async def _consume():
            async for _chunk in iter_sync_stream(lambda: stream):
                pass

        consumer = asyncio.create_task(_consume())
        try:
            done, _pending = await asyncio.wait({consumer}, timeout=2.0)
            assert not done, "同步阻塞 30s 期间消费端不应完成"
            # 心跳照常跳：事件循环未被冻结
            assert heartbeat >= 10, (
                f"事件循环疑似被同步读冻结：2s 内心跳仅 {heartbeat} 次"
            )
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
        finally:
            beat_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await beat_task

    @pytest.mark.asyncio
    async def test_adapter_stream_complete_cancel_during_blocking_read(self):
        """适配器层回归：stream_complete 遇到首块卡死的同步流时，消费任务
        可在数秒内取消收口（旧路径事件循环冻结后 watchdog/取消全部失效）。"""
        adapter = LiteLLMAdapter(_make_config())

        def _fake_completion(**kwargs: Any):
            return _BlockingSyncStream(n_chunks=5, block_seconds=30.0)

        async def _consume():
            with patch("litellm.completion", _fake_completion):
                async for _chunk in adapter.stream_complete(_make_request()):
                    pass

        consumer = asyncio.create_task(_consume())
        done, _pending = await asyncio.wait({consumer}, timeout=3.0)
        assert not done, "首块阻塞 30s，消费端 3s 内不应完成"
        consumer.cancel()
        # 取消后必须能在 3s 内收口（不等 30s 同步读结束——工作线程是 daemon；
        # 等待已取消的任务会重抛 CancelledError，显式捕获即证明收口完成）
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(consumer, timeout=3.0)


# ---------------------------------------------------------------------------
# ② chunk 顺序与完整性
# ---------------------------------------------------------------------------


class TestChunkIntegrity:
    @pytest.mark.asyncio
    async def test_chunks_preserved_in_order(self):
        chunks = list(range(50))
        received: List[int] = []
        async for c in iter_sync_stream(lambda: iter(chunks)):
            received.append(c)
        assert received == chunks

    @pytest.mark.asyncio
    async def test_adapter_parses_sync_stream_chunks(self):
        """适配器 stream_complete 改为消费同步 litellm.completion 流后，
        token/done 块解析语义不变（stream_options/usage/kind 分流照旧）。"""
        chunks = [
            _make_stream_chunk(content="Hello"),
            _make_stream_chunk(content=" world"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        def _fake_completion(**kwargs: Any):
            return iter(chunks)

        # acompletion 不应再被流式路径调用
        async def _forbidden_acompletion(**kwargs: Any):
            raise AssertionError("流式路径不得再调用 litellm.acompletion")

        adapter = LiteLLMAdapter(_make_config())
        with patch("litellm.completion", _fake_completion), patch(
            "litellm.acompletion", _forbidden_acompletion
        ):
            result = [c async for c in adapter.stream_complete(_make_request())]

        tokens = [c for c in result if c["type"] == "token"]
        done = [c for c in result if c["type"] == "done"]
        assert [c["content"] for c in tokens] == ["Hello", " world"]
        assert len(done) == 1
        assert done[0]["content"] == "Hello world"
        assert done[0]["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# ③ 取消语义：工作线程退出 + 流关闭（无泄漏）
# ---------------------------------------------------------------------------


class TestCancellationStopsWorker:
    @pytest.mark.asyncio
    async def test_aclose_stops_worker_and_closes_stream(self):
        """消费端 aclose（watchdog 取消/break 后生成器关闭）→ 工作线程在 chunk
        边界停止拉取并在 finally 关闭同步流（连接不泄漏）。"""
        stream = _SlowSyncStream(n_chunks=100, per_chunk_sleep=0.05)
        gen = iter_sync_stream(lambda: stream)

        first = await gen.__anext__()
        assert first == 0
        await gen.aclose()

        # 工作线程在 0.05s 节奏的下一个边界退出并 close
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if stream.closed:
                break
            await asyncio.sleep(0.05)
        assert stream.closed, "工作线程退出时必须关闭同步流（连接泄漏防护）"
        assert stream.consumed <= 3, (
            f"取消后工作线程应停止拉取，实际又消费了 {stream.consumed} 块"
        )

    @pytest.mark.asyncio
    async def test_worker_runs_off_event_loop_thread(self):
        """同步流的建连与迭代必须发生在专用工作线程（非事件循环线程）。"""
        seen: dict[str, str] = {}

        def _factory():
            seen["thread"] = threading.current_thread().name
            return iter(["a"])

        out = [c async for c in iter_sync_stream(_factory)]
        assert out == ["a"]
        assert seen["thread"] != threading.main_thread().name, (
            "同步 LLM 调用必须在工作线程执行"
        )
        assert "llm" in seen["thread"], f"工作线程名应可辨识：{seen['thread']}"


# ---------------------------------------------------------------------------
# ④ kwargs 透传（timeout 在线程内生效）与异常类型保持
# ---------------------------------------------------------------------------


class TestKwargsAndExceptionPassthrough:
    @pytest.mark.asyncio
    async def test_stream_kwargs_passed_through_to_sync_completion(self):
        """stream=True/timeout 等 kwargs 原样透传给工作线程内的
        litellm.completion（timeout 在线程内作为 httpx 同步超时生效）。"""
        captured: dict[str, Any] = {}

        def _fake_completion(**kwargs: Any):
            captured.update(kwargs)
            return iter([_make_stream_chunk(finish_reason="stop")])

        adapter = LiteLLMAdapter(_make_config())
        with patch("litellm.completion", _fake_completion):
            _ = [c async for c in adapter.stream_complete(_make_request())]

        assert captured.get("stream") is True
        assert captured.get("timeout") == _make_config().timeout
        assert captured.get("model") == adapter._litellm_model

    @pytest.mark.asyncio
    async def test_connect_exception_propagates_with_type(self):
        """建连阶段异常（litellm 抛错）经队列投递后在消费端原样重抛，
        异常类型保持（适配器 except 分类依赖类型匹配）。"""

        def _factory():
            raise _BoomError("connect failed")

        with pytest.raises(_BoomError, match="connect failed"):
            async for _c in iter_sync_stream(_factory):
                pass

    @pytest.mark.asyncio
    async def test_mid_stream_exception_propagates_with_type(self):
        """迭代中途异常同样原样重抛（模拟 read 中途连接重置）。"""

        def _factory():
            def _gen():
                yield 1
                raise _BoomError("connection reset mid-stream")

            return _gen()

        collected: list[int] = []
        with pytest.raises(_BoomError, match="connection reset"):
            async for c in iter_sync_stream(_factory):
                collected.append(c)
        assert collected == [1]

    @pytest.mark.asyncio
    async def test_adapter_stream_auth_error_classified(self):
        """适配器流式错误分类不随线程桥改变：AuthenticationError → error chunk
        error_type=authentication（异常对象跨线程投递后类型不变）。"""
        exc = litellm.exceptions.AuthenticationError(
            "OpenAIException - authorization failed", "openai", "gpt-4o"
        )

        def _fake_completion(**kwargs: Any):
            raise exc

        adapter = LiteLLMAdapter(_make_config())
        error_chunks: list[dict] = []
        with patch("litellm.completion", _fake_completion):
            async for chunk in adapter.stream_complete(_make_request()):
                if chunk.get("type") == "error":
                    error_chunks.append(chunk)

        assert error_chunks, "认证错误必须产出 error chunk"
        assert error_chunks[0]["error_type"] == "authentication"


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def _make_config() -> LLMConfig:
    return LLMConfig(
        provider=LLMProvider.OPENAI,
        api_key="sk-test-key",
        model="qwen-test",
        base_url="http://sglang.example:30000/v1",
        timeout=10,
        max_tokens=100,
        temperature=0.6,
    )


def _make_request() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        temperature=0.6,
        max_tokens=100,
    )


def _make_stream_chunk(
    content: str | None = None,
    finish_reason: str | None = None,
) -> MagicMock:
    """构造一个 OpenAI/SGLang 风格流式 chunk（纯 content 模型，无 reasoning）。"""
    chunk = MagicMock()
    chunk.usage = None
    choice = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = None
    delta.thinking = None
    delta.tool_calls = None
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    return chunk
