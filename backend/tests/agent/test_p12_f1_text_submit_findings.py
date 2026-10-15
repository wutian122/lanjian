"""P12 Wave 0 · F1 回归夹具（seq 3418 形态：文本 Action: submit_findings 协议桥接）。

事件证据（任务 8a3a713f，seq 3418/3419；对照任务 cdbc12ff seq 5452 同形态）：
模型在能力探测 tools=true 但函数调用不稳定时，以**文本协议**输出
``Action: submit_findings`` + ``Action Input: {findings, summary}`` 交卷，
执行器判「工具 submit_findings 不存在」整体丢弃——协议桥接断裂，候选销毁。

本夹具锁定 Wave 1.1 契约（方案第二章 1.1，裁决⑨ nudge 分叉在 F2 外另测）：
1. ``_parse_llm_response`` 对文本 ``Action: submit_findings`` 做终态特判，
   合成与 ``_final_step_from_tool_calls`` 同构的 is_final step
   （findings 非 list → None；非 dict 项过滤；json-repair 抢救；无标签形态同样识别；
   summary 中的 UNCOVERED_DIMENSION_EXEMPT 豁免行原样保留）；
2. 交卷门禁：终态合成前要求至少 1 个成功工具 step（text/tool_calls 两路同口径，
   现状两路同裸——不得把空洞扩大），未达标时定向引导模型改用
   ``Final Answer: {findings, summary}``，而不是泛化「可用工具」清单；
3. base 未知工具分支对 submit_findings 返回定向引导。

T0 阶段（仅夹具、无生产码）本文件全部用例 RED，失败原因=终态特判/门禁缺失。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent


def _agent(monkeypatch, tools=None):
    agent = AnalysisAgent(llm_service=SimpleNamespace(), tools=tools or {})
    for name in (
        "emit_thinking", "emit_event", "emit_llm_decision", "emit_llm_thought",
        "emit_finding", "emit_llm_action", "emit_llm_observation",
        "emit_llm_complete",
    ):
        monkeypatch.setattr(agent, name, AsyncMock())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    return agent


_PAYLOAD = {
    "findings": [
        {
            "vulnerability_type": "ssrf",
            "severity": "high",
            "title": "upstream URL 透传 SSRF",
            "description": "proxy_pass 拼接用户可控 url",
            "file_path": "src/ngx_http_proxy.c",
            "line_start": 1820,
            "code_snippet": "ngx_http_proxy_pass(r, url)",
            "suggestion": "白名单校验",
            "confidence": 0.55,
            "needs_verification": True,
        }
    ],
    "summary": (
        "限定文件审计完成，1 条低置信 SSRF 候选。\n"
        "UNCOVERED_DIMENSION_EXEMPT: D7_crypto - 限定范围无加解密逻辑\n"
        "UNCOVERED_DIMENSION_EXEMPT: D10_supply_chain - 未审计依赖清单\n"
        "UNCOVERED_DIMENSION_EXEMPT: D4_deserialization - 无反序列化入口\n"
        "UNCOVERED_DIMENSION_EXEMPT: D9_business_logic - 纯模块无业务流\n"
    ),
}


# ---------- 解析层：文本 submit_findings 终态特判 ----------

def test_f1_labeled_action_input_synthesizes_final_step(monkeypatch):
    """seq3418 形态：带 Action Input 标签的文本 submit_findings → is_final。

    summary 中的 4 条 UNCOVERED_DIMENSION_EXEMPT 豁免必须原样保留
    （seq3418 正是携带完整 summary+4 豁免被整体丢弃）。
    """
    agent = _agent(monkeypatch)
    response = (
        "Thought: 限定文件已读完，提交报告\n"
        "Action: submit_findings\n"
        "Action Input: " + json.dumps(_PAYLOAD, ensure_ascii=False)
    )

    step = agent._parse_llm_response(response)

    assert step is not None, "文本 submit_findings 必须被识别（当前被当未知工具）"
    assert step.is_final is True, "submit_findings 是终态动作，必须合成 final step"
    assert step.action is None
    assert isinstance(step.final_answer, dict)
    assert len(step.final_answer["findings"]) == 1
    assert step.final_answer["findings"][0]["title"] == "upstream URL 透传 SSRF"
    summary = step.final_answer["summary"]
    assert summary.count("UNCOVERED_DIMENSION_EXEMPT:") == 4


def test_f1_unlabeled_input_synthesizes_final_step(monkeypatch):
    """无 ``Action Input:`` 标签、JSON 直接跟在 Action 行后的形态同样终态识别。"""
    agent = _agent(monkeypatch)
    response = (
        "Thought: 提交\nAction: submit_findings\n"
        + json.dumps(_PAYLOAD, ensure_ascii=False)
    )

    step = agent._parse_llm_response(response)

    assert step is not None and step.is_final is True
    assert len(step.final_answer["findings"]) == 1


def test_f1_non_dict_findings_items_filtered(monkeypatch):
    """findings 数组中的非字典项被过滤（与 tool_calls 路径同构）。"""
    agent = _agent(monkeypatch)
    payload = {"findings": [_PAYLOAD["findings"][0], "垃圾字符串", 42], "summary": "s"}
    response = (
        "Action: submit_findings\nAction Input: "
        + json.dumps(payload, ensure_ascii=False)
    )

    step = agent._parse_llm_response(response)

    assert step is not None and step.is_final is True
    assert step.final_answer["findings"] == [_PAYLOAD["findings"][0]]


def test_f1_findings_not_list_returns_none(monkeypatch):
    """findings 不是 list（坏 JSON 被修成对象/字符串）→ None 交主循环格式 nudge。"""
    agent = _agent(monkeypatch)
    response = (
        'Action: submit_findings\nAction Input: '
        '{"findings": {"title": "not-a-list"}, "summary": "s"}'
    )

    assert agent._parse_llm_response(response) is None


def test_f1_half_json_repaired_into_final_step(monkeypatch):
    """尾部截断的 submit_findings JSON 经 json-repair 抢救，仍合成终态 step。"""
    agent = _agent(monkeypatch)
    half = (
        'Action: submit_findings\nAction Input: {"findings": ['
        '{"vulnerability_type": "ssrf", "severity": "high", '
        '"title": "proxy ssrf", "file_path": "src/x.c", "line_start": 9'
    )

    step = agent._parse_llm_response(half)

    assert step is not None and step.is_final is True
    assert isinstance(step.final_answer.get("findings"), list)
    assert step.final_answer["findings"], "半截 JSON 中已成形的候选不得被整轮丢弃"


def test_f1_regular_tool_action_still_non_final(monkeypatch):
    """回归：read_file 等日常工具的文本 Action 不得被误判为终态。"""
    agent = _agent(monkeypatch)
    response = 'Thought: 读文件\nAction: read_file\nAction Input: {"file_path": "src/x.c"}'

    step = agent._parse_llm_response(response)

    assert step is not None and step.is_final is False
    assert step.action == "read_file"


# ---------- base 未知工具分支：submit_findings 定向引导 ----------

@pytest.mark.asyncio
async def test_f1_execute_tool_unknown_submit_findings_directed_guidance(monkeypatch):
    """文本协议下 execute_tool 收到 submit_findings 时返回定向引导
    （改用 Final Answer: {findings, summary}），不是泛化可用工具清单。"""
    read_file_tool = MagicMock()
    agent = _agent(monkeypatch, tools={"read_file": read_file_tool})

    observation = await agent.execute_tool(
        "submit_findings", {"findings": [], "summary": "s"}
    )

    assert "Final Answer" in observation, "必须定向引导模型改用 Final Answer 交卷"
    assert "findings" in observation
    # 泛化清单（seq3419 实际文案）不得再出现
    assert "可用工具: ['read_file']" not in observation


# ---------- run 循环层：交卷前至少 1 个成功工具 step 门禁 ----------

def _install_script(monkeypatch, agent, script):
    async def _fake_stream(messages, **kwargs):
        i = len(calls)
        calls.append(i)
        return script[i], 10

    calls = []
    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_fake_stream))
    return calls


@pytest.mark.asyncio
async def test_f1_submit_before_any_successful_tool_is_guided_not_final(monkeypatch):
    """交卷门禁：0 个成功工具 step 时文本 submit_findings 不得直接终态——
    注入定向 Final Answer 引导后继续循环（seq3418 有真实读码动作，属合法交卷，
    本用例锁的是裸交卷防幻觉，且 text/tool_calls 两路同口径）。"""
    agent = _agent(monkeypatch)
    submit_text = (
        "Action: submit_findings\nAction Input: "
        + json.dumps(_PAYLOAD, ensure_ascii=False)
    )
    empty_final = 'Final Answer: {"findings": [], "summary": "nothing"}'
    # 空 Final Answer 后既有强制总结逻辑会再发一轮：给足豁免避免 floor 重试
    exempt_summary = (
        'Final Answer: {"findings": [], '
        '"summary": "UNCOVERED_DIMENSION_EXEMPT: D7_crypto - 限定范围无加密代码"}'
    )
    calls = _install_script(
        monkeypatch, agent, [submit_text, empty_final, exempt_summary]
    )
    execute = AsyncMock(return_value="不应被调用")
    monkeypatch.setattr(agent, "execute_tool", execute)

    result = await agent.run({"project_info": {"name": "p"}, "config": {}})

    assert result.success, f"引导后第二轮 Final Answer 应正常收口: {result.error}"
    assert result.data["findings"] == [], "被门禁拦下的候选不得落库"
    execute.assert_not_awaited(), "门禁必须在工具执行前拦截，不得走未知工具分支"
    assert len(calls) == 3, "首轮引导 + 空 Final Answer + 既有强制总结，恰 3 轮"
    user_msgs = [
        m["content"] for m in agent._conversation_history if m["role"] == "user"
    ]
    assert any("Final Answer" in m and "findings" in m for m in user_msgs), (
        "引导消息必须指向 Final Answer: {findings, summary} 交卷格式"
    )


@pytest.mark.asyncio
async def test_f1_submit_after_successful_tool_step_finalizes(monkeypatch):
    """seq3418 合法形态：先有成功 read_file，再文本 submit_findings → 正常终态落库。"""
    agent = _agent(monkeypatch)
    read_action = (
        'Thought: 读取目标文件\n'
        'Action: read_file\n'
        'Action Input: {"file_path": "src/ngx_http_proxy.c"}'
    )
    submit_text = (
        "Thought: 提交\nAction: submit_findings\nAction Input: "
        + json.dumps(_PAYLOAD, ensure_ascii=False)
    )
    calls = _install_script(monkeypatch, agent, [read_action, submit_text])

    async def _fake_execute(name, payload):
        return "/* ngx_http_proxy_handler ... requests 上游 */"

    monkeypatch.setattr(agent, "execute_tool", AsyncMock(side_effect=_fake_execute))

    result = await agent.run({"project_info": {"name": "p"}, "config": {}})

    assert result.success, f"有成功工具 step 的交卷必须终态: {result.error}"
    assert len(calls) == 2
    findings = result.data["findings"]
    assert len(findings) == 1
    assert findings[0]["file_path"] == "src/ngx_http_proxy.c"
    assert result.iterations == 2
