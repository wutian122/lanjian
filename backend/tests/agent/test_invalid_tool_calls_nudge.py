"""
sandbox-verification-hard-gate Task 21：空/无效 tool_calls 强 nudge 自愈

R2 服务端实测模型在 Orchestrator tools 协议下的三种退化形态：
1. 空 name（前端显示"决策: 未知操作 (未知操作: ，可用操作: ...)"）；
2. dispatch_agent 空参数（"调度 unknown Agent (任务: )"——agent/task 空，
   现状会真实触发一次子 Agent 调度失败）；
3. 坏 JSON arguments（_step_from_tool_calls 吞成空参数，模型不知情）。
现状自愈喂泛化 observation（且"未知操作"分支存在 step.observation 未赋值、
历史喂回 "Observation:\\nNone" 的缺陷），R2 实测自愈后模型继续退化。

Task 21 行为：
- 分类无效形态 → 强化 observation：空/未知 name 重喂三函数 schema；
  dispatch_agent 空参（agent 缺失/非枚举 或 task 空）喂参数要求；坏 JSON 喂 JSON 要求；
- 连续 ≥2 次无效 → observation 追加协议降级 nudge（引导 Thought:/Action:/
  Action Input: 文本格式；文本解析路径原样保留，降级后可承接）；
- _invalid_tool_calls_count 连续计数，有效决策轮复位；与 _empty_retry_count
  （连续 5 次空响应停止）相互独立、互不触碰；
- 强化 observation 走 llm_observation 事件（前端可见）并以 Observation 入历史；
- 无效 dispatch_agent 在分发前拦截，不触发真实调度。

覆盖：
Part 1 分类与 nudge 文案（_classify_invalid_tool_call / _invalid_tool_call_nudge）
Part 2 run 循环集成：三形态 observation、降级 nudge 阈值、有效轮复位、
       与空响应计数独立、文本协议降级承接、空参不触发真实调度
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.structured_output import BackendCapabilities


# ============ 公共工厂（照搬 test_orchestrator_tools_protocol 风格） ============

def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    return e


def _tool_call(name, arguments="{}"):
    return {"id": f"call_{name or 'empty'}", "name": name, "arguments": arguments}


def _make_bare_orchestrator():
    return OrchestratorAgent(llm_service=SimpleNamespace(), tools={})


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
    # 覆盖率门禁桩为充分覆盖，让 finish 直达收尾
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
    """安装 stream_llm_call 脚本：每轮按 spec 设置 _last_tool_calls 并返回正文。

    spec 形态：
    - {"tool_calls": [...]}：原生工具轮（正文空）；
    - {"output": "Thought: ..."}：文本协议轮（无 tool_calls）；
    - {"output": "", "tool_calls": None}：空响应轮。
    返回 snapshots 列表，记录每轮 LLM 调用入口处两个计数器的快照。
    """
    snapshots = []

    async def _fake_stream(messages, temperature=None, tools=None, **kwargs):
        idx = len(snapshots)
        snapshots.append({
            "invalid_before": getattr(agent, "_invalid_tool_calls_count", 0),
            "empty_before": getattr(agent, "_empty_retry_count", 0),
        })
        round_spec = script[idx]
        agent._last_tool_calls = round_spec.get("tool_calls")
        return round_spec.get("output", ""), round_spec.get("tokens", 11)

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_fake_stream))
    return snapshots


def _emitted(emitter):
    events = []
    for call in emitter.emit.await_args_list:
        data = call.args[0]
        events.append((data.event_type, dict(data.metadata or {})))
    return events


def _observations(agent):
    """历史中以 Observation: 前缀喂回模型的观察文本（nudge 入历史的载体）。"""
    return [
        m["content"][len("Observation:\n"):]
        for m in agent._conversation_history
        if m["role"] == "user" and m["content"].startswith("Observation:\n")
    ]


# ============ Part 1：分类与 nudge 文案（纯同步单元） ============

def test_classify_missing_or_blank_name():
    """空 name / name 缺失 → missing_name。"""
    agent = _make_bare_orchestrator()
    for calls in (
        [_tool_call("", "{}")],
        [{"id": "c1", "name": None, "arguments": "{}"}],
        [{"id": "c1", "arguments": "{}"}],
    ):
        step = agent._step_from_tool_calls(calls)
        kind = agent._classify_invalid_tool_call(calls, step)
        assert kind == "missing_name", f"空 name 必须判 missing_name，实际: {kind}"


def test_classify_unknown_name():
    """非空但不在三动作集合的函数名 → unknown_name（与空 name 同喂 schema 重喂）。"""
    agent = _make_bare_orchestrator()
    calls = [_tool_call("read_file", '{"path": "x"}')]
    step = agent._step_from_tool_calls(calls)

    assert agent._classify_invalid_tool_call(calls, step) == "unknown_name"


def test_classify_broken_json():
    """arguments 非空但非法 JSON / 非 JSON 对象 → bad_json（优先于参数校验）。"""
    agent = _make_bare_orchestrator()
    for raw in ("{broken json", "[1, 2]"):
        calls = [_tool_call("dispatch_agent", raw)]
        step = agent._step_from_tool_calls(calls)
        kind = agent._classify_invalid_tool_call(calls, step)
        assert kind == "bad_json", f"非法 JSON（{raw!r}）必须判 bad_json，实际: {kind}"


def test_classify_dispatch_agent_empty_or_invalid_args():
    """dispatch_agent 的 agent 缺失/空/非枚举，或 task 缺失/空 → empty_dispatch_args。"""
    agent = _make_bare_orchestrator()
    for raw in (
        "{}",
        '{"agent": "", "task": "审计"}',
        '{"agent": "hacker", "task": "审计"}',
        '{"agent": "analysis"}',
        '{"agent": "analysis", "task": "   "}',
        '{"agent": "analysis", "task": null}',
    ):
        calls = [_tool_call("dispatch_agent", raw)]
        step = agent._step_from_tool_calls(calls)
        kind = agent._classify_invalid_tool_call(calls, step)
        assert kind == "empty_dispatch_args", f"参数 {raw!r} 必须判 empty_dispatch_args，实际: {kind}"


def test_classify_valid_tool_calls_return_none():
    """合法调用返回 None：dispatch 全参（含 agent 大小写不敏感）、finish/summarize 无参、
    arguments 直接为 dict、空字符串 arguments。"""
    agent = _make_bare_orchestrator()
    valid = [
        [_tool_call("dispatch_agent", '{"agent": "analysis", "task": "深度审计 D1", "context": "ctx"}')],
        [_tool_call("dispatch_agent", '{"agent": "Analysis", "task": "审计"}')],
        [_tool_call("finish", "{}")],
        [_tool_call("summarize", "")],
        [{"id": "c9", "name": "dispatch_agent",
          "arguments": {"agent": "recon", "task": "信息收集"}}],
    ]
    for calls in valid:
        step = agent._step_from_tool_calls(calls)
        kind = agent._classify_invalid_tool_call(calls, step)
        assert kind is None, f"合法调用不得判无效: {calls[0]['name']} / {calls[0]['arguments']!r}"


def test_classify_broken_json_on_finish_also_flagged():
    """finish/summarize 的坏 JSON 同样判 bad_json——损坏输出是退化信号，统一 nudge 重输出。"""
    agent = _make_bare_orchestrator()
    calls = [_tool_call("finish", "{oops")]
    step = agent._step_from_tool_calls(calls)

    assert agent._classify_invalid_tool_call(calls, step) == "bad_json"


def test_nudge_texts_per_kind():
    """三类 observation 文案手推断言：schema 重喂 / 参数要求 / JSON 要求。"""
    agent = _make_bare_orchestrator()

    schema_nudge = agent._invalid_tool_call_nudge("missing_name", 1)
    assert "缺少有效的函数名" in schema_nudge
    assert "可用操作与参数 schema" in schema_nudge
    assert "dispatch_agent" in schema_nudge
    assert "summarize()" in schema_nudge
    assert "finish()" in schema_nudge
    assert "'recon'|'analysis'|'verification'" in schema_nudge
    assert "agent 与 task 参数必须非空" in schema_nudge
    # unknown_name 同文案
    assert agent._invalid_tool_call_nudge("unknown_name", 1) == schema_nudge

    args_nudge = agent._invalid_tool_call_nudge("empty_dispatch_args", 1)
    assert "agent 参数缺失或无效" in args_nudge
    assert "recon/analysis/verification" in args_nudge
    assert "task 必须为非空" in args_nudge

    json_nudge = agent._invalid_tool_call_nudge("bad_json", 1)
    assert "不是合法 JSON" in json_nudge

    # 第 1 次无效不带协议降级 nudge
    assert "改用文本格式" not in schema_nudge
    assert "改用文本格式" not in args_nudge
    assert "改用文本格式" not in json_nudge


def test_fallback_nudge_appended_from_second_consecutive():
    """连续计数 ≥2 时追加协议降级 nudge，且计数动态准确。"""
    agent = _make_bare_orchestrator()

    first = agent._invalid_tool_call_nudge("missing_name", 1)
    second = agent._invalid_tool_call_nudge("missing_name", 2)
    third = agent._invalid_tool_call_nudge("bad_json", 3)

    assert "改用文本格式" not in first
    assert "已连续 2 次工具调用无效" in second
    assert "Thought:" in second and "Action Input:" in second
    assert "dispatch_agent 或 finish" in second
    assert "已连续 3 次工具调用无效" in third
    # 降级 nudge 追加在分类 observation 之后（不替换）
    assert "缺少有效的函数名" in second
    assert "不是合法 JSON" in third


# ============ Part 2：run 循环集成 ============

def _caps_tools():
    return BackendCapabilities(tools=True, guided_json=False)


@pytest.mark.asyncio
async def test_empty_name_tool_call_gets_schema_observation(monkeypatch):
    """空 name tool_calls：不进未知操作/分发，喂 schema 重喂 observation（事件+历史），
    下一轮 finish 正常收尾。"""
    caps = _caps_tools()
    agent, emitter, dispatch_mock, _ = _make_orch(monkeypatch, caps=caps)
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("", "{}")]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    obs = _observations(agent)
    assert len(obs) == 1, f"无效轮必须喂回 1 条 observation，实际: {len(obs)}"
    assert "缺少有效的函数名" in obs[0]
    assert "可用操作与参数 schema" in obs[0]
    # llm_observation 事件发射（前端可见）
    events = _emitted(emitter)
    llm_obs = [md for et, md in events if et == "llm_observation"]
    assert any("缺少有效的函数名" in md.get("observation", "") for md in llm_obs), (
        "强化 observation 必须走 llm_observation 事件"
    )
    # 无效轮不得触发真实调度，也不得发"调度 unknown Agent"误导决策
    dispatch_mock.assert_not_awaited()
    decisions = [md.get("decision", "") for et, md in events if et == "llm_decision"]
    assert any("工具调用无效" in d for d in decisions)
    assert not any("unknown" in d for d in decisions), "空 name 不得再显示'调度 unknown Agent'"


@pytest.mark.asyncio
async def test_unknown_name_tool_call_gets_schema_observation(monkeypatch):
    """未知函数名（read_file）：同喂 schema 重喂 observation，finish 收尾。"""
    agent, _, dispatch_mock, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("read_file", '{"path": "a.py"}')]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    obs = _observations(agent)
    assert len(obs) == 1
    assert "缺少有效的函数名" in obs[0]
    assert "dispatch_agent" in obs[0] and "summarize" in obs[0] and "finish" in obs[0]
    dispatch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_dispatch_args_blocked_before_real_dispatch(monkeypatch):
    """dispatch_agent 空参（agent 缺失）：分发前拦截，_dispatch_agent 不被调用，
    喂参数要求 observation；下一轮 finish 收尾。"""
    agent, emitter, dispatch_mock, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("dispatch_agent", "{}")]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    dispatch_mock.assert_not_awaited(), "空参 dispatch_agent 必须在分发前拦截，不得真实调度"
    obs = _observations(agent)
    assert len(obs) == 1
    assert "agent 参数缺失或无效" in obs[0]
    assert "recon/analysis/verification" in obs[0]
    events = _emitted(emitter)
    llm_obs = [md for et, md in events if et == "llm_observation"]
    assert any("agent 参数缺失或无效" in md.get("observation", "") for md in llm_obs)


@pytest.mark.asyncio
async def test_dispatch_with_blank_task_blocked_before_real_dispatch(monkeypatch):
    """agent 合法但 task 为空：同样拦截（避免子 Agent 跑空任务），喂参数要求 observation。"""
    agent, _, dispatch_mock, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("dispatch_agent", '{"agent": "analysis", "task": ""}')]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    dispatch_mock.assert_not_awaited(), "task 空的 dispatch_agent 必须拦截，不得调度空任务"
    obs = _observations(agent)
    assert len(obs) == 1
    assert "task 必须为非空" in obs[0]


@pytest.mark.asyncio
async def test_broken_json_gets_json_observation(monkeypatch):
    """坏 JSON arguments：喂 JSON 要求 observation（模型知情重输出），finish 收尾。"""
    agent, _, dispatch_mock, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("dispatch_agent", "{broken json")]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    dispatch_mock.assert_not_awaited(), "坏 JSON 降级为空参后必须拦截，不得真实调度"
    obs = _observations(agent)
    assert len(obs) == 1
    assert "不是合法 JSON" in obs[0]


@pytest.mark.asyncio
async def test_second_consecutive_invalid_appends_fallback_nudge(monkeypatch):
    """连续 2 次无效：第 1 条 observation 无降级引导，第 2 条追加协议降级 nudge；
    第 3 轮 finish 有效，计数复位。"""
    agent, _, _, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("", "{}")]},
        {"tool_calls": [_tool_call("read_file", "{}")]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    obs = _observations(agent)
    assert len(obs) == 2, f"两轮无效各 1 条 observation，实际: {len(obs)}"
    assert "改用文本格式" not in obs[0], "第 1 次无效不得带协议降级 nudge"
    assert "缺少有效的函数名" in obs[0]
    assert "改用文本格式" in obs[1], "第 2 次连续无效必须追加协议降级 nudge"
    assert "已连续 2 次工具调用无效" in obs[1]
    assert "Thought:" in obs[1] and "Action Input:" in obs[1]
    assert agent._invalid_tool_calls_count == 0, "finish 有效轮必须复位连续无效计数"


@pytest.mark.asyncio
async def test_valid_round_resets_invalid_count(monkeypatch):
    """有效决策轮复位计数：无效 → 有效 dispatch → 再无效，第 2 次无效是新计数的
    第 1 次（不带降级 nudge），证明中间有效轮复位过。"""
    agent, _, dispatch_mock, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("", "{}")]},
        {"tool_calls": [_tool_call(
            "dispatch_agent", '{"agent": "analysis", "task": "深度审计 D1", "context": ""}')]},
        {"tool_calls": [_tool_call("", "{}")]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"任务应收尾成功: {result.error}"
    dispatch_mock.assert_awaited_once(), "中间有效 dispatch 必须真实执行"
    obs = _observations(agent)
    # 两条无效 observation（轮1、轮3）+ 一条 dispatch observation（轮2）
    invalid_obs = [o for o in obs if "系统提示" in o]
    assert len(invalid_obs) == 2
    assert all("改用文本格式" not in o for o in invalid_obs), (
        "有效轮复位后，再次无效是新序列第 1 次，不得带降级 nudge"
    )
    assert agent._invalid_tool_calls_count == 0


@pytest.mark.asyncio
async def test_invalid_count_independent_of_empty_retry_count(monkeypatch):
    """两个计数相互独立：无效 tool_calls 轮不增空响应计数；空响应轮不增/不复位
    无效计数；有效 finish 轮双双复位。"""
    monkeypatch.setattr(
        "app.services.agent.agents.orchestrator.asyncio.sleep", AsyncMock(),
    )
    agent, _, _, _ = _make_orch(monkeypatch, caps=_caps_tools())
    snapshots = _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("", "{}")]},      # 轮1：无效 tool_calls
        {"output": "", "tool_calls": None},           # 轮2：空响应
        {"tool_calls": [_tool_call("finish", "{}")]},  # 轮3：有效 finish
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    # 轮2（空响应）入口：无效计数已被轮1 置 1，空响应计数尚未增长
    assert snapshots[1]["invalid_before"] == 1, "无效 tool_calls 轮必须置无效计数为 1"
    assert snapshots[1]["empty_before"] == 0, "tool_calls 轮正文空属正常，不得增空响应计数"
    # 轮3（finish）入口：空响应轮把空响应计数置 1，但未触碰无效计数
    assert snapshots[2]["invalid_before"] == 1, "空响应轮不得复位/递增无效 tool_calls 计数"
    assert snapshots[2]["empty_before"] == 1
    # 有效轮双双复位
    assert agent._invalid_tool_calls_count == 0
    assert agent._empty_retry_count == 0
    # 空响应重试提示照常注入（空响应路径不受影响）
    user_msgs = [m["content"] for m in agent._conversation_history if m["role"] == "user"]
    assert any("收到空响应" in c for c in user_msgs)


@pytest.mark.asyncio
async def test_text_protocol_takes_over_after_fallback_nudge(monkeypatch):
    """协议降级承接：连续无效引导文本格式后，模型改输出 Thought:/Action: 文本轮
    （无 tool_calls）→ 走 _parse_llm_response 正常分发 finish，计数复位。"""
    agent, _, dispatch_mock, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("", "{}")]},
        {"tool_calls": [_tool_call("", "{}")]},
        {"output": "Thought: 审计已完成，直接收尾\nAction: finish\nAction Input: {}",
         "tokens": 9},
    ])
    parse_spy = MagicMock(wraps=agent._parse_llm_response)
    monkeypatch.setattr(agent, "_parse_llm_response", parse_spy)

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"文本协议 finish 应收尾成功: {result.error}"
    parse_spy.assert_called(), "降级后的文本响应必须由文本解析路径承接"
    dispatch_mock.assert_not_awaited()
    obs = _observations(agent)
    assert any("改用文本格式" in o for o in obs), "第 2 次无效必须给出协议降级引导"
    assert agent._invalid_tool_calls_count == 0, "文本协议有效轮同样复位无效计数"


@pytest.mark.asyncio
async def test_invalid_step_recorded_with_nudge_observation(monkeypatch):
    """无效步仍入 steps 台账，且 observation 为强 nudge 文本（而非 None）。"""
    agent, _, _, _ = _make_orch(monkeypatch, caps=_caps_tools())
    _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("", "{}")]},
        {"tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success
    steps = result.data.get("steps", [])
    invalid_steps = [s for s in steps if s["action"] == ""]
    assert len(invalid_steps) == 1
    assert invalid_steps[0]["observation"] and "系统提示" in invalid_steps[0]["observation"], (
        "无效步的 observation 必须是强 nudge 文本，不得为 None（旧未知操作分支缺陷）"
    )
