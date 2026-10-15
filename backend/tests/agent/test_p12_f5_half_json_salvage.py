"""P12 Wave 0 · F5 回归夹具（提交轮半截 JSON 崩坏截断——降级语义版）。

事件证据（任务 8a3a713f，4 个交卷时刻 iter18/25/26/26 被 F2/F4 崩坏整轮丢弃，
样本含 SSRF、path_traversal 低置信候选原文）：charwall 在模型刚输出
``Action: submit_findings`` + 半截 JSON 后掐断，base.py degeneracy 分支把
三累计变量置空——整轮丢弃。

**降级语义（spike 闸门判定）**：T0 salvage 良率 spike 实测不达标
（164 样本/14 条交卷标记/可解析 85.7% 达标/三字段齐备 0% 不达标/乱码 0%）
→ 按裁决④与 T0 约定，Wave 1.3 抢救管线**降级为「只纠正不抢救」**：
- 含交卷协议标记的崩坏轮**整轮丢弃**（崩坏原文严禁进 SSE/前端/报告铁律不变）；
- 丢弃时**计数留痕**（``_degenerate_submit_marker_dropped``）供观测区分
  普通崩坏轮与交卷时刻崩坏（后续若复核良率达标再启用抢救）；
- 非交卷轮崩坏维持既有整轮丢弃（控制组）。

T0 阶段 RED 原因：计数留痕缺失（实现前恒 0）。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents.analysis import AnalysisAgent

TASK_ID = "f5f5f5f5-0000-4000-8000-000000000001"

_READ_ACTION = (
    'Thought: 读源码\n'
    'Action: read_file\n'
    'Action Input: {"file_path": "src/ngx_http_core_module.c"}'
)


def _half_submit_json(items):
    """构造未闭合的 submit_findings 半截 JSON（charwall 在 JSON 尾部掐断）。"""
    body = ",".join(json.dumps(it, ensure_ascii=False) for it in items)
    return (
        "Thought: 审计完成，提交报告\n"
        "Action: submit_findings\n"
        'Action Input: {"findings": [' + body
    )


def _candidate(i=0, file_path="src/ngx_http_core_module.c"):
    return {
        "vulnerability_type": "ssrf",
        "severity": "high",
        "title": f"upstream URL 透传可疑点 {i}",
        "description": "url 参数进入 proxy_pass 未见白名单",
        "file_path": file_path,
        "line_start": 1800 + i,
        "code_snippet": "ngx_http_proxy_pass(r, url)",
        "suggestion": "校验目标主机",
        "confidence": 0.35,
        "needs_verification": True,
    }


def _make_service(monkeypatch, rounds):
    """rounds: list，'read'=正常读码轮；其余为崩溃轮 accumulated 文本。"""
    state = {"i": 0}

    async def _read_gen():
        text = _READ_ACTION
        yield {
            "type": "token", "kind": "content", "content": text,
            "accumulated": text, "accumulated_content": text,
            "accumulated_reasoning": "",
        }
        yield {
            "type": "done", "content": text, "reasoning": "",
            "accumulated": text,
            "usage": {"total_tokens": 30}, "finish_reason": "stop",
        }

    async def _crash_gen(accumulated_text):
        prefix = accumulated_text
        wall = "Q" * 40
        yield {
            "type": "token", "kind": "content", "content": prefix,
            "accumulated": prefix, "accumulated_content": prefix,
            "accumulated_reasoning": "",
        }
        yield {
            "type": "token", "kind": "content", "content": wall,
            "accumulated": prefix + wall,
            "accumulated_content": prefix + wall,
            "accumulated_reasoning": "",
        }
        # charwall 掐断：done 永不被拉取

    def factory(messages=None, **kwargs):
        spec = rounds[state["i"]]
        state["i"] += 1
        if spec == "read":
            return _read_gen()
        return _crash_gen(spec)

    service = MagicMock()
    service.backend_capabilities = None
    service.get_agent_timeout_config = MagicMock(return_value={
        "llm_first_token_timeout": 30,
        "llm_stream_timeout": 60,
        "agent_timeout": 1800,
        "sub_agent_timeout": 600,
        "tool_timeout": 60,
    })
    service.config = SimpleNamespace(max_tokens=8192)
    service.chat_completion_stream = factory
    return service


def _make_agent(monkeypatch, tmp_path, rounds):
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    service = _make_service(monkeypatch, rounds)
    agent = AnalysisAgent(
        llm_service=service, tools={}, event_emitter=emitter, task_id=TASK_ID
    )
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    monkeypatch.setattr(
        agent, "_get_llm_rate_limiter",
        lambda: MagicMock(acquire=AsyncMock()),
    )
    # degenerate 连续记账会让端点健康单例进入 degraded 并真实 sleep 节流；
    # 本夹具只验证丢弃/留痕契约，patch 掉节流避免 5s×N 真实等待
    import app.services.agent.agents.base as base_mod

    async def _no_throttle(*a, **k):
        return None

    monkeypatch.setattr(
        base_mod.get_endpoint_health(), "maybe_throttle", _no_throttle
    )
    monkeypatch.setattr(
        agent, "execute_tool",
        AsyncMock(return_value="/* ngx_http_core_module.c 真实源码 */"),
    )
    monkeypatch.setattr("app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path))
    return agent, emitter


def _run_input(tmp_path):
    return {
        "project_info": {"name": "nginx", "root": str(tmp_path)},
        "config": {},
        "project_root": str(tmp_path),
        "previous_results": {},
    }


@pytest.mark.asyncio
async def test_f5_half_submit_json_on_charwall_dropped_with_counter(monkeypatch, tmp_path):
    """降级语义：提交轮 charwall 掐断（accumulated 含半截 submit_findings JSON）
    整轮丢弃（不抢救出候选），但计数留痕 _degenerate_submit_marker_dropped；
    崩坏原文严禁进固化事件面（铁律）。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ngx_http_core_module.c").write_text(
        "/* nginx source */\n", encoding="utf-8"
    )
    crash_text = _half_submit_json([_candidate()])
    rounds = ["read", crash_text, crash_text, crash_text]
    agent, emitter = _make_agent(monkeypatch, tmp_path, rounds)

    with patch("app.services.agent.agents.base.get_llm_circuit") as fake_cb:
        fake_cb.return_value.call = lambda fn: fn()
        result = await agent.run(_run_input(tmp_path))

    assert result.success is False, "三连崩坏仍为诚实失败终态"
    # 降级：不抢救——findings 不得凭空产出（salvage 管线不启用）
    assert result.data.get("findings") == [], (
        f"降级语义下不得抢救出候选: {result.data.get('findings')}"
    )
    # 计数留痕：三次含交卷标记的崩坏轮
    assert getattr(agent, "_degenerate_submit_marker_dropped", 0) == 3, (
        "含交卷标记的崩坏轮必须计数留痕（区分普通崩坏轮）"
    )

    # 铁律：崩坏原文（charwall / 半截 JSON）不得进固化事件面
    events = [c.args[0] for c in emitter.emit.await_args_list]
    transient = {"thinking_token", "content_token"}
    for e in events:
        if e.event_type in transient:
            continue
        blob = (e.message or "") + json.dumps(e.metadata or {}, ensure_ascii=False)
        assert "QQQQ" not in blob, "charwall 崩坏片段泄漏到固化事件"
        assert "Action: submit_findings" not in blob, "崩坏轮原文不得固化展示"
    # 崩坏轮的 content_end 必须清空 accumulated（read 轮合法携带文本，不纳入断言）
    content_ends = [
        e for e in events
        if e.event_type == "content_end" and (e.metadata or {}).get("accumulated") is not None
    ]
    degenerate_ends = [e for e in content_ends if "read_file" not in (e.metadata or {}).get("accumulated", "")]
    assert all((e.metadata or {}).get("accumulated", "") == "" for e in degenerate_ends)


@pytest.mark.asyncio
async def test_f5_non_submission_charwall_round_dropped_entirely(monkeypatch, tmp_path):
    """控制组：非交卷轮（accumulated 无交卷协议标记）charwall 掐断维持整轮丢弃，
    不伪造任何候选，且不计入交卷标记留痕。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ngx_http_core_module.c").write_text("/* x */\n", encoding="utf-8")
    # 纯思考内容 + charwall，无 submit_findings/Final Answer/findings 标记
    crash_text = "我正在思考 nginx 的模块机制，还没有任何结论" + "Q" * 40
    agent, _ = _make_agent(monkeypatch, tmp_path, ["read", crash_text, crash_text, crash_text])

    with patch("app.services.agent.agents.base.get_llm_circuit") as fake_cb:
        fake_cb.return_value.call = lambda fn: fn()
        result = await agent.run(_run_input(tmp_path))

    assert result.success is False
    assert result.data.get("findings") == [], "非交卷崩坏轮不得抢救出候选"
    assert getattr(agent, "_degenerate_submit_marker_dropped", 0) == 0, (
        "非交卷标记轮不得计入留痕计数"
    )


def test_f5_submit_marker_detection_regex():
    """标记检测单元：Action: submit_findings / Final Answer: { / "findings": 三种
    形态命中；普通崩坏文本与纯思考不命中。"""
    agent = AnalysisAgent.__new__(AnalysisAgent)
    assert agent._has_submit_marker("Action: submit_findings\nAction Input: {")
    assert agent._has_submit_marker('Final Answer: {"findings": []}')
    assert agent._has_submit_marker('{"findings": [{"a": 1}')
    assert not agent._has_submit_marker("我正在思考模块机制" + "Q" * 40)
    assert not agent._has_submit_marker("")
