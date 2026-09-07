"""
structured-output-protocol Task 3：流式 chunk 按 kind 分流测试

背景（乱码根治的核心一刀）：Qwen3 thinking 模型（SGLang --reasoning-parser
qwen3）流式响应含两个独立字段——delta.reasoning_content（思考流）与
delta.content（正文）。旧实现用 ``content or reasoning_content or thinking``
的 or 链把两者混进同一个 content 流：思考退化噪声（stopstopSTOP /
UUU...LLL...III...）直接污染正文视野与 Final Answer 解析输入。

本任务在 adapter 产出侧把两通道在 chunk 层分离：

- 每个 token chunk 带 ``kind``：delta.content 来源 → ``"content"``；
  delta.reasoning_content / delta.thinking 来源 → ``"reasoning"``；
  同一 delta 两者都出现时各自独立 yield（两个 if，非 if/else）；
- 累计拆分：``accumulated_content`` 仅累计正文、``accumulated_reasoning``
  仅累计思考；旧 ``accumulated`` 键保留为两者拼接（兼容 Task 4 前的下游）；
- done 块三键语义：``content``=仅正文累计、``reasoning``=仅思考累计、
  ``accumulated``=两者拼接（兼容）；
- 零破坏：后端不返回 reasoning 字段时全部 chunk kind="content"，
  content/accumulated 与旧行为逐字节一致。
"""

from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.types import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMRequest,
)


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
    content: Optional[str] = None,
    reasoning: Optional[str] = None,
    thinking: Optional[str] = None,
    finish_reason: Optional[str] = None,
) -> MagicMock:
    """构造一个 SGLang 风格流式 chunk（delta 三字段独立）"""
    chunk = MagicMock()
    chunk.usage = None
    choice = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = reasoning
    delta.thinking = thinking
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    return chunk


async def _collect_stream(chunks: List[MagicMock]) -> List[dict]:
    """patch litellm.completion（同步，工作线程内调用）返回给定 chunk 序列的
    同步可迭代对象，跑完 stream_complete（F2/A1 线程桥后的出站边界）"""
    adapter = LiteLLMAdapter(_make_config())

    def _fake_completion(**kwargs: Any):
        return iter(chunks)

    with patch("litellm.completion", _fake_completion):
        return [c async for c in adapter.stream_complete(_make_request())]


# ---------------------------------------------------------------------------
# ① 混合流：reasoning 与 content 按 kind 独立 yield，累计互不串流
# ---------------------------------------------------------------------------


class TestMixedStreamKindSplit:
    @pytest.mark.asyncio
    async def test_reasoning_and_content_yield_as_independent_kinds(self):
        """先 reasoning 后 content 的 delta 序列 → 两种 kind 独立 chunk，
        不再 or 链合并为同一 content 流"""
        chunks = [
            _make_stream_chunk(reasoning="让我思考一下"),
            _make_stream_chunk(reasoning="stopstopSTOP"),  # 思考退化噪声
            _make_stream_chunk(content="Final Answer: 配置正确"),
            _make_stream_chunk(content="，无风险。"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)

        tokens = [c for c in result if c["type"] == "token"]
        kinds = [c["kind"] for c in tokens]
        assert kinds == ["reasoning", "reasoning", "content", "content"]

        # 各通道增量文本正确归属
        reasoning_pieces = [c["content"] for c in tokens if c["kind"] == "reasoning"]
        content_pieces = [c["content"] for c in tokens if c["kind"] == "content"]
        assert "".join(reasoning_pieces) == "让我思考一下stopstopSTOP"
        assert "".join(content_pieces) == "Final Answer: 配置正确，无风险。"

    @pytest.mark.asyncio
    async def test_accumulated_split_never_cross_contaminates(self):
        """accumulated_content 永不含思考文本；accumulated_reasoning 永不含正文；
        旧 accumulated 键为两者拼接（兼容）"""
        chunks = [
            _make_stream_chunk(reasoning="思考A"),
            _make_stream_chunk(content="正文B"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)
        tokens = [c for c in result if c["type"] == "token"]

        for tok in tokens:
            # 新语义键必须存在
            assert "accumulated_content" in tok
            assert "accumulated_reasoning" in tok
            # 旧键保留（Task 4 前的下游 base.py 仍读它）
            assert "accumulated" in tok
            # 正文累计里永远不允许出现思考文本
            assert "思考A" not in tok["accumulated_content"]
            # 思考累计里永远不允许出现正文文本
            assert "正文B" not in tok["accumulated_reasoning"]

        last = tokens[-1]
        assert last["accumulated_content"] == "正文B"
        assert last["accumulated_reasoning"] == "思考A"
        # 拼接顺序 = 流到达顺序（思考阶段先于正文阶段）
        assert last["accumulated"] == "思考A正文B"

    @pytest.mark.asyncio
    async def test_thinking_field_also_maps_to_reasoning_kind(self):
        """delta.thinking（部分后端的思考字段名）同样归入 kind=reasoning"""
        chunks = [
            _make_stream_chunk(thinking="深度思考中"),
            _make_stream_chunk(content="答案"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)
        tokens = [c for c in result if c["type"] == "token"]

        assert tokens[0]["kind"] == "reasoning"
        assert tokens[0]["content"] == "深度思考中"
        assert tokens[1]["kind"] == "content"

    @pytest.mark.asyncio
    async def test_same_delta_both_fields_yield_two_chunks(self):
        """同一 delta 同时含 reasoning_content 与 content → 两个独立 chunk
        （两个 if 而非 if/else；or 链会丢思考，这里不允许丢）"""
        chunks = [
            _make_stream_chunk(reasoning="转念一想", content="正式回答"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)
        tokens = [c for c in result if c["type"] == "token"]

        assert len(tokens) == 2
        assert tokens[0]["kind"] == "reasoning"
        assert tokens[0]["content"] == "转念一想"
        assert tokens[1]["kind"] == "content"
        assert tokens[1]["content"] == "正式回答"
        # 思考先于正文 yield（模型先想后答的流顺序）
        assert tokens[1]["accumulated"] == "转念一想正式回答"


# ---------------------------------------------------------------------------
# ② 纯 content 模型：零破坏，行为与旧实现完全一致
# ---------------------------------------------------------------------------


class TestPureContentModelUnchanged:
    @pytest.mark.asyncio
    async def test_all_chunks_kind_content_and_accumulation_identical(self):
        """后端不返回 reasoning 字段 → 全部 kind=content，
        content/accumulated 序列与旧 or 链实现逐字节一致"""
        chunks = [
            _make_stream_chunk(content="Hello"),
            _make_stream_chunk(content=" world"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)
        tokens = [c for c in result if c["type"] == "token"]

        assert all(c["kind"] == "content" for c in tokens)
        # 旧下游读取的两个键：增量 content 与累计 accumulated
        assert [c["content"] for c in tokens] == ["Hello", " world"]
        assert [c["accumulated"] for c in tokens] == ["Hello", "Hello world"]
        # 思考累计恒空，正文累计 == 拼接累计
        assert all(c["accumulated_reasoning"] == "" for c in tokens)
        assert tokens[-1]["accumulated_content"] == "Hello world"
        assert tokens[-1]["accumulated"] == tokens[-1]["accumulated_content"]

    @pytest.mark.asyncio
    async def test_done_block_pure_content_matches_old_shape(self):
        """纯 content 模型 done 块：content 即全文，reasoning 为空，
        旧消费方读 content/usage/finish_reason 不受影响"""
        chunks = [
            _make_stream_chunk(content="abc"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)
        done = [c for c in result if c["type"] == "done"]
        assert len(done) == 1
        assert done[0]["content"] == "abc"
        assert done[0]["reasoning"] == ""
        assert done[0]["accumulated"] == "abc"
        assert done[0]["finish_reason"] == "stop"
        assert done[0]["usage"] is not None


# ---------------------------------------------------------------------------
# ③ done 块三键语义：content=正文 / reasoning=思考 / accumulated=拼接
# ---------------------------------------------------------------------------


class TestDoneBlockSemanticSplit:
    @pytest.mark.asyncio
    async def test_done_three_keys_semantics(self):
        chunks = [
            _make_stream_chunk(reasoning="思考过程"),
            _make_stream_chunk(reasoning="stopstopSTOP"),
            _make_stream_chunk(content="正文答案"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)
        done = [c for c in result if c["type"] == "done"]
        assert len(done) == 1

        # content 仅正文（Final Answer 解析输入不再混入思考流）
        assert done[0]["content"] == "正文答案"
        # reasoning 仅思考
        assert done[0]["reasoning"] == "思考过程stopstopSTOP"
        # 旧 accumulated 键为拼接（兼容）
        assert done[0]["accumulated"] == "思考过程stopstopSTOP正文答案"
        assert done[0]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_done_without_finish_reason_also_split(self):
        """流结束但无 finish_reason 的兜底 done 块同样带三键语义"""
        chunks = [
            _make_stream_chunk(reasoning="还在想"),
            _make_stream_chunk(content="给出结论"),
        ]

        result = await _collect_stream(chunks)
        done = [c for c in result if c["type"] == "done"]
        assert len(done) == 1
        assert done[0]["finish_reason"] == "complete"
        assert done[0]["content"] == "给出结论"
        assert done[0]["reasoning"] == "还在想"
        assert done[0]["accumulated"] == "还在想给出结论"


# ---------------------------------------------------------------------------
# ④ 思考退化噪声只存在于 kind=reasoning 通道，正文通道永不被污染
# ---------------------------------------------------------------------------


class TestReasoningDegradationNoiseIsolated:
    @pytest.mark.asyncio
    async def test_stopstop_noise_only_in_reasoning_chunks(self):
        """stopstopSTOP / UUU...LLL... 类退化噪声只出现在 reasoning chunk 与
        done.reasoning；正文 chunk 与 done.content 干干净净"""
        noise = "stopstopSTOP UUU...LLL...III..."
        chunks = [
            _make_stream_chunk(reasoning="正常思考后开始退化："),
            _make_stream_chunk(reasoning=noise),
            _make_stream_chunk(content="审计结论：未发现风险"),
            _make_stream_chunk(finish_reason="stop"),
        ]

        result = await _collect_stream(chunks)
        tokens = [c for c in result if c["type"] == "token"]
        done = [c for c in result if c["type"] == "done"][0]

        content_chunks = [c for c in tokens if c["kind"] == "content"]
        reasoning_chunks = [c for c in tokens if c["kind"] == "reasoning"]

        # 噪声在思考通道可见
        assert any("stopstopSTOP" in c["content"] for c in reasoning_chunks)
        # 正文通道任何字段都不含噪声
        for c in content_chunks:
            assert "stop" not in c["content"].lower()
            assert "UUU" not in c["accumulated_content"]
        # done 块：正文干净，噪声归 reasoning
        assert "stop" not in done["content"].lower()
        assert done["content"] == "审计结论：未发现风险"
        assert "stopstopSTOP" in done["reasoning"]
        assert "stopstopSTOP" in done["accumulated"]  # 拼接兼容键保留全文
