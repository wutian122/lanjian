"""P12 Wave 0 · F2 回归夹具（seq 4536 形态：空卷矛盾门禁）。

事件证据（任务 8a3a713f，seq 4536）：模型输出空 findings 的 Final Answer 合法
交卷，但 summary 文本自曝存在 confidence=0.15 候选（file_path + needs_verification
共现），因自设阈值未放入 findings 数组，服务端零门禁直接收口——候选销毁链断链②。

Wave 1.4 契约（方案第二章 1.4，裁决①=A：仅注入纠正，不做服务端自由文本抽取）：
- is_final 分支：findings 为空但 thought/summary 命中候选承认特征
  （confidence=0.x + file_path(+行号) + needs_verification 共现）时不 break，
  回引模型原文注入一轮模板化矛盾纠正（"必须入数组或逐条引用证据否定"），
  该轮不占 orchestrator dispatch 名额；
- 纠正轮给出候选 → 候选正常落库；
- 纠正轮仍空 → 才允许收口，并在返回 data 标记 contradicted_empty=true
  （floor_report 同源），供报告分区/守卫区分"判干净"与"自相矛盾"。

T0 阶段本文件用例 RED，失败原因=矛盾检测/纠正轮缺失（当前空 Final Answer 直接 break）。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent


# seq4536 形态：summary 自相矛盾——承认 0.15 候选却交空卷
_ADMISSION_SUMMARY = (
    "对限定文件完成审计。备注：src/ngx_http_core_module.c:1820 处 "
    "file_path=src/ngx_http_core_module.c line=1820 有一个 confidence=0.15 "
    "的可疑 URL 透传点，needs_verification=true，但置信度低于自设阈值，"
    "未放入 findings 数组。"
)

_EMPTY_ADMISSION_FINAL = (
    "Thought: 审计完成\nFinal Answer: "
    + json.dumps({"findings": [], "summary": _ADMISSION_SUMMARY}, ensure_ascii=False)
)

_EMPTY_PLAIN_FINAL = (
    'Thought: 没东西\nFinal Answer: {"findings": [], "summary": "nothing"}'
)

_EXEMPT_SUMMARY_FINAL = (
    'Final Answer: {"findings": [], '
    '"summary": "UNCOVERED_DIMENSION_EXEMPT: D7_crypto - 限定范围无加密代码\\n'
    'UNCOVERED_DIMENSION_EXEMPT: D10_supply_chain - 未审计依赖清单"}'
)

_CANDIDATE = {
    "vulnerability_type": "ssrf",
    "severity": "medium",
    "title": "upstream URL 透传可疑点",
    "description": "url 参数未白名单校验",
    "file_path": "src/ngx_http_core_module.c",
    "line_start": 1820,
    "code_snippet": "ngx_http_proxy_pass(r, url)",
    "suggestion": "校验目标主机",
    "confidence": 0.15,
    "needs_verification": True,
}

_CANDIDATE_FINAL = (
    "Thought: 按纠正要求入数组\nFinal Answer: "
    + json.dumps({"findings": [_CANDIDATE], "summary": "补交 1 条候选"}, ensure_ascii=False)
)

_READ_ACTION = (
    'Thought: 读文件\n'
    'Action: read_file\n'
    'Action Input: {"file_path": "src/ngx_http_core_module.c"}'
)


def _agent(monkeypatch):
    agent = AnalysisAgent(llm_service=SimpleNamespace(), tools={})
    for name in (
        "emit_thinking", "emit_event", "emit_llm_decision", "emit_llm_thought",
        "emit_finding", "emit_llm_action", "emit_llm_observation",
        "emit_llm_complete",
    ):
        monkeypatch.setattr(agent, name, AsyncMock())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    monkeypatch.setattr(
        agent, "execute_tool",
        AsyncMock(return_value="/* ngx_http_core_module.c 源码内容 */"),
    )
    return agent


def _install_script(monkeypatch, agent, script):
    calls = []

    async def _fake_stream(messages, **kwargs):
        i = len(calls)
        calls.append(messages[-1]["content"] if messages else "")
        return script[i], 10

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_fake_stream))
    return calls


@pytest.mark.asyncio
async def test_f2_empty_final_with_admitted_candidate_triggers_correction_round(monkeypatch):
    """seq4536 形态：空 findings + summary 三特征共现 → 必须注入矛盾纠正轮，
    不得在第一轮直接 break 收口；纠正轮仍空才收口并标 contradicted_empty。"""
    agent = _agent(monkeypatch)
    calls = _install_script(
        monkeypatch,
        agent,
        [
            _READ_ACTION,
            _EMPTY_ADMISSION_FINAL,   # 空卷但自曝候选 → 纠正，不 break
            _EMPTY_PLAIN_FINAL,       # 纠正轮仍空 → 允许收口
            _EXEMPT_SUMMARY_FINAL,    # 既有强制总结轮（0 候选时触发）
        ],
    )

    result = await agent.run({"project_info": {"name": "p"}, "config": {}})

    assert result.success, f"纠正后仍空应正常收口（非异常失败）: {result.error}"
    assert result.data["findings"] == []
    # 纠正轮确实发生：至少 3 次 LLM 调用（读码 / 空卷 / 纠正轮）
    assert len(calls) >= 3, (
        f"当前空 Final Answer 直接收口（{len(calls)} 轮），必须追加矛盾纠正轮"
    )
    # 纠正模板必须回引模型原文并给出二选一口径
    correction = next(
        (c for c in calls if "findings" in c and ("证据否定" in c or "矛盾" in c or "入数组" in c)),
        None,
    )
    assert correction is not None, (
        "必须注入模板化矛盾纠正：候选入数组或逐条引用证据否定"
    )
    assert result.data.get("contradicted_empty") is True, (
        "纠正轮仍空时必须标记 contradicted_empty=true 供守卫/报告分区使用"
    )


@pytest.mark.asyncio
async def test_f2_correction_round_providing_candidate_lands_normally(monkeypatch):
    """纠正轮把承认的候选放入 findings → 正常终态落库，不标 contradicted_empty。"""
    agent = _agent(monkeypatch)
    calls = _install_script(
        monkeypatch,
        agent,
        [
            _READ_ACTION,
            _EMPTY_ADMISSION_FINAL,
            _CANDIDATE_FINAL,         # 纠正轮补交候选
        ],
    )

    result = await agent.run({"project_info": {"name": "p"}, "config": {}})

    assert result.success, f"补交候选后应正常收口: {result.error}"
    findings = result.data["findings"]
    assert len(findings) == 1
    assert findings[0]["vulnerability_type"] == "ssrf"
    assert findings[0]["file_path"] == "src/ngx_http_core_module.c"
    assert len(calls) == 3, "补交候选即终态，不应再触发强制总结"
    # 候选必须经由"矛盾纠正轮"入数组，而非既有强制总结兜底
    assert any(
        "证据否定" in c or ("矛盾" in c and "findings" in c) or "入数组" in c
        for c in calls
    ), "纠正轮前必须注入矛盾纠正模板（回引模型原文）"
    assert not result.data.get("contradicted_empty"), (
        "纠正成功（候选已入数组）不得标 contradicted_empty"
    )


@pytest.mark.asyncio
async def test_f2_clean_empty_final_without_admission_not_corrected(monkeypatch):
    """控制组：空 findings 且 summary 无候选承认特征 → 不注入矛盾纠正
    （走既有强制总结路径，门禁不得误拦真"判干净"）。"""
    agent = _agent(monkeypatch)
    calls = _install_script(
        monkeypatch,
        agent,
        [
            _READ_ACTION,
            _EMPTY_PLAIN_FINAL,      # 无 admission 特征
            _EXEMPT_SUMMARY_FINAL,   # 既有强制总结轮给豁免
        ],
    )

    result = await agent.run({"project_info": {"name": "p"}, "config": {}})

    assert result.success
    correction = [
        c for c in calls if "证据否定" in c or ("矛盾" in c and "findings" in c)
    ]
    assert correction == [], "无承认特征时空卷不得触发矛盾纠正"
    assert not result.data.get("contradicted_empty")
