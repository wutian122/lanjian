"""
structured-output-protocol Task 8：Analysis/Verification Final Answer 即工具调用

能力探测 tools=True 时，Analysis/Verification 决策轮携带 submit_findings 工具
定义（参数 schema = 现有 Final Answer 契约字段）；模型调用该工具即宣布完成，
function.arguments 由服务端 tool-call-parser 保证合法 JSON，直接 json.loads 作为
final_answer（不再依赖 "Final Answer: 文本 + json-repair"，文本路径降级保留）。
强制总结轮（Analysis._run_forced_summary）在 guided_json 可用时注入 findings
schema 的 response_format（纯 JSON 一次性输出，无工具调用）。

覆盖：
- submit_findings 工具定义形态（Analysis/Verification 各自 schema 契约字段）
- tool_calls submit_findings → is_final + final_answer（不走 json-repair）
- arguments 坏 JSON / 未知函数名 / 空 / 非对象 → 降级文本路径（返回 None）
- base.stream_llm_call 透传 response_format
- run 循环集成：tools 注入、空正文不误判空响应、历史合成、事件流等价、
  能力 False 不传 tools、文本 Final Answer 路径回归、坏 arguments 降级自愈
- 强制总结轮 guided response_format 注入；主循环中间轮不注入 response_format
- Verification 沙箱门禁在 tool_calls 形态下同构生效（无证据拒绝 / skip_reason 放行）
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.agents.analysis import AnalysisAgent
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.agents.verification import VerificationAgent
from app.services.agent.structured_output import BackendCapabilities

# ============ 公共构造辅助 ============

_TIMEOUT_CONFIG = {
    "llm_first_token_timeout": 30,
    "llm_stream_timeout": 60,
    "agent_timeout": 1800,
    "sub_agent_timeout": 600,
    "tool_timeout": 60,
}


def _make_emitter():
    e = MagicMock()
    # BaseAgent 的部分 emit_* 直接 await emitter 的同名方法（emit_finding 等），
    # 事件总线方法为 emit；两者都需 AsyncMock
    for name in [
        "emit", "emit_info", "emit_warning", "emit_error", "emit_thinking",
        "emit_tool_call", "emit_tool_result", "emit_finding", "emit_progress",
        "emit_phase_start", "emit_phase_complete", "emit_task_complete",
    ]:
        setattr(e, name, AsyncMock())
    return e


def _make_service(caps):
    """caps=None 模拟"未探测"；否则挂 BackendCapabilities。"""
    service = MagicMock()
    service.backend_capabilities = caps
    service.get_agent_timeout_config = MagicMock(return_value=_TIMEOUT_CONFIG)
    service.config = SimpleNamespace(max_tokens=8192)
    return service


def _tool_call(name, arguments="{}"):
    return {"id": f"call_{name}", "name": name, "arguments": arguments}


def _install_stream(monkeypatch, agent, script):
    """安装 stream_llm_call 脚本：每轮返回 (output, tokens) 并设置 _last_tool_calls。

    返回 seen 列表，记录每轮调用收到的 tools / response_format 参数。
    """
    seen = []

    async def _fake_stream(messages, temperature=None, tools=None, response_format=None, **kwargs):
        idx = len(seen)
        seen.append({"tools": tools, "response_format": response_format})
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


# ============ Analysis 契约 payload ============

ANALYSIS_PAYLOAD = {
    "summary": "发现 1 个 SQL 注入",
    "findings": [
        {
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "title": "SQL 注入漏洞",
            "description": "f-string 直接拼接用户输入到 SQL 查询",
            "file_path": "src/sql_vuln.py",
            "line_start": 6,
            "code_snippet": "query = f\"SELECT * FROM users WHERE id = '{user_id}'\"",
            "source": "user_id 请求参数",
            "sink": "cursor.execute",
            "suggestion": "使用参数化查询",
            "confidence": 0.9,
            "needs_verification": True,
        }
    ],
}

VERIFICATION_PAYLOAD = {
    "summary": {"total": 1, "confirmed": 1, "likely": 0, "false_positive": 0},
    "findings": [
        {
            "file_path": "src/sql_vuln.py",
            "line_start": 6,
            "vulnerability_type": "sql_injection",
            "severity": "high",
            "title": "SQL 注入漏洞",
            "description": "f-string 拼接查询",
            "code_snippet": "query = f\"SELECT * FROM users WHERE id = '{user_id}'\"",
            "verdict": "confirmed",
            "confidence": 0.95,
            "is_verified": True,
            "verification_method": "沙箱执行 PoC",
            "verification_details": "PoC 触发延时，漏洞确认",
            "sandbox_skip_reason": "沙箱缺少数据库服务，无法动态复现",
            "poc": {
                "description": "时间盲注 PoC",
                "steps": ["构造延时 payload", "观察响应时间"],
                "payload": "python3 -c \"import time; time.sleep(0.01)\"",
                "harness_code": "",
            },
            "impact": "可拖库",
            "recommendation": "参数化查询",
            "sandbox_attempts": [],
        }
    ],
}

VERIFICATION_INPUT_FINDING = {
    "vulnerability_type": "sql_injection",
    "severity": "high",
    "title": "SQL 注入漏洞",
    "description": "f-string 拼接查询",
    "file_path": "src/sql_vuln.py",
    "line_start": 6,
    "code_snippet": "query = f\"SELECT * FROM users WHERE id = '{user_id}'\"",
    "needs_verification": True,
}


# ============ 工具定义形态 ============

def test_analysis_submit_findings_tool_def_matches_contract():
    """Analysis 工具定义：submit_findings 参数 schema 覆盖 Final Answer 契约全字段。"""
    agent = AnalysisAgent(llm_service=_make_service(None), tools={}, event_emitter=_make_emitter())

    tool_def = agent._build_submit_findings_tool_def()

    assert tool_def["type"] == "function"
    fn = tool_def["function"]
    assert fn["name"] == "submit_findings"
    assert fn["description"]

    params = fn["parameters"]
    assert params["type"] == "object"
    assert set(params["required"]) >= {"findings", "summary"}
    assert params["properties"]["summary"]["type"] == "string"

    item_props = params["properties"]["findings"]["items"]["properties"]
    for field in [
        "vulnerability_type", "severity", "title", "description", "file_path",
        "line_start", "code_snippet", "source", "sink", "suggestion",
        "confidence", "needs_verification",
    ]:
        assert field in item_props, f"findings item 缺少契约字段: {field}"

    assert item_props["line_start"]["type"] == "integer"
    assert item_props["confidence"]["type"] == "number"
    assert item_props["needs_verification"]["type"] == "boolean"
    assert set(item_props["severity"]["enum"]) >= {"critical", "high", "medium", "low"}
    assert "sql_injection" in item_props["vulnerability_type"]["enum"]
    assert "xss" in item_props["vulnerability_type"]["enum"]


def test_verification_submit_findings_tool_def_matches_contract():
    """Verification 工具定义：参数 schema 覆盖验证 Final Answer 契约（verdict/poc/skip_reason/summary 对象）。"""
    agent = VerificationAgent(llm_service=_make_service(None), tools={}, event_emitter=_make_emitter())

    tool_def = agent._build_submit_findings_tool_def()

    assert tool_def["type"] == "function"
    fn = tool_def["function"]
    assert fn["name"] == "submit_findings"
    assert fn["description"]

    params = fn["parameters"]
    assert set(params["required"]) >= {"findings", "summary"}

    # Verification 的 summary 是统计对象（Analysis 是字符串）
    summary_schema = params["properties"]["summary"]
    assert summary_schema["type"] == "object"
    assert set(summary_schema["properties"]) >= {"total", "confirmed", "likely", "false_positive"}

    item_props = params["properties"]["findings"]["items"]["properties"]
    for field in [
        "file_path", "line_start", "verdict", "confidence", "is_verified",
        "verification_method", "verification_details", "poc", "impact",
        "recommendation", "sandbox_skip_reason", "sandbox_attempts",
    ]:
        assert field in item_props, f"verification findings item 缺少契约字段: {field}"

    assert item_props["line_start"]["type"] == "integer"
    assert item_props["is_verified"]["type"] == "boolean"
    assert item_props["poc"]["type"] == "object"
    assert item_props["sandbox_attempts"]["type"] == "array"
    assert set(item_props["verdict"]["enum"]) >= {
        "confirmed", "false_positive", "not_reproducible", "needs_context",
    }
    # file_path/line_start 为验证报告强制字段（与输入发现逐字匹配）
    item_required = params["properties"]["findings"]["items"].get("required", [])
    assert "file_path" in item_required
    assert "line_start" in item_required


# ============ tool_calls → Final 步骤映射 ============

def test_analysis_final_step_from_submit_findings_arguments():
    """submit_findings 合法 arguments → is_final 步骤，final_answer 即解析后的 JSON。"""
    agent = AnalysisAgent(llm_service=_make_service(None), tools={}, event_emitter=_make_emitter())

    step = agent._final_step_from_tool_calls([
        _tool_call("submit_findings", json.dumps(ANALYSIS_PAYLOAD, ensure_ascii=False))
    ])

    assert step is not None
    assert step.is_final is True
    assert step.action is None
    assert step.final_answer["summary"] == "发现 1 个 SQL 注入"
    assert len(step.final_answer["findings"]) == 1
    assert step.final_answer["findings"][0]["title"] == "SQL 注入漏洞"


def test_analysis_final_step_accepts_dict_arguments_and_filters_non_dict_findings():
    """arguments 为 dict 形态同样接受；findings 中的非字典项被过滤（与文本路径同构）。"""
    agent = AnalysisAgent(llm_service=_make_service(None), tools={}, event_emitter=_make_emitter())

    payload = {"summary": "s", "findings": [{"title": "真实发现"}, "垃圾字符串", 42]}
    step = agent._final_step_from_tool_calls([_tool_call("submit_findings", payload)])

    assert step is not None and step.is_final
    assert step.final_answer["findings"] == [{"title": "真实发现"}]


def test_analysis_final_step_broken_json_returns_none_for_text_fallback():
    """arguments 坏 JSON（理论上由服务端 parser 保证，防御）→ None，调用方降级文本路径。"""
    agent = AnalysisAgent(llm_service=_make_service(None), tools={}, event_emitter=_make_emitter())

    assert agent._final_step_from_tool_calls([_tool_call("submit_findings", "{broken json")]) is None
    # 空 arguments / 非对象 JSON / 未知函数名 / 空列表 → None
    assert agent._final_step_from_tool_calls([_tool_call("submit_findings", "")]) is None
    assert agent._final_step_from_tool_calls([_tool_call("submit_findings", "[1, 2, 3]")]) is None
    assert agent._final_step_from_tool_calls([_tool_call("read_file", '{"file_path": "a.py"}')]) is None
    assert agent._final_step_from_tool_calls([]) is None
    assert agent._final_step_from_tool_calls(None) is None


def test_verification_final_step_from_submit_findings_arguments():
    """Verification 版：submit_findings arguments → is_final，verdict/skip_reason 等字段原样保留。"""
    agent = VerificationAgent(llm_service=_make_service(None), tools={}, event_emitter=_make_emitter())

    step = agent._final_step_from_tool_calls([
        _tool_call("submit_findings", json.dumps(VERIFICATION_PAYLOAD, ensure_ascii=False))
    ])

    assert step is not None and step.is_final
    finding = step.final_answer["findings"][0]
    assert finding["verdict"] == "confirmed"
    assert finding["sandbox_skip_reason"]
    assert step.final_answer["summary"]["total"] == 1


def test_verification_final_step_broken_json_returns_none():
    agent = VerificationAgent(llm_service=_make_service(None), tools={}, event_emitter=_make_emitter())

    assert agent._final_step_from_tool_calls([_tool_call("submit_findings", "{broken")]) is None
    assert agent._final_step_from_tool_calls([_tool_call("sandbox_exec", "{}")]) is None
    assert agent._final_step_from_tool_calls(None) is None


# ============ base 层：response_format 透传 ============

@pytest.mark.asyncio
async def test_stream_llm_call_passes_response_format():
    """response_format 参数透传至 chat_completion_stream（强制总结轮 guided 注入依赖）。"""
    captured = {}

    async def _gen(messages=None, temperature=None, max_tokens=None, tools=None, response_format=None):
        captured["response_format"] = response_format
        yield {"type": "token", "kind": "content", "content": "{}",
               "accumulated": "{}", "accumulated_content": "{}", "accumulated_reasoning": ""}
        yield {"type": "done", "content": "{}", "reasoning": "",
               "usage": {"total_tokens": 3}, "finish_reason": "stop"}

    service = MagicMock()
    service.get_agent_timeout_config = MagicMock(return_value=_TIMEOUT_CONFIG)
    service.config = SimpleNamespace(max_tokens=8192)
    service.chat_completion_stream = _gen
    agent = ReconAgent(llm_service=service, tools={}, event_emitter=_make_emitter())

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "analysis_findings", "schema": {"type": "object"}},
    }
    await agent.stream_llm_call(agent._conversation_history, response_format=response_format)

    assert captured["response_format"] is response_format, "response_format 必须透传到 chat_completion_stream"


# ============ Analysis run 循环集成 ============

def _make_analysis(monkeypatch, caps):
    agent = AnalysisAgent(llm_service=_make_service(caps), tools={}, event_emitter=_make_emitter())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    return agent


@pytest.mark.asyncio
async def test_analysis_tools_injected_and_submit_findings_finalizes(monkeypatch):
    """能力 tools=True → 每轮携带 submit_findings 定义；tool_calls 响应即 Final Answer。

    本轮正文为空（tool_calls 形态正常表现）：不得误判空响应；findings 直接来自
    arguments（不经 json-repair）；首轮即可交卷（"禁止无工具直接 Final"仅为提示词
    约束，submit_findings 本身就是工具调用，无代码门禁会误拦）。
    """
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_analysis(monkeypatch, caps)
    emitter = agent.event_emitter
    seen = _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("submit_findings", json.dumps(ANALYSIS_PAYLOAD, ensure_ascii=False))],
         "output": ""},
    ])

    # json-repair 间谍：tool_calls 路径不得调用 AgentJsonParser.parse
    from app.services.agent.agents import analysis as analysis_mod
    parse_calls = []
    real_parse = analysis_mod.AgentJsonParser.parse

    def _spy_parse(text, default=None):
        parse_calls.append(text)
        return real_parse(text, default=default)

    monkeypatch.setattr(analysis_mod.AgentJsonParser, "parse", staticmethod(_spy_parse))

    result = await agent.run({"project_info": {"name": "p"}, "config": {}, "previous_results": {}})

    assert result.success, f"submit_findings 应收尾成功: {result.error}"
    assert seen[0]["tools"] is not None, "能力可用时必须传 tools"
    assert [t["function"]["name"] for t in seen[0]["tools"]] == ["submit_findings"]
    # 主循环不注入 response_format（中间轮/工具轮约束由 tools 参数 schema 承担）
    assert seen[0]["response_format"] is None

    findings = result.data["findings"]
    assert len(findings) == 1, f"findings 应直接来自 arguments: {findings}"
    assert findings[0]["title"] == "SQL 注入漏洞"
    assert findings[0]["file_path"] == "src/sql_vuln.py"
    assert parse_calls == [], "tool_calls submit_findings 路径不得走 json-repair 文本解析"

    # 首轮即终态：证明无"先 Action 后 Final"代码门禁误拦工具形态
    assert result.iterations == 1

    # 事件流等价：完成决策 + 完成事件照常发射
    events = _emitted(emitter)
    decisions = [md for et, md in events if et == "llm_decision"]
    assert any("完成安全分析" in md.get("decision", "") for md in decisions)
    assert any(et == "llm_complete" for et, _ in events)

    # 历史合成：tool_calls 轮合成 Final Answer 文本入历史（多轮历史自洽）
    assistant_msgs = [m for m in agent._conversation_history if m["role"] == "assistant"]
    assert any("Final Answer:" in m["content"] for m in assistant_msgs), (
        "submit_findings 轮必须合成 Final Answer 文本入历史"
    )

    # 协议说明段：能力 tools=True 时首轮 history 注入一次，告知模型提交规则，
    # 防止凭推测在未扫描/未读文件前调用 submit_findings 直接交卷。
    protocol_msgs = [
        m for m in agent._conversation_history
        if m["role"] == "user" and "【工具协议说明】" in m["content"]
    ]
    assert len(protocol_msgs) == 1, (
        f"能力 tools=True 时必须注入一次【工具协议说明】到首轮 history；"
        f"实际 {len(protocol_msgs)} 次"
    )
    body = protocol_msgs[0]["content"]
    assert "submit_findings" in body
    assert "工作工具仍走文本格式" in body
    assert "read_file" in body
    assert "findings" in body and "空数组" in body


@pytest.mark.asyncio
async def test_analysis_broken_arguments_degrade_to_text_path(monkeypatch):
    """submit_findings arguments 坏 JSON → 降级文本路径；下一轮文本 Final Answer 正常交卷。"""
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_analysis(monkeypatch, caps)
    text_final = "Thought: 分析完成\nFinal Answer: " + json.dumps(ANALYSIS_PAYLOAD, ensure_ascii=False)
    seen = _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("submit_findings", "{broken json")], "output": ""},
        {"output": text_final},
    ])

    result = await agent.run({"project_info": {"name": "p"}, "config": {}, "previous_results": {}})

    assert result.success, f"降级文本路径应收尾成功: {result.error}"
    assert len(seen) == 2, "坏 JSON 轮不得终态，应继续循环"
    assert len(result.data["findings"]) == 1
    assert result.data["findings"][0]["title"] == "SQL 注入漏洞"


@pytest.mark.asyncio
async def test_analysis_no_tools_when_capability_unavailable_and_text_path(monkeypatch):
    """能力 tools=False → 不传 tools；文本 Final Answer 走现有 json-repair 路径（回归）。"""
    caps = BackendCapabilities(tools=False, guided_json=False)
    agent = _make_analysis(monkeypatch, caps)
    text_final = "Thought: 分析完成\nFinal Answer: " + json.dumps(ANALYSIS_PAYLOAD, ensure_ascii=False)
    seen = _install_stream(monkeypatch, agent, [{"output": text_final}])

    result = await agent.run({"project_info": {"name": "p"}, "config": {}, "previous_results": {}})

    assert result.success, f"文本协议路径应收尾成功: {result.error}"
    assert seen[0]["tools"] is None, "能力不可用时不得传 tools"
    assert seen[0]["response_format"] is None
    assert len(result.data["findings"]) == 1

    # 降级模式模型看不到 submit_findings，协议说明段必须缺席（写入反而混淆）
    assert not any(
        "【工具协议说明】" in m["content"]
        for m in agent._conversation_history if m["role"] == "user"
    ), "能力 tools=False 时不得注入 submit_findings 协议说明段"


@pytest.mark.asyncio
async def test_analysis_forced_summary_injects_guided_response_format(monkeypatch):
    """强制总结轮：guided_json 可用 → response_format 携带 findings schema；不可用 → None。"""
    caps = BackendCapabilities(tools=True, guided_json=True)
    agent = _make_analysis(monkeypatch, caps)
    agent._conversation_history = []
    captured = {}

    async def _fake_summary_stream(messages, temperature=None, tools=None, response_format=None, **kwargs):
        captured["tools"] = tools
        captured["response_format"] = response_format
        return json.dumps(ANALYSIS_PAYLOAD, ensure_ascii=False), 7

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_fake_summary_stream))

    findings, _floor = await agent._run_forced_summary([])

    assert len(findings) == 1
    assert findings[0]["title"] == "SQL 注入漏洞"
    # 该轮是纯 JSON 输出：不携带 tools（submit_findings 不用于强制总结轮）
    assert captured["tools"] is None
    rf = captured["response_format"]
    assert rf is not None and rf["type"] == "json_schema"
    schema = rf["json_schema"]["schema"]
    assert "findings" in schema["properties"]
    assert "summary" in schema["properties"]

    # guided 不可用 → 不传 response_format（维持提示词约束现状）
    caps_no_guided = BackendCapabilities(tools=True, guided_json=False)
    agent2 = _make_analysis(monkeypatch, caps_no_guided)
    agent2._conversation_history = []
    captured2 = {}

    async def _fake_summary_stream_2(messages, temperature=None, tools=None, response_format=None, **kwargs):
        captured2["response_format"] = response_format
        return json.dumps(ANALYSIS_PAYLOAD, ensure_ascii=False), 7

    monkeypatch.setattr(agent2, "stream_llm_call", AsyncMock(side_effect=_fake_summary_stream_2))
    await agent2._run_forced_summary([])
    assert captured2["response_format"] is None


# ============ Verification run 循环集成 ============

def _make_verification(monkeypatch, caps):
    agent = VerificationAgent(llm_service=_make_service(caps), tools={}, event_emitter=_make_emitter())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    # 沙箱准备/确定性执行在本测试范围外（协议层测试），桩掉
    monkeypatch.setattr(agent, "_run_deterministic_sandbox_commands", AsyncMock())
    monkeypatch.setattr(agent, "_build_sandbox_commands", MagicMock(return_value=[]))
    monkeypatch.setattr(agent, "_prepare_sandbox_files", MagicMock(return_value=None))
    return agent


def _verification_input():
    return {"previous_results": {"findings": [dict(VERIFICATION_INPUT_FINDING)]}, "config": {}}


@pytest.mark.asyncio
async def test_verification_tool_calls_submit_findings_passes_gate(monkeypatch):
    """Verification tool_calls 形态：tools 注入 + submit_findings 交卷；

    沙箱门禁消费 step.final_answer（与文本形态同构）——finding 带 sandbox_skip_reason
    时 _count_skip_reasons 正常计数放行，verdict/poc 等字段下游归一化不受影响。
    """
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_verification(monkeypatch, caps)
    emitter = agent.event_emitter
    seen = _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("submit_findings", json.dumps(VERIFICATION_PAYLOAD, ensure_ascii=False))],
         "output": ""},
    ])

    result = await agent.run(_verification_input())

    assert result.success, f"submit_findings 应收尾成功: {result.error}"
    assert seen[0]["tools"] is not None
    assert [t["function"]["name"] for t in seen[0]["tools"]] == ["submit_findings"]
    assert len(result.data["findings"]) == 1, f"验证结果应来自 arguments: {result.data}"
    # 完成决策事件等价（含验证计数文案）
    events = _emitted(emitter)
    decisions = [md for et, md in events if et == "llm_decision"]
    assert any("完成漏洞验证" in md.get("decision", "") for md in decisions)
    assert any(et == "llm_complete" for et, _ in events)
    # 历史合成（门禁拒绝后继续循环时模型能看到自己的报告）
    assistant_msgs = [m for m in agent._conversation_history if m["role"] == "assistant"]
    assert any("Final Answer:" in m["content"] for m in assistant_msgs)

    # 协议说明段：能力 tools=True 时首轮注入一次，含"先沙箱验证后提交"门禁提示
    protocol_msgs = [
        m for m in agent._conversation_history
        if m["role"] == "user" and "【工具协议说明】" in m["content"]
    ]
    assert len(protocol_msgs) == 1, (
        f"Verification 能力 tools=True 时必须注入一次协议说明段；实际 {len(protocol_msgs)} 次"
    )
    body = protocol_msgs[0]["content"]
    assert "submit_findings" in body
    assert "sandbox_skip_reason" in body
    assert "无证据提交会被系统拒绝" in body


@pytest.mark.asyncio
async def test_verification_tool_calls_gate_rejects_without_evidence(monkeypatch):
    """沙箱门禁在 tool_calls 形态下仍然生效：无沙箱成功且无 skip_reason → 拒绝完成；

    下一轮带 sandbox_skip_reason 的 submit_findings 放行。证明工具形态既不被误拦、
    也不绕过验证证据门禁。
    """
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_verification(monkeypatch, caps)

    payload_no_skip = json.loads(json.dumps(VERIFICATION_PAYLOAD, ensure_ascii=False))
    for f in payload_no_skip["findings"]:
        f.pop("sandbox_skip_reason")
    payload_with_skip = VERIFICATION_PAYLOAD

    thinking_msgs = []
    real_emit_thinking = agent.emit_thinking

    async def _spy_thinking(msg):
        thinking_msgs.append(msg)
        return await real_emit_thinking(msg)

    monkeypatch.setattr(agent, "emit_thinking", AsyncMock(side_effect=_spy_thinking))
    seen = _install_stream(monkeypatch, agent, [
        {"tool_calls": [_tool_call("submit_findings", json.dumps(payload_no_skip, ensure_ascii=False))],
         "output": ""},
        {"tool_calls": [_tool_call("submit_findings", json.dumps(payload_with_skip, ensure_ascii=False))],
         "output": ""},
    ])

    result = await agent.run(_verification_input())

    assert result.success, f"第二轮 skip_reason 应放行: {result.error}"
    assert len(seen) == 2, "第一轮必须被门禁拒绝并继续循环"
    assert any("拒绝完成" in m for m in thinking_msgs), (
        "无沙箱证据的 submit_findings 必须被门禁拒绝（工具形态不得绕过门禁）"
    )


@pytest.mark.asyncio
async def test_verification_text_final_answer_path_unchanged(monkeypatch):
    """Verification 能力 False → 不传 tools；文本 Final Answer 路径回归。"""
    caps = BackendCapabilities(tools=False, guided_json=False)
    agent = _make_verification(monkeypatch, caps)
    text_final = "Thought: 验证完成\nFinal Answer: " + json.dumps(VERIFICATION_PAYLOAD, ensure_ascii=False)
    seen = _install_stream(monkeypatch, agent, [{"output": text_final}])

    result = await agent.run(_verification_input())

    assert result.success, f"文本协议路径应收尾成功: {result.error}"
    assert seen[0]["tools"] is None
    assert len(result.data["findings"]) == 1

    # 降级模式不注入 submit_findings 协议说明段
    assert not any(
        "【工具协议说明】" in m["content"]
        for m in agent._conversation_history if m["role"] == "user"
    ), "Verification 能力 tools=False 时不得注入协议说明段"
