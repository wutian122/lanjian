"""
structured-output-protocol Task 4：Agent 循环按 kind 分流 token 事件

Task 3 在 adapter 层把流式 chunk 按 kind 分流（reasoning/content），本任务在
stream_llm_call 消费侧落地：
- kind="reasoning" 的 token → emit_thinking_token（前端思考区，accumulated 用思考累计）
- kind="content" 的 token → emit_content_token（新事件，正文流式区，accumulated 用正文累计）
- 无 kind 的 chunk（旧后端 / NATIVE_ONLY 伪流式）→ 全部走 thinking_token（现状兼容）
- done 返回值仅正文；thinking_end 收思考累计；新增 content_end 正文收尾事件（落库兜底）

Scenario 覆盖：
- 混合流（reasoning + content）→ 两类 token 事件分开发射、各带正确 accumulated
- 混合流 → 返回值仅正文、thinking_end 仅思考、content_end 带正文全文且先于 thinking_end
- 旧后端无 kind → 全走 thinking_token、无 content_* 事件、thinking_end 收全文（现状不变）
- event_manager：content_token 不落库/可丢弃聚合（同 thinking_token），content_end 落库
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.recon import ReconAgent
from app.services.agent.core.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_llm_circuit,
)
from app.services.agent.core.rate_limiter import get_llm_rate_limiter

# ---------- 工厂 ----------

def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    return e


def _make_service(stream_fn, max_tokens=8192):
    service = MagicMock()
    # BaseAgent._get_timeout_config() 走 hasattr 分支，需返回真实 dict（float() 可用）
    service.get_agent_timeout_config = MagicMock(return_value={
        "llm_first_token_timeout": 30,
        "llm_stream_timeout": 60,
        "agent_timeout": 1800,
        "sub_agent_timeout": 600,
        "tool_timeout": 60,
    })
    service.config.max_tokens = max_tokens
    service.chat_completion_stream = stream_fn
    return service


def _make_agent(stream_fn, max_tokens=8192):
    return ReconAgent(
        llm_service=_make_service(stream_fn, max_tokens=max_tokens),
        tools={},
        event_emitter=_make_emitter(),
    )


def _reset_resilience():
    """熔断器/限流器单例复位，避免跨用例污染（照搬 test_circuit_integration）。"""
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


def _emitted(agent):
    """从 emitter.emit 调用记录提取 [(event_type, metadata)]，按发射顺序。"""
    events = []
    for call in agent.event_emitter.emit.await_args_list:
        data = call.args[0]
        events.append((data.event_type, dict(data.metadata or {})))
    return events


def _tokens(agent, event_type):
    """提取某类 token 事件的 [(token, accumulated)]。"""
    return [
        (md.get("token"), md.get("accumulated"))
        for et, md in _emitted(agent)
        if et == event_type
    ]


def _end_accumulated(agent, event_type):
    """提取 end 事件（thinking_end/content_end）的 accumulated。"""
    for et, md in _emitted(agent):
        if et == event_type:
            return md.get("accumulated")
    return None


# ---------- 流夹具 ----------

def _mixed_stream():
    """新协议后端：先思考后正文，chunk 带 kind + 双累计键（Task 3 adapter 形态）。"""
    async def _gen(messages=None, temperature=None, max_tokens=None):
        yield {
            "type": "token", "kind": "reasoning", "content": "思",
            "accumulated": "思", "accumulated_content": "", "accumulated_reasoning": "思",
        }
        yield {
            "type": "token", "kind": "reasoning", "content": "考",
            "accumulated": "思考", "accumulated_content": "", "accumulated_reasoning": "思考",
        }
        yield {
            "type": "token", "kind": "content", "content": "正",
            "accumulated": "思考正", "accumulated_content": "正", "accumulated_reasoning": "思考",
        }
        yield {
            "type": "token", "kind": "content", "content": "文",
            "accumulated": "思考正文", "accumulated_content": "正文", "accumulated_reasoning": "思考",
        }
        yield {
            "type": "done", "content": "正文", "reasoning": "思考", "accumulated": "思考正文",
            "usage": {"total_tokens": 7}, "finish_reason": "stop",
        }
    return _gen


def _legacy_stream():
    """旧后端/NATIVE_ONLY 伪流式：chunk 无 kind，token 全量走思考流（Task 4 前形态）。"""
    async def _gen(messages=None, temperature=None, max_tokens=None):
        yield {"type": "token", "content": "混", "accumulated": "混"}
        yield {"type": "token", "content": "合", "accumulated": "混合"}
        yield {
            "type": "done", "content": "混合",
            "usage": {"total_tokens": 3}, "finish_reason": "stop",
        }
    return _gen


# ---------- Scenario 1：混合流分流 ----------

@pytest.mark.asyncio
async def test_mixed_stream_emits_separate_thinking_and_content_tokens():
    """kind=reasoning → thinking_token；kind=content → content_token；accumulated 各按本通道累计。"""
    agent = _make_agent(_mixed_stream())

    await agent.stream_llm_call(agent._conversation_history)

    assert _tokens(agent, "thinking_token") == [("思", "思"), ("考", "思考")], (
        "reasoning chunk 必须走 thinking_token，accumulated 仅含思考累计"
    )
    assert _tokens(agent, "content_token") == [("正", "正"), ("文", "正文")], (
        "content chunk 必须走 content_token，accumulated 仅含正文累计"
    )


@pytest.mark.asyncio
async def test_mixed_stream_return_value_is_content_only():
    """done 返回值仅正文（Final Answer 解析输入不再含思考噪声）。"""
    agent = _make_agent(_mixed_stream())

    output, tokens = await agent.stream_llm_call(agent._conversation_history)

    assert output == "正文", f"返回值必须仅正文，实际: {output!r}"
    assert "思" not in output
    assert tokens == 7


@pytest.mark.asyncio
async def test_mixed_stream_thinking_end_receives_reasoning_only():
    """thinking_end 收思考累计（思考区收尾显示思考文本，而非正文/拼接）。"""
    agent = _make_agent(_mixed_stream())

    await agent.stream_llm_call(agent._conversation_history)

    assert _end_accumulated(agent, "thinking_end") == "思考", (
        "新协议下 thinking_end 的 accumulated 必须仅思考累计"
    )


@pytest.mark.asyncio
async def test_mixed_stream_emits_content_end_with_full_content_before_thinking_end():
    """content_end 带正文全文（落库兜底/回放校准），且发射先于 thinking_end。"""
    agent = _make_agent(_mixed_stream())

    await agent.stream_llm_call(agent._conversation_history)

    assert _end_accumulated(agent, "content_end") == "正文", (
        "content_end 必须携带正文全文 accumulated"
    )
    event_types = [et for et, _ in _emitted(agent)]
    assert event_types.index("content_end") < event_types.index("thinking_end"), (
        "正文收尾 content_end 必须先于思考收尾 thinking_end"
    )


@pytest.mark.asyncio
async def test_mixed_stream_event_order_has_single_thinking_start():
    """边界事件顺序：thinking_start → thinking_token* → content_token* → content_end → thinking_end。"""
    agent = _make_agent(_mixed_stream())

    await agent.stream_llm_call(agent._conversation_history)

    event_types = [et for et, _ in _emitted(agent)]
    assert event_types[0] == "thinking_start"
    assert event_types.count("thinking_start") == 1
    # content_token 全部位于最后一个 thinking_token 之后
    last_thinking = max(i for i, et in enumerate(event_types) if et == "thinking_token")
    first_content = min(i for i, et in enumerate(event_types) if et == "content_token")
    assert first_content > last_thinking


# ---------- Scenario 2：旧后端无 kind 兼容 ----------

@pytest.mark.asyncio
async def test_legacy_stream_without_kind_all_goes_to_thinking():
    """无 kind chunk：全部走 thinking_token（现状），不发 content_token/content_end。"""
    agent = _make_agent(_legacy_stream())

    output, tokens = await agent.stream_llm_call(agent._conversation_history)

    assert _tokens(agent, "thinking_token") == [("混", "混"), ("合", "混合")], (
        "旧后端 token 必须保持走 thinking_token（兼容）"
    )
    assert _tokens(agent, "content_token") == [], "旧后端不得发射 content_token"
    assert _end_accumulated(agent, "content_end") is None, "旧后端不得发射 content_end"
    # thinking_end 收全文累计（前端 cleanThinkingContent 兼容路径不变）
    assert _end_accumulated(agent, "thinking_end") == "混合"
    assert output == "混合"
    assert tokens == 3


# ---------- Scenario 3：event_manager 落库/丢弃策略 ----------

class _FakeSession:
    """记录落库 event_type 的假 DB 会话。"""

    def __init__(self, saved):
        self._saved = saved

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self._saved.append(obj.event_type)

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_content_token_not_persisted_but_content_end_is():
    """content_token 高频不落库（同 thinking_token）；content_end/thinking_end 落库（回放兜底）。"""
    from app.services.agent.event_manager import EventManager

    saved = []
    mgr = EventManager(db_session_factory=lambda: _FakeSession(saved))
    task_id = "t_persist"
    mgr.create_queue(task_id)

    await mgr.add_event(task_id, "content_token", sequence=1,
                        metadata={"token": "正", "accumulated": "正"})
    await mgr.add_event(task_id, "thinking_token", sequence=2,
                        metadata={"token": "思", "accumulated": "思"})
    await mgr.add_event(task_id, "content_end", sequence=3,
                        message="正文输出完成", metadata={"accumulated": "正文全文"})
    await mgr.add_event(task_id, "thinking_end", sequence=4,
                        message="思考完成", metadata={"accumulated": "思考全文"})

    assert "content_token" not in saved, "正文 token 高频事件不得落库"
    assert "thinking_token" not in saved
    assert "content_end" in saved, "content_end 必须落库（SSE 重连/历史回放正文全文来源）"
    assert "thinking_end" in saved


@pytest.mark.asyncio
async def test_content_token_dropped_when_queue_full():
    """队列满时 content_token 非阻塞丢弃并计数 dropped_content_tokens（同 thinking_token 策略）。"""
    from app.services.agent.event_manager import EventManager

    mgr = EventManager(db_session_factory=None)
    task_id = "t_drop_content"
    mgr._event_queues[task_id] = asyncio.Queue(maxsize=3)
    q = mgr._event_queues[task_id]
    for _ in range(3):
        q.put_nowait({"filler": True})
    assert q.full()

    # 第 1 条撞满丢弃计数，后续 4 条在聚合窗口内被跳过（不阻塞、不抛错）
    for i in range(5):
        await asyncio.wait_for(
            mgr.add_event(task_id, "content_token", sequence=i,
                          metadata={"token": "x", "accumulated": "x" * (i + 1)}),
            timeout=1.0,
        )

    assert mgr.dropped_content_tokens.get(task_id, 0) == 1, (
        f"预期丢弃 1 个 content_token（其余聚合），实际 "
        f"{mgr.dropped_content_tokens.get(task_id, 0)}"
    )
    assert q.qsize() == 3


@pytest.mark.asyncio
async def test_thinking_and_content_token_coalesce_buffers_are_independent():
    """thinking_token 与 content_token 聚合缓冲互不串扰：交替到达时各自独立聚合/入队。"""
    from app.services.agent.event_manager import EventManager

    mgr = EventManager(db_session_factory=None)
    task_id = "t_coalesce"
    mgr.create_queue(task_id)
    q = mgr._event_queues[task_id]

    # 交替发 4 轮（共 8 条），每条 accumulated 增量极小（窗口内聚合条件）
    for i in range(4):
        await mgr.add_event(
            task_id, "thinking_token", sequence=100 + i,
            metadata={"token": "思", "accumulated": "思" * (i + 1)},
        )
        await mgr.add_event(
            task_id, "content_token", sequence=200 + i,
            metadata={"token": "正", "accumulated": "正" * (i + 1)},
        )

    # asyncio.Queue 无 snapshot：直接排空统计入队事件类型
    types = []
    while not q.empty():
        types.append(q.get_nowait().get("event_type"))

    # 两类各有第一条入队（缓冲独立：若共用缓冲，交替刷新会导致聚合失效、8 条全入队）
    assert types.count("thinking_token") >= 1
    assert types.count("content_token") >= 1
    assert len(types) < 8, f"聚合应跳过窗口内小增量事件，实际入队 {len(types)} 条"
