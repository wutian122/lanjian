"""
Task 2（fix-audit-observability-time-governance）：LLM 输出截断可见化

finish_reason=length 时 stream_llm_call 消费端 MUST：
1. 发射 warning 事件（消息含"输出被 max_tokens 截断"、agent 名、当轮迭代号、max_tokens 值）；
2. 向 _conversation_history 追加截断提示（供下一轮 LLM 压缩/分批重试）；
3. 置位 _last_llm_truncated，Final Answer 无 findings 时日志归因"疑似 max_tokens 截断"。

Scenario 覆盖：
- Final Answer 轮 length 截断 → warning + 标志 + 历史提示 + 解析失败归因
- 中间轮 length 截断 → warning + 下一轮历史可见提示 + 标志按轮重置
"""
import json
import logging
import time
from types import SimpleNamespace
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


def _stream_factory(text, finish_reason):
    async def _gen(messages=None, temperature=None, max_tokens=None):
        yield {"type": "token", "content": text, "accumulated": text}
        yield {
            "type": "done",
            "content": text,
            "usage": {"total_tokens": 10},
            "finish_reason": finish_reason,
        }
    return _gen


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


def _warning_messages(agent):
    """从 emitter.emit 调用记录中提取 warning 事件消息。"""
    msgs = []
    for call in agent.event_emitter.emit.await_args_list:
        event_data = call.args[0]
        if event_data.event_type == "warning":
            msgs.append(event_data.message)
    return msgs


def _emitted_warning_texts(agent):
    """提取归因 warning 文本，兼容真实 emitter（emit AgentEventData）与 monkeypatch 的 emit_event AsyncMock。"""
    texts = []
    emitter = getattr(agent, "event_emitter", None)
    if emitter is not None and hasattr(emitter.emit, "await_args_list"):
        for call in emitter.emit.await_args_list:
            event_data = call.args[0]
            if event_data.event_type == "warning":
                texts.append(event_data.message)
    if isinstance(getattr(agent, "emit_event", None), AsyncMock):
        for call in agent.emit_event.await_args_list:
            if call.args and call.args[0] == "warning":
                texts.append(call.args[1])
    return texts


# ---------- Scenario 1：Final Answer 轮被截断 ----------

@pytest.mark.asyncio
async def test_length_finish_reason_emits_warning_sets_flag_and_history_hint():
    """finish_reason=length：warning 事件（含 agent 名/迭代号/max_tokens 值）+ 标志置位 + 历史提示。"""
    truncated_text = "Final Answer: " + json.dumps(
        {"findings": [{"title": "半截发现"}], "summary": "被 max_tokens 切断",
    }, ensure_ascii=False)[:40]  # 模拟被切断的不完整输出
    agent = _make_agent(_stream_factory(truncated_text, "length"))
    agent._iteration = 3
    # 与真实调用一致：run 循环传入的就是 agent 自己的 _conversation_history
    history = agent._conversation_history
    history.append({"role": "user", "content": "请输出 Final Answer"})

    output, tokens = await agent.stream_llm_call(history)

    # 返回签名不变
    assert output == truncated_text
    assert tokens == 10
    # 截断标志置位（供 Final Answer 解析失败归因）
    assert agent._last_llm_truncated is True
    # warning 事件：含"输出被 max_tokens 截断"字样、agent 名、迭代号、max_tokens 值
    warnings = _warning_messages(agent)
    assert warnings, "finish_reason=length 时必须发射 warning 事件"
    joined = "\n".join(warnings)
    assert "输出被 max_tokens 截断" in joined
    assert "Recon" in joined
    assert "第 3 轮" in joined
    assert "8192" in joined
    # 对话历史注入截断提示（user 角色，供下一轮 LLM 知晓）
    assert history[-1]["role"] == "user"
    assert "截断" in history[-1]["content"]


@pytest.mark.asyncio
async def test_explicit_max_tokens_arg_shown_in_warning():
    """显式传入 max_tokens 参数时，warning 展示该值而非服务配置值。"""
    agent = _make_agent(_stream_factory("x", "length"), max_tokens=8192)
    agent._iteration = 1
    history = agent._conversation_history
    history.append({"role": "user", "content": "hi"})

    await agent.stream_llm_call(history, max_tokens=4096)

    joined = "\n".join(_warning_messages(agent))
    assert "4096" in joined
    assert "8192" not in joined


# ---------- Scenario 2：中间轮被截断 ----------

@pytest.mark.asyncio
async def test_intermediate_round_truncation_hint_reaches_next_round():
    """中间轮 length 截断：warning + 历史提示；下一轮 stop 正常时标志重置、提示对 LLM 可见且不重复追加。"""
    agent = _make_agent(_stream_factory("Thought: 还在读文件\nAction: read_file", "length"))
    agent._iteration = 2
    history = agent._conversation_history
    history.append({"role": "user", "content": "开始审计"})

    await agent.stream_llm_call(history)

    assert agent._last_llm_truncated is True
    hint = history[-1]
    assert hint["role"] == "user"
    assert "截断" in hint["content"]
    warnings_after_first = len(_warning_messages(agent))

    # 下一轮：finish_reason=stop
    seen = {}

    async def _stop_stream(messages=None, temperature=None, max_tokens=None):
        seen["messages"] = messages
        yield {
            "type": "done",
            "content": "Thought: 继续\nAction: search_code",
            "usage": {"total_tokens": 5},
            "finish_reason": "stop",
        }

    agent.llm_service.chat_completion_stream = _stop_stream
    agent._iteration = 3

    output, tokens = await agent.stream_llm_call(history)

    assert output == "Thought: 继续\nAction: search_code"
    assert tokens == 5
    # 标志按轮重置
    assert agent._last_llm_truncated is False
    # 截断提示随下一轮 messages 发给 LLM
    assert any("截断" in m.get("content", "") for m in seen["messages"]), (
        "下一轮发给 LLM 的历史必须包含截断提示"
    )
    # 提示只追加一次（stop 轮不追加）
    assert sum(1 for m in history if "截断" in m.get("content", "")) == 1
    # 第二轮无新增 warning
    assert len(_warning_messages(agent)) == warnings_after_first


@pytest.mark.asyncio
async def test_stop_finish_reason_no_warning_no_flag_no_hint():
    """finish_reason=stop：不告警、不置位、不追加历史。"""
    agent = _make_agent(_stream_factory("Thought: 正常输出", "stop"))
    agent._iteration = 1
    history = agent._conversation_history
    history.append({"role": "user", "content": "hi"})

    output, _ = await agent.stream_llm_call(history)

    assert output == "Thought: 正常输出"
    assert agent._last_llm_truncated is False
    assert _warning_messages(agent) == []
    assert len(history) == 1  # stop 轮不追加任何提示


# ---------- Scenario 1 归因：截断轮 Final Answer 必须发归因 warning（事件流 + 日志双通道） ----------

def _make_analysis_agent(monkeypatch):
    """轻量构造 AnalysisAgent（stream_llm_call 被替换，llm_service 用 SimpleNamespace）。"""
    from app.services.agent.agents.analysis import AnalysisAgent

    agent = AnalysisAgent(llm_service=SimpleNamespace(), tools={})
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "emit_event", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_decision", AsyncMock())
    monkeypatch.setattr(agent, "emit_llm_thought", AsyncMock())
    monkeypatch.setattr(agent, "emit_finding", AsyncMock())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    agent.config.max_iterations = 3
    # app.services.agent logger 在日志初始化时被设 propagate=False（core/logging.py），
    # caplog handler 挂在 root 收不到，临时打开传播以便断言容器日志内容
    monkeypatch.setattr(logging.getLogger("app.services.agent"), "propagate", True)
    return agent


def _make_analysis_with_real_stream(stream_fn):
    """构造走真实 stream_llm_call（done 块置位截断标志）的 AnalysisAgent。"""
    from app.services.agent.agents.analysis import AnalysisAgent

    service = MagicMock()
    service.get_agent_timeout_config = MagicMock(return_value={
        "llm_first_token_timeout": 30,
        "llm_stream_timeout": 60,
        "agent_timeout": 1800,
        "sub_agent_timeout": 600,
        "tool_timeout": 60,
    })
    service.config.max_tokens = 8192
    service.chat_completion_stream = stream_fn
    emitter = MagicMock()
    # base 层 emit_* 方法直接 await event_emitter.emit_xxx，需全部为 AsyncMock
    for _name in (
        "emit", "emit_info", "emit_warning", "emit_error", "emit_thinking",
        "emit_tool_call", "emit_tool_result", "emit_finding", "emit_progress",
        "emit_phase_start", "emit_phase_complete", "emit_task_complete",
    ):
        setattr(emitter, _name, AsyncMock())
    agent = AnalysisAgent(llm_service=service, tools={}, event_emitter=emitter)
    agent._check_token_budget_exceeded = lambda: False
    agent.config.max_iterations = 3
    return agent


def _analysis_stream(text, finish_reason):
    async def _gen(messages=None, temperature=None, max_tokens=None):
        yield {"type": "token", "content": text, "accumulated": text}
        yield {
            "type": "done",
            "content": text,
            "usage": {"total_tokens": 10},
            "finish_reason": finish_reason,
        }
    return _gen


@pytest.mark.asyncio
async def test_truncated_final_answer_partial_findings_emits_warning_event():
    """json-repair 把半截 findings 修成部分 findings（走 if 分支）时，归因 warning 事件仍必须发出。"""
    # 审查亲测：半截 JSON 被 json-repair 修成含 1 条部分 finding 的合法 dict
    half_json = '{"findings": [{"title": "SQL注入", "severity": "high"'
    text = "Thought: 总结发现\nFinal Answer: " + half_json
    agent = _make_analysis_with_real_stream(_analysis_stream(text, "length"))

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is True
    # 证明 json-repair 确实修成了部分 findings（否则本测试未覆盖绕过路径）
    assert len(result.data["findings"]) == 1
    warnings = _emitted_warning_texts(agent)
    assert any("截断" in m and "Final Answer" in m for m in warnings), (
        f"截断轮 Final Answer 即使解析出部分 findings 也必须发归因 warning，实际: {warnings}"
    )


@pytest.mark.asyncio
async def test_truncated_final_answer_empty_findings_emits_warning_event():
    """{'findings': [ 被 json-repair 修成空 findings（有键无内容，同样走 if 分支）也必须归因。"""
    text = 'Thought: 总结\nFinal Answer: {"findings": ['
    agent = _make_analysis_with_real_stream(_analysis_stream(text, "length"))

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is True
    assert result.data["findings"] == []
    warnings = _emitted_warning_texts(agent)
    assert any("截断" in m and "Final Answer" in m for m in warnings), (
        f"截断轮空 findings 必须发归因 warning，实际: {warnings}"
    )


@pytest.mark.asyncio
async def test_forced_summary_truncation_emits_warning(monkeypatch):
    """_run_forced_summary 强制总结轮被截断：解析出部分 findings 后同样发归因 warning。"""
    agent = _make_analysis_agent(monkeypatch)

    async def _fake_stream(messages, **kwargs):
        # 模拟 base 层 done 块已置位截断标志
        agent._last_llm_truncated = True
        # 半截总结 JSON（json-repair 可修成 1 条部分 finding）
        return ('{"findings": [{"title": "XSS", "severity": "low"}], "summar', 10)

    monkeypatch.setattr(agent, "stream_llm_call", _fake_stream)

    findings = await agent._run_forced_summary([])

    assert len(findings) == 1, "json-repair 应解析出 1 条部分 finding"
    warnings = _emitted_warning_texts(agent)
    assert any("截断" in m for m in warnings), (
        f"强制总结截断轮必须发归因 warning，实际: {warnings}"
    )


@pytest.mark.asyncio
async def test_final_answer_no_findings_truncation_emits_event_and_log(monkeypatch, caplog):
    """Final Answer 无 findings 键且上轮被截断 → 归因 warning 事件 + 容器日志双通道。"""
    agent = _make_analysis_agent(monkeypatch)

    async def _fake_stream(messages, **kwargs):
        # 模拟 base 层 done 块已置位截断标志
        agent._last_llm_truncated = True
        return (
            'Thought: 总结\nFinal Answer: {"summary": "被截断的总结，没有 findings 键"}',
            10,
        )

    monkeypatch.setattr(agent, "stream_llm_call", _fake_stream)

    with caplog.at_level(logging.WARNING):
        result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is True
    # 事件流通道
    warnings = _emitted_warning_texts(agent)
    assert any("截断" in m and "Final Answer" in m for m in warnings), warnings
    # 容器日志通道
    trunc_logs = [r.message for r in caplog.records if "截断" in r.message]
    assert trunc_logs, "截断归因必须同时写入容器日志"


@pytest.mark.asyncio
async def test_final_answer_no_truncation_no_attribution(monkeypatch, caplog):
    """对照：未发生截断时，Final Answer 无论有无 findings 都不得发截断归因。"""
    agent = _make_analysis_agent(monkeypatch)

    async def _fake_stream(messages, **kwargs):
        return 'Thought: 总结\nFinal Answer: {"summary": "确实没有发现"}', 10

    monkeypatch.setattr(agent, "stream_llm_call", _fake_stream)

    with caplog.at_level(logging.WARNING):
        result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is True
    assert not any("截断" in m for m in _emitted_warning_texts(agent)), (
        "未截断时不得发截断归因事件"
    )
    assert not [r for r in caplog.records if "截断" in r.message], (
        "未截断时日志不得含截断归因"
    )
