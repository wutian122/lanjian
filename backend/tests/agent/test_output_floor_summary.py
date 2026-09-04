"""
sandbox-verification-hard-gate Task 10：强制总结维度级产出下限——解析校验与 data 回写

spec（finding-output-floor「强制总结 SHALL 有维度级产出下限」）：
- 强制总结轮对每个未覆盖维度（D1-D10 缺口）必须二选一：findings 数组输出候选
  （confidence 0.1-0.7、needs_verification=true），或 summary 中以
  "UNCOVERED_DIMENSION_EXEMPT: <维度标识> - <豁免理由>" 逐行书面豁免；
- 解析后候选数与豁免数回写结果 data：dimension_gaps_reported（{维度: candidate/exempt}）、
  output_floor_violated（0 候选且 0 豁免）；
- 首轮 0 候选 0 豁免 SHALL 追加一次重试提示，仍为空则 output_floor_violated=true
  （Task 11 orchestrator 侧据此记覆盖不足证据）。

覆盖：
- Part 1 纯函数：维度标识解析（维度键/Dn 前缀/vuln type/中英文别名）、
  豁免行提取（markdown 前缀/全角冒号/不可识别标签跳过）、下限报告聚合
  （候选/豁免/混合候选优先/未知类型候选仍计数）
- Part 2 _run_forced_summary 集成：首轮豁免不重试、空→重试成功、双空 violated、
  候选合并、重试提示文案、提示词含结构化豁免格式
- Part 3 run() data 回写：主循环 Final Answer 路径聚合候选维度；
  强制总结路径豁免/双空两形态回写 data
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.agents.analysis import (
    AnalysisAgent,
    _build_output_floor_report,
    _parse_dimension_exemptions,
    _resolve_dimension_label,
)

_TIMEOUT_CONFIG = {
    "llm_first_token_timeout": 30,
    "llm_stream_timeout": 60,
    "agent_timeout": 1800,
    "sub_agent_timeout": 600,
    "tool_timeout": 60,
}


def _make_service(caps=None):
    service = MagicMock()
    service.backend_capabilities = caps
    service.get_agent_timeout_config = MagicMock(return_value=_TIMEOUT_CONFIG)
    service.config = SimpleNamespace(max_tokens=8192)
    return service


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


def _make_agent():
    return AnalysisAgent(
        llm_service=_make_service(),
        tools={},
        event_emitter=_make_emitter(),
    )


def _summary_json(findings=None, summary=""):
    return json.dumps(
        {"findings": findings or [], "summary": summary},
        ensure_ascii=False,
    )


# ============ Part 1：维度标识与豁免行解析纯函数 ============

class TestResolveDimensionLabel:
    """维度标识 → DIMENSIONS 键：维度键/Dn 前缀 → vuln type 映射 → 中英文别名。"""

    def test_dimension_key_and_dn_prefix(self):
        assert _resolve_dimension_label("D6_ssrf") == "D6_ssrf"
        assert _resolve_dimension_label("d4_deserialization") == "D4_deserialization"
        # Dn 数字前缀（无后缀）
        assert _resolve_dimension_label("D10") == "D10_supply_chain"
        assert _resolve_dimension_label("d3") == "D3_authz"

    def test_vuln_type_labels(self):
        assert _resolve_dimension_label("ssrf") == "D6_ssrf"
        assert _resolve_dimension_label("deserialization") == "D4_deserialization"
        assert _resolve_dimension_label("xss") == "D1_injection"
        assert _resolve_dimension_label("hardcoded_secret") == "D7_crypto"
        assert _resolve_dimension_label("csrf") == "D3_authz"

    def test_chinese_and_english_aliases(self):
        assert _resolve_dimension_label("反序列化") == "D4_deserialization"
        assert _resolve_dimension_label("供应链") == "D10_supply_chain"
        assert _resolve_dimension_label("越权") == "D3_authz"
        assert _resolve_dimension_label("路径遍历") == "D5_file"
        assert _resolve_dimension_label("弱加密") == "D7_crypto"
        assert _resolve_dimension_label("业务逻辑") == "D9_business_logic"

    def test_authorization_not_misclassified_as_authentication(self):
        """英文词边界：authorization 归 D3_authz，不得被 'auth' 前缀误归 D2_auth。"""
        assert _resolve_dimension_label("authorization") == "D3_authz"
        assert _resolve_dimension_label("authentication") == "D2_auth"

    def test_unresolvable_label_returns_none(self):
        assert _resolve_dimension_label("区块链双花攻击") is None
        assert _resolve_dimension_label("") is None

    def test_label_with_markdown_bold_and_parenthesis(self):
        """标签带 markdown 粗体/括号说明仍可解析。"""
        assert _resolve_dimension_label("**D6_ssrf**") == "D6_ssrf"
        assert _resolve_dimension_label("SSRF（外部请求伪造）") == "D6_ssrf"


class TestParseDimensionExemptions:
    """summary 文本中 UNCOVERED_DIMENSION_EXEMPT 行提取 → {维度键: 理由}。"""

    def test_two_exempt_lines_dimension_key_and_vuln_type(self):
        summary = (
            "分析总结：项目为纯前端。\n"
            "UNCOVERED_DIMENSION_EXEMPT: D6_ssrf - 本项目无服务端外发请求入口\n"
            "UNCOVERED_DIMENSION_EXEMPT: deserialization - 无原生反序列化调用\n"
        )
        result = _parse_dimension_exemptions(summary)
        assert result == {
            "D6_ssrf": "本项目无服务端外发请求入口",
            "D4_deserialization": "无原生反序列化调用",
        }

    def test_markdown_list_prefix_and_fullwidth_colon(self):
        summary = (
            "- UNCOVERED_DIMENSION_EXEMPT: D5_file - 无文件上传接口\n"
            "＊UNCOVERED_DIMENSION_EXEMPT：ssrf：纯静态页面无 fetch 外发\n"
        )
        result = _parse_dimension_exemptions(summary)
        assert "D5_file" in result
        assert result["D5_file"] == "无文件上传接口"
        # 全角冒号行：标签 ssrf 与理由间仍可切分
        assert result.get("D6_ssrf") in ("纯静态页面无 fetch 外发", "ssrf：纯静态页面无 fetch 外发")

    def test_exempt_without_reason_still_counted(self):
        """只有维度标识无理由（行尾即结束）也算有效豁免。"""
        result = _parse_dimension_exemptions(
            "UNCOVERED_DIMENSION_EXEMPT: D10_supply_chain\n"
        )
        assert result == {"D10_supply_chain": ""}

    def test_unresolvable_label_skipped(self):
        """标签无法映射维度的行不计入（提示词已给合法标识表）。"""
        result = _parse_dimension_exemptions(
            "UNCOVERED_DIMENSION_EXEMPT: 区块链双花 - 本项目无区块链\n"
            "UNCOVERED_DIMENSION_EXEMPT: D8_config - 配置已审计无例外\n"
        )
        assert result == {"D8_config": "配置已审计无例外"}

    def test_non_string_summary_returns_empty(self):
        assert _parse_dimension_exemptions(None) == {}
        assert _parse_dimension_exemptions(123) == {}


class TestBuildOutputFloorReport:
    """parsed Final Answer → 维度级产出下限报告。"""

    def test_empty_findings_and_summary_is_violated(self):
        report = _build_output_floor_report({"findings": [], "summary": "无"})
        assert report["output_floor_violated"] is True
        assert report["dimension_gaps_reported"] == {}
        assert report["candidate_count"] == 0
        assert report["exempt_count"] == 0

    def test_candidates_mapped_to_dimensions(self):
        parsed = {
            "findings": [
                {"vulnerability_type": "ssrf", "title": "疑似 SSRF 候选", "confidence": 0.4},
                {"vulnerability_type": "deserialization", "title": "疑似反序列化", "confidence": 0.3},
            ],
            "summary": "",
        }
        report = _build_output_floor_report(parsed)
        assert report["output_floor_violated"] is False
        assert report["candidate_count"] == 2
        assert report["dimension_gaps_reported"] == {
            "D6_ssrf": "candidate",
            "D4_deserialization": "candidate",
        }

    def test_exempts_only_not_violated(self):
        parsed = {
            "findings": [],
            "summary": (
                "UNCOVERED_DIMENSION_EXEMPT: D6_ssrf - 无外发请求\n"
                "UNCOVERED_DIMENSION_EXEMPT: D7_crypto - 无加密逻辑\n"
            ),
        }
        report = _build_output_floor_report(parsed)
        assert report["output_floor_violated"] is False
        assert report["exempt_count"] == 2
        assert report["dimension_gaps_reported"] == {
            "D6_ssrf": "exempt",
            "D7_crypto": "exempt",
        }

    def test_mixed_same_dimension_candidate_wins(self):
        """同一维度既有候选又有豁免：候选优先（候选是实际产出）。"""
        parsed = {
            "findings": [{"vulnerability_type": "ssrf", "title": "疑似 SSRF"}],
            "summary": "UNCOVERED_DIMENSION_EXEMPT: D6_ssrf - 大部分外发被网关拦截",
        }
        report = _build_output_floor_report(parsed)
        assert report["dimension_gaps_reported"]["D6_ssrf"] == "candidate"
        assert report["output_floor_violated"] is False

    def test_unknown_vuln_type_candidate_still_counts(self):
        """vulnerability_type 无法映射维度的候选仍计入候选数（不触发 violated），
        仅不进维度映射。"""
        parsed = {
            "findings": [{"vulnerability_type": "量子侧信道", "title": "疑似候选"}],
            "summary": "",
        }
        report = _build_output_floor_report(parsed)
        assert report["candidate_count"] == 1
        assert report["output_floor_violated"] is False
        assert report["dimension_gaps_reported"] == {}

    def test_non_dict_findings_filtered(self):
        report = _build_output_floor_report(
            {"findings": ["junk", 123, None], "summary": ""}
        )
        assert report["candidate_count"] == 0
        assert report["output_floor_violated"] is True


# ============ Part 2：_run_forced_summary 集成 ============

def _scripted_llm_call(texts):
    """按调用次序返回脚本文本的假 stream_llm_call；calls 记录每次消息与 kwargs。"""
    calls = []
    state = {"i": 0}

    async def _fake(messages, **kwargs):
        idx = state["i"]
        state["i"] += 1
        calls.append({"messages": list(messages), "kwargs": kwargs})
        return texts[idx], 10

    _fake.calls = calls
    return _fake


class TestForcedSummaryFloorEnforcement:
    """_run_forced_summary 解析校验 + 一次重试 + floor_report 返回。"""

    @pytest.mark.asyncio
    async def test_first_round_exempts_no_retry(self):
        """首轮给出豁免：只调用 1 次，violated=False，豁免维度进报告。"""
        agent = _make_agent()
        agent.stream_llm_call = _scripted_llm_call([
            _summary_json(
                findings=[],
                summary=(
                    "UNCOVERED_DIMENSION_EXEMPT: D6_ssrf - 纯前端无外发请求\n"
                    "UNCOVERED_DIMENSION_EXEMPT: D4_deserialization - 无反序列化入口\n"
                ),
            ),
        ])

        findings, floor = await agent._run_forced_summary([])

        assert len(agent.stream_llm_call.calls) == 1
        assert floor["output_floor_violated"] is False
        assert floor["dimension_gaps_reported"] == {
            "D6_ssrf": "exempt",
            "D4_deserialization": "exempt",
        }
        assert findings == []

    @pytest.mark.asyncio
    async def test_empty_then_retry_with_exempts_succeeds(self):
        """首轮 0 候选 0 豁免 → 重试一次；次轮豁免 → violated=False。
        重试提示含"无效产出"与结构化豁免格式；首轮 assistant 输出入历史。"""
        agent = _make_agent()
        agent.stream_llm_call = _scripted_llm_call([
            _summary_json(findings=[], summary="分析完成，未发现问题。"),
            _summary_json(
                findings=[],
                summary="UNCOVERED_DIMENSION_EXEMPT: D6_ssrf - 本项目无外发 HTTP 调用\n",
            ),
        ])

        findings, floor = await agent._run_forced_summary([])

        assert len(agent.stream_llm_call.calls) == 2, "0 候选 0 豁免必须恰好重试 1 次"
        assert floor["output_floor_violated"] is False
        assert floor["dimension_gaps_reported"] == {"D6_ssrf": "exempt"}

        # 重试轮（第 2 次调用）的最后一条 user 消息是重试提示
        retry_messages = agent.stream_llm_call.calls[1]["messages"]
        last_user = [m for m in retry_messages if m["role"] == "user"][-1]["content"]
        assert "无效产出" in last_user
        assert "UNCOVERED_DIMENSION_EXEMPT" in last_user

        # 首轮 assistant 空总结已入历史（对话连贯，LLM 可见自己上次输出）
        roles = [m["role"] for m in retry_messages]
        assert roles[-2] == "assistant" or "assistant" in roles

    @pytest.mark.asyncio
    async def test_two_empty_rounds_violated_true_and_no_third_call(self):
        """连续两轮 0 候选 0 豁免 → output_floor_violated=True，且不触发第三次调用。"""
        agent = _make_agent()
        agent.stream_llm_call = _scripted_llm_call([
            _summary_json(findings=[], summary="无"),
            _summary_json(findings=[], summary="还是无"),
        ])

        findings, floor = await agent._run_forced_summary([])

        assert len(agent.stream_llm_call.calls) == 2
        assert floor["output_floor_violated"] is True
        assert floor["dimension_gaps_reported"] == {}
        assert findings == []

    @pytest.mark.asyncio
    async def test_first_round_candidates_merged_no_retry(self):
        """首轮产出候选：不重试，候选合并进返回的 all_findings。"""
        agent = _make_agent()
        candidate = {
            "vulnerability_type": "ssrf", "severity": "low",
            "title": "疑似 SSRF（网关部分缓解）", "confidence": 0.4,
            "needs_verification": True,
        }
        agent.stream_llm_call = _scripted_llm_call([
            _summary_json(findings=[candidate], summary="候选 1 条"),
        ])

        findings, floor = await agent._run_forced_summary([])

        assert len(agent.stream_llm_call.calls) == 1
        assert floor["output_floor_violated"] is False
        assert floor["dimension_gaps_reported"] == {"D6_ssrf": "candidate"}
        assert len(findings) == 1
        assert findings[0]["vulnerability_type"] == "ssrf"

    @pytest.mark.asyncio
    async def test_retry_round_candidates_merged(self):
        """首轮空、重试轮产出候选：候选合并进 findings，violated=False。"""
        agent = _make_agent()
        candidate = {
            "vulnerability_type": "deserialization", "severity": "low",
            "title": "疑似 pickle 加载", "confidence": 0.35,
            "needs_verification": True,
        }
        agent.stream_llm_call = _scripted_llm_call([
            _summary_json(findings=[], summary="无"),
            _summary_json(findings=[candidate], summary="补充候选 1 条"),
        ])

        findings, floor = await agent._run_forced_summary([])

        assert len(agent.stream_llm_call.calls) == 2
        assert floor["output_floor_violated"] is False
        assert floor["dimension_gaps_reported"] == {"D4_deserialization": "candidate"}
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_violated_first_round_emits_warning(self):
        """首轮违反下限 SHALL 发射 warning 事件（覆盖不足可观测）。"""
        agent = _make_agent()
        agent.stream_llm_call = _scripted_llm_call([
            _summary_json(findings=[], summary="无"),
            _summary_json(findings=[], summary="无"),
        ])

        await agent._run_forced_summary([])

        # emit_event 是 BaseAgent 真实方法，事件落在 event_emitter.emit
        warning_texts = [
            call.args[0].message
            for call in agent.event_emitter.emit.await_args_list
            if call.args and getattr(call.args[0], "event_type", None) == "warning"
        ]
        assert any("候选" in t and "豁免" in t for t in warning_texts), (
            f"应发射含候选/豁免语义的 warning，实际: {warning_texts}"
        )

    @pytest.mark.asyncio
    async def test_initial_prompt_documents_structured_exempt_format(self):
        """首轮提示词含 UNCOVERED_DIMENSION_EXEMPT 结构化格式与维度键清单。"""
        agent = _make_agent()
        agent.stream_llm_call = _scripted_llm_call([
            _summary_json(findings=[], summary="无"),
        ])

        await agent._run_forced_summary([])

        first_user = [
            m for m in agent.stream_llm_call.calls[0]["messages"]
            if m["role"] == "user"
        ][-1]["content"]
        assert "UNCOVERED_DIMENSION_EXEMPT" in first_user
        assert "D6_ssrf" in first_user and "D1_injection" in first_user
        # Task 9 维度级下限语义保留
        assert "候选" in first_user and "豁免" in first_user


# ============ Part 3：run() data 回写 ============

def _scripted_chat_stream(texts):
    """真实 chat_completion_stream 脚本：每次调用返回下一文本的单 done chunk。"""
    state = {"i": 0}

    async def _gen(messages=None, temperature=None, max_tokens=None, tools=None,
                   response_format=None):
        text = texts[state["i"]]
        state["i"] += 1
        yield {
            "type": "done", "content": text, "reasoning": "",
            "accumulated": text, "usage": {"total_tokens": 10},
            "finish_reason": "stop",
        }

    return _gen


def _make_run_agent(stream_texts):
    service = _make_service(caps=None)
    service.chat_completion_stream = _scripted_chat_stream(stream_texts)
    agent = AnalysisAgent(llm_service=service, tools={}, event_emitter=_make_emitter())
    agent._check_token_budget_exceeded = lambda: False
    agent.config.max_iterations = 2
    return agent


_ANALYSIS_FINAL_TEXT = (
    "Thought: 分析完成，汇总发现\nFinal Answer: "
    + json.dumps({
        "summary": "发现 1 个 XSS 候选",
        "findings": [{
            "vulnerability_type": "xss", "severity": "low", "title": "反射型 XSS",
            "description": "未转义输出", "file_path": "src/app.py", "line_start": 12,
            "code_snippet": "innerHTML = user_input",
            "suggestion": "转义输出", "confidence": 0.6, "needs_verification": True,
        }],
    }, ensure_ascii=False)
)

# 主循环非 final 非空文本（无 Final Answer、无工具调用）→ 耗尽迭代进入强制总结
_NON_FINAL_TEXT = "Thought: 继续阅读代码\n我还需要检查几个文件，暂未形成结论。"


class TestRunDataFloorFields:
    """run() 返回 data 无论主循环/强制总结路径都含下限两字段。"""

    @pytest.mark.asyncio
    async def test_main_loop_final_answer_aggregates_candidate_dimensions(self):
        """主循环正常 Final Answer 路径：候选维度聚合为 candidate，violated=False。"""
        agent = _make_run_agent([_ANALYSIS_FINAL_TEXT])

        result = await agent.run({"project_info": {}, "config": {}})

        assert result.success, f"任务应收尾成功: {result.error}"
        assert result.data["output_floor_violated"] is False
        assert result.data["dimension_gaps_reported"] == {"D1_injection": "candidate"}

    @pytest.mark.asyncio
    async def test_forced_summary_exempt_path_writes_floor_fields(self):
        """迭代耗尽走强制总结、总结轮给出豁免：data 含 exempt 维度映射，violated=False。"""
        forced = _summary_json(
            findings=[],
            summary="UNCOVERED_DIMENSION_EXEMPT: D6_ssrf - 纯前端无外发请求\n",
        )
        agent = _make_run_agent([_NON_FINAL_TEXT, _NON_FINAL_TEXT, forced])

        result = await agent.run({"project_info": {}, "config": {}})

        assert result.success, f"任务应收尾成功: {result.error}"
        assert result.data["output_floor_violated"] is False
        assert result.data["dimension_gaps_reported"] == {"D6_ssrf": "exempt"}

    @pytest.mark.asyncio
    async def test_forced_summary_double_empty_sets_violated_in_data(self):
        """强制总结两轮全空：data.output_floor_violated=True（Task 11 据此收口）。"""
        empty_summary = _summary_json(findings=[], summary="无")
        agent = _make_run_agent([
            _NON_FINAL_TEXT, _NON_FINAL_TEXT, empty_summary, empty_summary,
        ])

        result = await agent.run({"project_info": {}, "config": {}})

        assert result.success, f"任务应收尾成功: {result.error}"
        assert result.data["output_floor_violated"] is True
        assert result.data["dimension_gaps_reported"] == {}
