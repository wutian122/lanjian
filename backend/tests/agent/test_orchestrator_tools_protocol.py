"""
structured-output-protocol Task 7：Orchestrator 调度轮原生 tools 协议

能力探测 tools=True 时，Orchestrator 决策轮 LLM 调用携带 OpenAI tools 定义
（dispatch_agent/finish/summarize），模型 tool_calls 响应直接映射到现有
action 分发（_dispatch_agent / finish 门禁链 / summarize）；文本协议
（Thought:/Action:/Action Input:）路径原样保留（降级共存）。

覆盖：
- adapter 流式聚合 delta.tool_calls（按 index 聚合、arguments 增量拼接、done 输出）
- base.stream_llm_call 透传 tools 参数并暴露 _last_tool_calls（每轮重置）
- Orchestrator tools 定义形态（三函数 schema 与现有 Action 语义一一对应）
- tool_calls → AgentStep 映射（三动作 / 坏 JSON 防御 / 未知 name 自愈 / 空列表）
- 决策循环集成：tools 注入、dispatch/finish/summarize 分发等价、
  llm_action/llm_decision 事件流等价、能力 False 不传 tools、文本路径不变
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.agents.orchestrator import AgentStep, OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.structured_output import BackendCapabilities
from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.types import LLMConfig, LLMMessage, LLMProvider, LLMRequest

# ============ adapter 层：流式 tool_calls 聚合 ============

def _make_adapter() -> LiteLLMAdapter:
    config = LLMConfig(
        provider=LLMProvider.OPENAI,
        api_key="sk-test",
        model="qwen-test",
        base_url="http://probe.example/v1",
        timeout=10,
        max_tokens=256,
        temperature=0.1,
    )
    return LiteLLMAdapter(config)


def _make_request(tools=None) -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="decide")], tools=tools)


def _tool_call_delta(index, call_id, name, arguments):
    """构造一个 delta.tool_calls 元素（OpenAI 流式形态）。"""
    return SimpleNamespace(
        index=index,
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _stream_chunk(tool_calls=None, content=None, reasoning=None, finish_reason=None):
    delta = SimpleNamespace(
        content=content or "",
        reasoning_content=reasoning or "",
        thinking=None,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=None,
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _patch_acompletion(chunks, captured):
    async def _fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeStream(chunks)

    return patch("litellm.acompletion", _fake_acompletion)


@pytest.mark.asyncio
async def test_adapter_aggregates_streaming_tool_calls_arguments():
    """delta.tool_calls 按 index 聚合：id/name 首块到达，arguments 增量拼接，done 输出完整调用。"""
    adapter = _make_adapter()
    chunks = [
        _stream_chunk(tool_calls=[_tool_call_delta(0, "call_d1", "dispatch_agent", "")]),
        _stream_chunk(tool_calls=[_tool_call_delta(0, None, None, '{"agent": "analy')]),
        _stream_chunk(tool_calls=[_tool_call_delta(0, None, None, 'sis", "task": "深度审计"}')]),
        _stream_chunk(finish_reason="tool_calls"),
    ]
    captured = {}
    with _patch_acompletion(chunks, captured):
        out = [c async for c in adapter.stream_complete(
            _make_request(tools=[{"type": "function", "function": {"name": "dispatch_agent"}}])
        )]

    # tools 定义透传到出站请求
    assert captured.get("tools") is not None

    done = out[-1]
    assert done["type"] == "done"
    assert done["finish_reason"] == "tool_calls"
    assert done.get("tool_calls") == [
        {
            "id": "call_d1",
            "name": "dispatch_agent",
            "arguments": '{"agent": "analysis", "task": "深度审计"}',
        }
    ], f"tool_calls 必须聚合为完整调用，实际: {done.get('tool_calls')}"


@pytest.mark.asyncio
async def test_adapter_aggregates_multiple_tool_call_indices():
    """同一流中多个 tool_call（不同 index）按 index 排序聚合。"""
    adapter = _make_adapter()
    chunks = [
        _stream_chunk(tool_calls=[
            _tool_call_delta(0, "c0", "finish", ""),
            _tool_call_delta(1, "c1", "summarize", ""),
        ]),
        _stream_chunk(tool_calls=[
            _tool_call_delta(0, None, None, "{}"),
            _tool_call_delta(1, None, None, "{}"),
        ]),
        _stream_chunk(finish_reason="tool_calls"),
    ]
    captured = {}
    with _patch_acompletion(chunks, captured):
        out = [c async for c in adapter.stream_complete(
            _make_request(tools=[{"type": "function", "function": {"name": "x"}}])
        )]

    done = out[-1]
    calls = done["tool_calls"]
    assert [c["name"] for c in calls] == ["finish", "summarize"]
    assert [c["id"] for c in calls] == ["c0", "c1"]


@pytest.mark.asyncio
async def test_adapter_done_chunk_without_tool_calls_has_no_key():
    """纯文本流（无 tool_calls）的 done 块不携带 tool_calls 键（旧消费方兼容）。"""
    adapter = _make_adapter()
    chunks = [
        _stream_chunk(content="Thought: ok"),
        _stream_chunk(content=" done", finish_reason="stop"),
    ]
    with _patch_acompletion(chunks, {}):
        out = [c async for c in adapter.stream_complete(_make_request())]

    done = out[-1]
    assert done["type"] == "done"
    assert "tool_calls" not in done, "无 tool_calls 的流 done 不得携带 tool_calls 键"


# ============ base 层：stream_llm_call 透传 tools 并暴露 tool_calls ============

def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    return e


def _make_recon(stream_fn):
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
    return ReconAgent(
        llm_service=service,
        tools={},
        event_emitter=_make_emitter(),
    )


@pytest.mark.asyncio
async def test_stream_llm_call_passes_tools_and_exposes_tool_calls():
    """tools 参数透传至 chat_completion_stream；done 的 tool_calls 暴露到 _last_tool_calls。"""
    captured = {}

    async def _gen(messages=None, temperature=None, max_tokens=None, tools=None):
        captured["tools"] = tools
        yield {"type": "token", "kind": "content", "content": "ok",
               "accumulated": "ok", "accumulated_content": "ok", "accumulated_reasoning": ""}
        yield {
            "type": "done", "content": "ok", "reasoning": "", "accumulated": "ok",
            "usage": {"total_tokens": 5}, "finish_reason": "tool_calls",
            "tool_calls": [{"id": "c1", "name": "finish", "arguments": "{}"}],
        }

    agent = _make_recon(_gen)
    tool_defs = [{"type": "function", "function": {"name": "finish"}}]

    output, tokens = await agent.stream_llm_call(
        agent._conversation_history, tools=tool_defs
    )

    assert captured["tools"] is tool_defs, "tools 定义必须透传到 chat_completion_stream"
    assert agent._last_tool_calls == [{"id": "c1", "name": "finish", "arguments": "{}"}], (
        "done chunk 的 tool_calls 必须暴露到 agent._last_tool_calls"
    )
    assert output == "ok"
    assert tokens == 5


@pytest.mark.asyncio
async def test_stream_llm_call_resets_tool_calls_each_round():
    """每轮调用开始时 _last_tool_calls 重置：上一轮的 tool_calls 不得泄漏到文本轮。"""

    async def _gen_text(messages=None, temperature=None, max_tokens=None, tools=None):
        captured["tools"] = tools
        yield {"type": "token", "content": "混", "accumulated": "混"}
        yield {"type": "done", "content": "混合",
               "usage": {"total_tokens": 3}, "finish_reason": "stop"}

    captured = {}
    agent = _make_recon(_gen_text)
    # 上一轮残留（模拟）
    agent._last_tool_calls = [{"id": "stale", "name": "finish", "arguments": "{}"}]

    output, _ = await agent.stream_llm_call(agent._conversation_history)

    assert agent._last_tool_calls is None, "新一轮无 tool_calls 时必须重置为 None"
    assert captured["tools"] is None, "未传 tools 时下游收到 None"
    assert output == "混合"


# ============ Orchestrator：tools 定义与 tool_calls 映射 ============

def _make_bare_orchestrator():
    return OrchestratorAgent(llm_service=SimpleNamespace(), tools={})


def test_orchestrator_tool_defs_define_three_actions():
    """tools 定义为 dispatch_agent/finish/summarize 三个 OpenAI function。"""
    agent = _make_bare_orchestrator()

    defs = agent._build_orchestrator_tool_defs()

    assert [d["type"] for d in defs] == ["function", "function", "function"]
    funcs = {d["function"]["name"]: d["function"] for d in defs}
    assert set(funcs) == {"dispatch_agent", "finish", "summarize"}

    dispatch_params = funcs["dispatch_agent"]["parameters"]
    assert set(dispatch_params["properties"]) >= {"agent", "task", "context"}
    assert dispatch_params["required"] == ["agent", "task"]
    assert dispatch_params["properties"]["agent"]["enum"] == ["recon", "analysis", "verification"]

    # finish/summarize 无参数
    assert funcs["finish"]["parameters"]["type"] == "object"
    assert not funcs["finish"]["parameters"].get("required")
    assert funcs["summarize"]["parameters"]["type"] == "object"
    assert not funcs["summarize"]["parameters"].get("required")


def test_step_from_tool_calls_maps_dispatch_agent():
    agent = _make_bare_orchestrator()

    step = agent._step_from_tool_calls([
        {"id": "c1", "name": "dispatch_agent",
         "arguments": '{"agent": "verification", "task": "验证 SSRF", "context": "1 个发现"}'}
    ])

    assert isinstance(step, AgentStep)
    assert step.action == "dispatch_agent"
    assert step.action_input == {
        "agent": "verification", "task": "验证 SSRF", "context": "1 个发现"
    }


def test_step_from_tool_calls_maps_finish_and_summarize_with_no_args():
    agent = _make_bare_orchestrator()

    finish_step = agent._step_from_tool_calls(
        [{"id": "c2", "name": "finish", "arguments": "{}"}]
    )
    assert finish_step.action == "finish"
    assert finish_step.action_input == {}

    summarize_step = agent._step_from_tool_calls(
        [{"id": "c3", "name": "summarize", "arguments": ""}]
    )
    assert summarize_step.action == "summarize"
    assert summarize_step.action_input == {}


def test_step_from_tool_calls_broken_json_falls_back_to_empty_input():
    """arguments 非法 JSON（理论上由服务端 parser 保证，防御）：不抛错，action_input={}。"""
    agent = _make_bare_orchestrator()

    step = agent._step_from_tool_calls(
        [{"id": "c4", "name": "dispatch_agent", "arguments": "{broken json"}]
    )

    assert step.action == "dispatch_agent"
    assert step.action_input == {}, "坏 JSON 必须降级为空参数（由现有分支自愈），不得抛错"


def test_step_from_tool_calls_unknown_name_kept_for_self_healing():
    """未知函数名保留在 action 中，交给现有"未知操作"观察分支喂回模型自愈。"""
    agent = _make_bare_orchestrator()

    step = agent._step_from_tool_calls(
        [{"id": "c5", "name": "read_file", "arguments": "{}"}]
    )

    assert step.action == "read_file"


def test_step_from_tool_calls_empty_returns_none():
    agent = _make_bare_orchestrator()
    assert agent._step_from_tool_calls([]) is None
    assert agent._step_from_tool_calls(None) is None


# ============ 决策循环集成：tool_calls 分发与文本协议共存 ============

def _tool_call(name, arguments="{}"):
    return {"id": f"call_{name}", "name": name, "arguments": arguments}


def _make_orch(monkeypatch, caps=None):
    service = SimpleNamespace(backend_capabilities=caps)
    emitter = _make_emitter()
    agent = OrchestratorAgent(llm_service=service, tools={}, event_emitter=emitter)

    monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
    monkeypatch.setattr(
        agent,
        "_run_semgrep_prescan",
        AsyncMock(return_value={"findings": [], "hot_files": [], "scan_success": False}),
    )
    monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
    monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
    monkeypatch.setattr(agent, "check_messages", lambda: [])
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    # 覆盖率门禁为既有逻辑（有专属测试）：桩为充分覆盖，让 finish 直达收尾
    monkeypatch.setattr(
        agent, "_evaluate_current_coverage",
        lambda: SimpleNamespace(is_sufficient=True, covered_count=10, gaps=[]),
    )

    dispatch_mock = AsyncMock(return_value="dispatch-observation")
    monkeypatch.setattr(agent, "_dispatch_agent", dispatch_mock)
    summarize_mock = MagicMock(return_value="summary-observation")
    monkeypatch.setattr(agent, "_summarize_findings", summarize_mock)

    return agent, emitter, dispatch_mock, summarize_mock


def _install_stream(monkeypatch, agent, script):
    """安装 stream_llm_call 脚本：每轮返回 (output, tokens) 并设置 _last_tool_calls。

    返回 seen 列表，记录每轮调用收到的 tools 参数。
    """
    seen = []

    async def _fake_stream(messages, temperature=None, tools=None, **kwargs):
        idx = len(seen)
        seen.append({"tools": tools})
        round_spec = script[idx]
        agent._last_tool_calls = round_spec.get("tool_calls")
        return round_spec.get("output", ""), round_spec.get("tokens", 11)

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_fake_stream))
    return seen


def _emitted(emitter):
    events = []
    for call in emitter.emit.await_args_list:
        data = call.args[0]
        events.append((data.event_type, dict(data.metadata or {})))
    return events


@pytest.mark.asyncio
async def test_tools_injected_when_capability_available(monkeypatch):
    """能力 tools=True → 决策轮调用携带三个 tools 定义；tool_calls 响应直分发。"""
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent, emitter, dispatch_mock, _ = _make_orch(monkeypatch, caps=caps)
    seen = _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call(
            "dispatch_agent",
            '{"agent": "analysis", "task": "深度审计 D1 注入维度", "context": "初始轮"}'
        )]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"任务应成功收尾: {result.error}"
    assert seen[0]["tools"] is not None, "能力可用时必须传 tools"
    names = [t["function"]["name"] for t in seen[0]["tools"]]
    assert names == ["dispatch_agent", "finish", "summarize"]
    # 每轮都传（能力探测结果在任务内不变）
    assert seen[1]["tools"] is not None


@pytest.mark.asyncio
async def test_tool_calls_dispatch_agent_invokes_dispatch_with_params(monkeypatch):
    """tool_calls dispatch_agent → _dispatch_agent 收到解析后的参数，llm_action 事件等价发射。"""
    caps = BackendCapabilities(tools=True)
    agent, emitter, dispatch_mock, _ = _make_orch(monkeypatch, caps=caps)
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call(
            "dispatch_agent",
            '{"agent": "analysis", "task": "深度审计 D1", "context": "ctx"}'
        )]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success
    dispatch_mock.assert_awaited_once()
    params = dispatch_mock.await_args.args[0]
    assert params["agent"] == "analysis"
    assert params["task"] == "深度审计 D1"
    assert params["context"] == "ctx"

    events = _emitted(emitter)
    llm_actions = [md for et, md in events if et == "llm_action"]
    assert len(llm_actions) == 1, "tool_calls 形态必须发射与文本协议等价的 llm_action 事件"
    assert llm_actions[0]["action"] == "dispatch_agent"
    assert llm_actions[0]["action_input"]["agent"] == "analysis"

    # 决策事件同样发射（前端可见性等价）
    decisions = [md for et, md in events if et == "llm_decision"]
    assert any("analysis" in md["decision"] for md in decisions)

    # tool_calls 形态天然合法：不走文本格式重试计数
    assert getattr(agent, "_format_retry_count", 0) == 0

    # 多轮历史自洽：assistant 消息合成 ReAct 文本（下一轮模型/后端看到的历史与文本协议一致）
    assistant_msgs = [m for m in agent._conversation_history if m["role"] == "assistant"]
    assert any("Action: dispatch_agent" in m["content"] for m in assistant_msgs), (
        "tool_calls 轮的 assistant 历史必须合成 Action 文本，保证多轮历史自洽"
    )


@pytest.mark.asyncio
async def test_tool_calls_finish_triggers_finish_branch(monkeypatch):
    """tool_calls finish → 进入 finish 分支（门禁链被评估放行），发射完成决策/完成事件。"""
    caps = BackendCapabilities(tools=True)
    agent, emitter, dispatch_mock, _ = _make_orch(monkeypatch, caps=caps)
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    dispatch_mock.assert_not_awaited()
    events = _emitted(emitter)
    decisions = [md for et, md in events if et == "llm_decision"]
    assert any(md["decision"] == "完成审计" for md in decisions), (
        "finish 分支必须发射完成审计决策（门禁链通过后）"
    )
    assert any(et == "llm_complete" for et, _ in events), "finish 分支必须发射 llm_complete"


@pytest.mark.asyncio
async def test_tool_calls_summarize_returns_summary_observation(monkeypatch):
    """tool_calls summarize → _summarize_findings 被调，观察结果入历史并发 llm_observation。"""
    caps = BackendCapabilities(tools=True)
    agent, emitter, _, summarize_mock = _make_orch(monkeypatch, caps=caps)
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("summarize", "{}")]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success
    summarize_mock.assert_called_once()
    events = _emitted(emitter)
    assert any(et == "llm_observation" for et, _ in events), (
        "summarize 分支必须发射 llm_observation（与文本协议等价）"
    )
    # Observation 入历史（循环末尾统一追加）
    user_msgs = [m for m in agent._conversation_history if m["role"] == "user"]
    assert any("summary-observation" in m["content"] for m in user_msgs)


@pytest.mark.asyncio
async def test_no_tools_when_capability_unavailable_and_text_path_works(monkeypatch):
    """能力 tools=False → 不传 tools；模型文本 Action 响应走现有 _parse_llm_response 路径。"""
    caps = BackendCapabilities(tools=False, guided_json=False)
    agent, emitter, dispatch_mock, _ = _make_orch(monkeypatch, caps=caps)
    seen = _install_stream(monkeypatch, agent, [
        {"output": "Thought: 审计已完成\nAction: finish\nAction Input: {}", "tokens": 9},
    ])
    parse_spy = MagicMock(wraps=agent._parse_llm_response)
    monkeypatch.setattr(agent, "_parse_llm_response", parse_spy)

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"文本协议路径应收尾成功: {result.error}"
    assert seen[0]["tools"] is None, "能力不可用时不得传 tools"
    parse_spy.assert_called(), "文本响应必须经过 _parse_llm_response 解析"
    dispatch_mock.assert_not_awaited()
    events = _emitted(emitter)
    assert any(md.get("decision") == "完成审计"
               for et, md in events if et == "llm_decision")


@pytest.mark.asyncio
async def test_tool_calls_path_does_not_invoke_text_parser(monkeypatch):
    """tool_calls 响应不得再走文本正则解析（双形态互斥）。"""
    caps = BackendCapabilities(tools=True)
    agent, _, dispatch_mock, _ = _make_orch(monkeypatch, caps=caps)
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call(
            "dispatch_agent",
            '{"agent": "recon", "task": "收集项目结构", "context": ""}'
        )]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])
    parse_spy = MagicMock(wraps=agent._parse_llm_response)
    monkeypatch.setattr(agent, "_parse_llm_response", parse_spy)

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success
    parse_spy.assert_not_called(), "tool_calls 形态不得走文本解析"
    params = dispatch_mock.await_args.args[0]
    assert params["agent"] == "recon"
