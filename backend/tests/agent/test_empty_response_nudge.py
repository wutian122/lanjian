"""
sandbox-verification-hard-gate Task 20：空响应强化重试（双形态 nudge）

Task 10 端到端双轮验证实测两种空响应形态：
- 形态 A（truncated）：finish_reason=length 且正文空——reasoning 思考流吃光输出
  预算；截断事实由 _record_llm_truncation 的历史提示告知，nudge 只补归因
  （"思考耗尽预算 / 精简思考尽快 Action"），不重复"截断"字样；
- 形态 B（reasoning_only）：finish_reason=stop 且正文空——模型 reasoning 后
  自然停止零正文（无截断、无服务端错误，纯模型行为退化）；nudge 明确告知
  "只输出了思考没有行动，不要重复思考，直接输出 Action"；
- other：旧协议无 kind 分流 / API 错误恢复等空响应，nudge 返回空串，
  各 Agent 维持现有泛化重试提示。

Scenario 覆盖：
1. 形态判定（真实流）：reasoning_only / truncated / other / 非空重置 / tool_calls 轮不判空
2. nudge 文案：形态 B 含"只输出了思考"；tools 模式含 submit_findings、文本模式不含；
   形态 A 含"耗尽输出预算"且不含"截断"（与截断历史提示协同不重复）；other 返回空串
3. run 循环接入：analysis（文本/tools 两模式）、recon、verification、orchestrator
4. 上限不变：analysis 连续 3 次空响应收口与改动前一致；orchestrator 连续 5 次
"""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent
from app.services.agent.agents.verification import VerificationAgent
from app.services.agent.core.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_llm_circuit,
)
from app.services.agent.core.rate_limiter import get_llm_rate_limiter
from app.services.agent.structured_output import BackendCapabilities

_TIMEOUT_CONFIG = {
    "llm_first_token_timeout": 30,
    "llm_stream_timeout": 60,
    "agent_timeout": 1800,
    "sub_agent_timeout": 600,
    "tool_timeout": 60,
}


# ---------- 公共工厂 ----------

def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    for name in [
        "emit_info", "emit_warning", "emit_error", "emit_thinking",
        "emit_tool_call", "emit_tool_result", "emit_finding", "emit_progress",
        "emit_phase_start", "emit_phase_complete", "emit_task_complete",
    ]:
        setattr(e, name, AsyncMock())
    return e


def _make_service(caps=None, max_tokens=32768):
    service = MagicMock()
    service.backend_capabilities = caps
    service.get_agent_timeout_config = MagicMock(return_value=_TIMEOUT_CONFIG)
    service.config = SimpleNamespace(max_tokens=max_tokens)
    return service


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


def _tool_call(name, arguments="{}"):
    return {"id": f"call_{name}", "name": name, "arguments": arguments}


# ---------- 流脚本工厂（真实 chat_completion_stream，信号由真实 stream_llm_call 置位） ----------

def _stream_of(*chunks):
    """单轮固定 chunk 序列。"""
    async def _gen(messages=None, temperature=None, max_tokens=None, tools=None,
                   response_format=None):
        for chunk in chunks:
            yield chunk
    return _gen


_REASONING_CHUNK = {
    "type": "token", "kind": "reasoning", "content": "让我仔细分析一下当前情况",
    "accumulated": "让我仔细分析一下当前情况",
    "accumulated_content": "",
    "accumulated_reasoning": "让我仔细分析一下当前情况",
}


def _scripted_chat_stream(rounds):
    """多轮脚本：每次调用 chat_completion_stream 返回下一脚本轮次的生成器。

    rounds 每项 form：
    - reasoning_only：新协议 reasoning 流后 finish_reason=stop、正文空（形态 B）
    - truncated：新协议 reasoning 流后 finish_reason=length、正文空（形态 A）
    - other_empty：旧协议无 kind、正文空（other）
    - tool_calls：正文空但带 tool_calls（正常工具轮，不判空）
    - ok：正常正文（text 指定）
    """
    state = {"i": 0}

    async def _stream(messages=None, temperature=None, max_tokens=None, tools=None,
                      response_format=None):
        spec = rounds[state["i"]]
        state["i"] += 1
        form = spec["form"]
        if form == "reasoning_only":
            yield dict(_REASONING_CHUNK)
            yield {
                "type": "done", "content": "",
                "reasoning": "让我仔细分析一下当前情况",
                "accumulated": "让我仔细分析一下当前情况",
                "usage": {"total_tokens": 12}, "finish_reason": "stop",
            }
        elif form == "truncated":
            yield {
                "type": "token", "kind": "reasoning", "content": "思考很长但还没行动",
                "accumulated": "思考很长但还没行动",
                "accumulated_content": "",
                "accumulated_reasoning": "思考很长但还没行动",
            }
            yield {
                "type": "done", "content": "",
                "reasoning": "思考很长但还没行动",
                "accumulated": "思考很长但还没行动",
                "usage": {"total_tokens": 99}, "finish_reason": "length",
            }
        elif form == "other_empty":
            yield {"type": "done", "content": "",
                   "usage": {"total_tokens": 0}, "finish_reason": "stop"}
        elif form == "tool_calls":
            yield {
                "type": "done", "content": "", "reasoning": "",
                "tool_calls": spec["tool_calls"],
                "usage": {"total_tokens": 10}, "finish_reason": "tool_calls",
            }
        else:  # ok
            text = spec["text"]
            yield {
                "type": "done", "content": text, "reasoning": "",
                "accumulated": text,
                "usage": {"total_tokens": 10}, "finish_reason": "stop",
            }

    return _stream


def _user_messages(agent):
    return [m["content"] for m in agent._conversation_history if m["role"] == "user"]


def _nudge_retry_messages(agent):
    """历史中携带形态 nudge 的重试提示（形态 B/A 关键词）。"""
    return [
        c for c in _user_messages(agent)
        if "只输出了思考" in c or "耗尽输出预算" in c
    ]


# ============ Part 1：形态判定与 nudge 文案（真实流，ReconAgent 最轻量） ============

def _part1_agent(stream_fn):
    return ReconAgent(
        llm_service=_make_service_with_stream(stream_fn),
        tools={},
        event_emitter=_make_emitter(),
    )


def _make_service_with_stream(stream_fn, max_tokens=32768):
    service = _make_service(caps=None, max_tokens=max_tokens)
    service.chat_completion_stream = stream_fn
    return service


@pytest.mark.asyncio
async def test_reasoning_only_empty_classified_and_nudge_text():
    """形态 B：finish_reason=stop 且仅 reasoning 无正文 → kind=reasoning_only；
    nudge 含"只输出了思考"；文本模式不提 submit_findings，tools 模式提。"""
    agent = _part1_agent(_stream_of(
        dict(_REASONING_CHUNK),
        {
            "type": "done", "content": "",
            "reasoning": "让我仔细分析一下当前情况",
            "accumulated": "让我仔细分析一下当前情况",
            "usage": {"total_tokens": 12}, "finish_reason": "stop",
        },
    ))
    history = agent._conversation_history
    history.append({"role": "user", "content": "开始审计"})

    output, tokens = await agent.stream_llm_call(history)

    assert output == ""
    assert tokens == 12
    assert agent._last_empty_kind == "reasoning_only"

    nudge_text_mode = agent._empty_response_nudge()
    assert "只输出了思考" in nudge_text_mode
    assert "submit_findings" not in nudge_text_mode, "文本协议不得提示 submit_findings"

    nudge_tools_mode = agent._empty_response_nudge(tool_hint="或调用 submit_findings 提交报告")
    assert "只输出了思考" in nudge_tools_mode
    assert "submit_findings" in nudge_tools_mode


@pytest.mark.asyncio
async def test_truncated_empty_nudge_complements_truncation_hint():
    """形态 A：finish_reason=length 正文空 → kind=truncated；nudge 含"耗尽输出预算"
    且不含"截断"（截断事实由 _record_llm_truncation 历史提示承担，协同不重复）。"""
    agent = _part1_agent(_stream_of(
        {
            "type": "token", "kind": "reasoning", "content": "思考很长但还没行动",
            "accumulated": "思考很长但还没行动",
            "accumulated_content": "",
            "accumulated_reasoning": "思考很长但还没行动",
        },
        {
            "type": "done", "content": "",
            "reasoning": "思考很长但还没行动",
            "accumulated": "思考很长但还没行动",
            "usage": {"total_tokens": 99}, "finish_reason": "length",
        },
    ))
    history = agent._conversation_history
    history.append({"role": "user", "content": "开始审计"})

    output, _ = await agent.stream_llm_call(history)

    assert output == ""
    assert agent._last_empty_kind == "truncated"
    nudge = agent._empty_response_nudge()
    assert "耗尽输出预算" in nudge
    assert "截断" not in nudge, (
        "nudge 不得重复截断事实——截断提示已由 _record_llm_truncation 注入历史"
    )
    # 协同：截断事实提示已在同一轮注入历史
    trunc_hints = [
        m["content"] for m in history
        if m["role"] == "user" and "截断" in m["content"]
    ]
    assert trunc_hints, "finish_reason=length 必须先注入截断历史提示"


@pytest.mark.asyncio
async def test_legacy_empty_classified_other_and_no_nudge():
    """旧协议无 kind 分流的空响应 → kind=other；nudge 返回空串（维持泛化提示）。"""
    agent = _part1_agent(_stream_of(
        {"type": "done", "content": "",
         "usage": {"total_tokens": 0}, "finish_reason": "stop"},
    ))
    history = agent._conversation_history
    history.append({"role": "user", "content": "开始审计"})

    output, _ = await agent.stream_llm_call(history)

    assert output == ""
    assert agent._last_empty_kind == "other"
    assert agent._empty_response_nudge() == ""
    assert agent._empty_response_nudge(tool_hint="或调用 submit_findings 提交报告") == ""


@pytest.mark.asyncio
async def test_non_empty_response_resets_empty_kind():
    """正常正文轮 → _last_empty_kind 为 None（含从上一轮形态复位）。"""
    agent = _part1_agent(_stream_of(
        {
            "type": "done", "content": "Thought: 正常输出\nAction: read_file",
            "reasoning": "", "accumulated": "Thought: 正常输出\nAction: read_file",
            "usage": {"total_tokens": 5}, "finish_reason": "stop",
        },
    ))
    # 预置上一轮残留形态，证明按轮重置
    agent._last_empty_kind = "reasoning_only"
    history = agent._conversation_history
    history.append({"role": "user", "content": "开始审计"})

    output, _ = await agent.stream_llm_call(history)

    assert output
    assert agent._last_empty_kind is None


@pytest.mark.asyncio
async def test_tool_calls_round_not_classified_as_empty():
    """正文空但带 tool_calls（正常工具轮）→ 不判空响应形态。"""
    agent = _part1_agent(_stream_of(
        {
            "type": "done", "content": "", "reasoning": "",
            "tool_calls": [_tool_call("submit_findings", '{"findings": []}')],
            "usage": {"total_tokens": 10}, "finish_reason": "tool_calls",
        },
    ))
    history = agent._conversation_history
    history.append({"role": "user", "content": "开始审计"})

    output, _ = await agent.stream_llm_call(history)

    assert output == ""
    assert agent._last_tool_calls is not None
    assert agent._last_empty_kind is None, (
        "tool_calls 轮正文空属正常（参数在 tool_calls 中），不得分类为空响应形态"
    )


# ============ Part 2：run 循环重试点接入 ============

_ANALYSIS_FINAL_TEXT = (
    "Thought: 分析完成，汇总发现\nFinal Answer: "
    + json.dumps({
        "summary": "发现 1 个 XSS",
        "findings": [{
            "vulnerability_type": "xss", "severity": "low", "title": "反射型 XSS",
            "description": "未转义输出", "file_path": "src/app.py", "line_start": 12,
            "code_snippet": "innerHTML = user_input",
            "source": "request.args.q", "sink": "innerHTML",
            "suggestion": "转义输出", "confidence": 0.6, "needs_verification": True,
        }],
    }, ensure_ascii=False)
)

_SUBMIT_FINDINGS_CALL = _tool_call(
    "submit_findings",
    json.dumps({
        "summary": "分析完成",
        "findings": [{
            "vulnerability_type": "xss", "severity": "low", "title": "反射型 XSS",
            "description": "未转义输出", "file_path": "src/app.py", "line_start": 12,
            "code_snippet": "innerHTML = user_input",
            "source": "request.args.q", "sink": "innerHTML",
            "suggestion": "转义输出", "confidence": 0.6, "needs_verification": True,
        }],
    }, ensure_ascii=False),
)

_RECON_FINAL_TEXT = (
    "Thought: 信息收集完成\nFinal Answer: "
    + json.dumps({
        "tech_stack": {"languages": ["Python"], "frameworks": []},
        "entry_points": ["main.py"],
        "high_risk_areas": [],
    }, ensure_ascii=False)
)

_VERIFICATION_PAYLOAD = {
    "summary": {"total": 1, "confirmed": 1, "likely": 0, "false_positive": 0},
    "findings": [{
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
    }],
}

_VERIFICATION_INPUT_FINDING = {
    "vulnerability_type": "sql_injection",
    "severity": "high",
    "title": "SQL 注入漏洞",
    "description": "f-string 拼接查询",
    "file_path": "src/sql_vuln.py",
    "line_start": 6,
    "code_snippet": "query = f\"SELECT * FROM users WHERE id = '{user_id}'\"",
    "needs_verification": True,
}


def _make_analysis_agent(caps):
    service = _make_service(caps=caps)
    agent = AnalysisAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
    agent._check_token_budget_exceeded = lambda: False
    agent.config.max_iterations = 6
    return agent


def _make_recon_agent():
    service = _make_service(caps=None)
    return ReconAgent(llm_service=service, tools={}, event_emitter=_make_emitter())


def _make_verification_agent(caps):
    service = _make_service(caps=caps)
    agent = VerificationAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
    agent._check_token_budget_exceeded = lambda: False
    # 沙箱准备/确定性执行在本测试范围外（协议层测试），桩掉
    agent._run_deterministic_sandbox_commands = AsyncMock()
    agent._build_sandbox_commands = MagicMock(return_value=[])
    agent._prepare_sandbox_files = MagicMock(return_value=None)
    return agent


def _make_orchestrator_agent(caps):
    service = _make_service(caps=caps)
    agent = OrchestratorAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
    agent._register_to_registry = lambda task=None: None
    agent._run_semgrep_prescan = AsyncMock(return_value={
        "findings": [], "hot_files": [], "scan_success": False,
    })
    agent._maybe_pause = AsyncMock()
    agent.emit_thinking = AsyncMock()
    agent.check_messages = lambda: []
    agent._check_token_budget_exceeded = lambda: False
    # 覆盖率门禁桩为充分覆盖，让 finish 直达收尾
    agent._evaluate_current_coverage = lambda: SimpleNamespace(
        is_sufficient=True, covered_count=10, gaps=[],
    )
    agent._dispatch_agent = AsyncMock(return_value="dispatch-observation")
    agent._summarize_findings = MagicMock(return_value="summary-observation")
    return agent


@pytest.mark.asyncio
async def test_analysis_reasoning_only_retry_contains_nudge_text_mode():
    """Analysis 文本协议（caps 未探测）：形态 B 空响应后重试提示含 nudge，不提 submit_findings。"""
    agent = _make_analysis_agent(caps=None)
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "reasoning_only"},
        {"form": "ok", "text": _ANALYSIS_FINAL_TEXT},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"任务应收尾成功: {result.error}"
    nudges = _nudge_retry_messages(agent)
    assert len(nudges) == 1, f"形态 B 空响应必须注入 1 条 nudge 重试提示，实际: {len(nudges)}"
    assert "只输出了思考" in nudges[0]
    assert "submit_findings" not in nudges[0], "文本协议 nudge 不得提 submit_findings"
    # 泛化格式说明仍保留（nudge 是前缀增强，不替换）
    assert "收到空响应" in nudges[0]


@pytest.mark.asyncio
async def test_analysis_reasoning_only_retry_contains_submit_findings_in_tools_mode():
    """Analysis tools 协议（caps.tools=True）：形态 B nudge 含 submit_findings 提示。"""
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_analysis_agent(caps=caps)
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "reasoning_only"},
        {"form": "tool_calls", "tool_calls": [_SUBMIT_FINDINGS_CALL]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"submit_findings 应收尾成功: {result.error}"
    nudges = _nudge_retry_messages(agent)
    assert len(nudges) == 1
    assert "只输出了思考" in nudges[0]
    assert "submit_findings" in nudges[0], "tools 协议 nudge 必须提示可调用 submit_findings"


@pytest.mark.asyncio
async def test_analysis_truncated_empty_retry_complements_truncation_hint():
    """Analysis 形态 A：截断历史提示与 nudge 同轮协同——事实归截断提示，归因归 nudge。"""
    agent = _make_analysis_agent(caps=None)
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "truncated"},
        {"form": "ok", "text": _ANALYSIS_FINAL_TEXT},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"任务应收尾成功: {result.error}"
    user_msgs = _user_messages(agent)
    trunc_hints = [c for c in user_msgs if "截断" in c]
    nudge_msgs = [c for c in user_msgs if "耗尽输出预算" in c]
    assert trunc_hints, "形态 A 必须保留 _record_llm_truncation 的截断历史提示"
    assert len(nudge_msgs) == 1, "形态 A 重试提示必须含预算耗尽 nudge"
    assert "截断" not in nudge_msgs[0], "nudge 不得重复截断事实"


@pytest.mark.asyncio
async def test_analysis_three_consecutive_empty_fallback_unchanged():
    """Analysis 连续 3 次形态 B 空响应：收口行为与改动前一致（失败结果 + 计数 3 +
    仅前 2 次注入重试提示，第 3 次到上限不再 append）。"""
    agent = _make_analysis_agent(caps=None)
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "reasoning_only"},
        {"form": "reasoning_only"},
        {"form": "reasoning_only"},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success is False, "连续 3 次空响应维持现有失败收口"
    assert "连续收到空响应" in (result.error or "")
    assert agent._empty_retry_count == 3
    assert len(_nudge_retry_messages(agent)) == 2, (
        "第 3 次到达上限直接 break，不得再 append 重试提示"
    )


@pytest.mark.asyncio
async def test_recon_reasoning_only_retry_contains_nudge_without_tools_hint(tmp_path):
    """Recon（永无 tools 协议）：形态 B 空响应重试提示含 nudge、不提 submit_findings。"""
    agent = _make_recon_agent()
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "reasoning_only"},
        {"form": "ok", "text": _RECON_FINAL_TEXT},
    ])

    result = await agent.run({
        "project_info": {"name": "p", "root": str(tmp_path)},
        "config": {},
    })

    assert result.success, f"任务应收尾成功: {result.error}"
    nudges = _nudge_retry_messages(agent)
    assert len(nudges) == 1
    assert "只输出了思考" in nudges[0]
    assert "submit_findings" not in nudges[0], "Recon 不传 tools，nudge 不得提 submit_findings"
    assert "收到空响应" in nudges[0]


@pytest.mark.asyncio
async def test_verification_reasoning_only_injects_nudge_and_other_keeps_generic():
    """Verification：形态 B 注入 nudge（tools 模式含 submit_findings）；
    other 形态维持原有英文泛化提示；最终 submit_findings 正常收尾。"""
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_verification_agent(caps=caps)
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "reasoning_only"},
        {"form": "other_empty"},
        {"form": "tool_calls",
         "tool_calls": [_tool_call("submit_findings", json.dumps(_VERIFICATION_PAYLOAD,
                                                                 ensure_ascii=False))]},
    ])

    result = await agent.run({
        "previous_results": {"findings": [dict(_VERIFICATION_INPUT_FINDING)]},
        "config": {},
    })

    assert result.success, f"submit_findings 应收尾成功: {result.error}"
    user_msgs = _user_messages(agent)
    nudge_msgs = [c for c in user_msgs if "只输出了思考" in c]
    assert len(nudge_msgs) == 1, "形态 B 必须注入 nudge"
    assert "submit_findings" in nudge_msgs[0], "tools 协议 nudge 必须提示 submit_findings"
    # other 形态：nudge 为空，维持原有英文泛化提示
    generic_msgs = [c for c in user_msgs if "Received empty response" in c]
    assert len(generic_msgs) == 1, "other 形态必须维持原有英文泛化提示"


@pytest.mark.asyncio
async def test_orchestrator_reasoning_only_retry_contains_nudge(monkeypatch):
    """Orchestrator tools 协议：形态 B 空响应重试提示含 nudge 与调度工具提示；
    计数/上限/sleep 机制不变，finish 正常收尾。"""
    monkeypatch.setattr(
        "app.services.agent.agents.orchestrator.asyncio.sleep", AsyncMock(),
    )
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_orchestrator_agent(caps=caps)
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "reasoning_only"},
        {"form": "reasoning_only"},
        {"form": "tool_calls", "tool_calls": [_tool_call("finish", "{}")]},
    ])

    result = await agent.run({"project_info": {}, "config": {}})

    assert result.success, f"finish 应收尾成功: {result.error}"
    nudges = _nudge_retry_messages(agent)
    assert len(nudges) == 2, f"两次空响应必须各注入 1 条 nudge，实际: {len(nudges)}"
    assert all("只输出了思考" in c for c in nudges)
    assert all("dispatch_agent" in c for c in nudges), (
        "orchestrator tools 协议 nudge 必须提示可直接调用调度工具"
    )
    assert agent._empty_retry_count == 0, "非空响应轮必须重置空响应计数"


@pytest.mark.asyncio
async def test_orchestrator_five_consecutive_empty_stop_unchanged(monkeypatch):
    """Orchestrator 连续 5 次空响应：停止编排行为与改动前一致（计数 5、error 事件、
    仅前 4 次注入重试提示）。"""
    monkeypatch.setattr(
        "app.services.agent.agents.orchestrator.asyncio.sleep", AsyncMock(),
    )
    caps = BackendCapabilities(tools=True, guided_json=False)
    agent = _make_orchestrator_agent(caps=caps)
    agent.llm_service.chat_completion_stream = _scripted_chat_stream([
        {"form": "reasoning_only"},
        {"form": "reasoning_only"},
        {"form": "reasoning_only"},
        {"form": "reasoning_only"},
        {"form": "reasoning_only"},
    ])

    await agent.run({"project_info": {}, "config": {}})

    assert agent._empty_retry_count == 5
    assert len(_nudge_retry_messages(agent)) == 4, (
        "第 5 次到达上限直接 break，不得再 append 重试提示"
    )
    error_texts = []
    for call in agent.event_emitter.emit.await_args_list:
        event_data = call.args[0]
        if event_data.event_type == "error":
            error_texts.append(event_data.message)
    assert any("连续收到空响应，停止编排" in t for t in error_texts), (
        f"5 连空必须发停止编排 error 事件，实际: {error_texts}"
    )
