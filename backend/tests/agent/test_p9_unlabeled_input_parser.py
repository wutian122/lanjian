"""P9 修复测试（2026-10-08）——T1 解析器组。

覆盖：
1. P9-1a BaseAgent._extract_unlabeled_input 无标签 JSON 提取
   （verification/analysis/recon 三 Agent 接入，参数化 9 例）
2. D8 Orchestrator._parse_llm_response 无标签提取接入
3. P9-1b 提取失败 → _subagent_format_nudge 格式重试
   （坏→好集成例；连续 8 次 gate 停止例）
4. P9-1c _enhance_missing_arg_error 模式组扩展（四模式+短路+execute_tool 兜底）

生产根因：约半数 LLM 文本输出省略 "Action Input:" 标签（参数完整、直接
跟在 Action 行后），旧解析器静默给 {} → 空参 TypeError，模型无法自愈。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# ============ 公共构造 ============

class _Captured:
    def __init__(self):
        self.events: list[tuple[str, str]] = []


class _FakeEmitter:
    """记录 emit 的事件，供断言 warning 可见性。"""

    def __init__(self, captured: _Captured):
        self.captured = captured

    async def emit(self, event) -> None:
        self.captured.events.append((event.event_type, event.message))


def _bare_verification_agent(max_iterations: int, captured: _Captured | None = None):
    """构造不走 __init__ 的 VerificationAgent，仅装主循环段所需属性。"""
    from app.services.agent.agents.verification import VerificationAgent

    agent = VerificationAgent.__new__(VerificationAgent)
    config = MagicMock()
    config.name = "Verification"
    config.max_iterations = max_iterations
    # run() 把 system_prompt 写入历史首条；置字符串避免 MagicMock 进历史
    config.system_prompt = "system prompt"
    agent.config = config
    agent.tools = {}
    agent.event_emitter = _FakeEmitter(captured) if captured is not None else None
    agent.parent_id = None
    agent.task_id = "t-p9"
    agent.trace_manager = None
    agent._agent_id = "agent_p9test"
    agent._state = MagicMock()
    agent._iteration = 0
    agent._total_tokens = 0
    agent._tool_calls = 0
    agent._cancelled = False
    agent._user_cancelled = False
    agent._cancel_callback = None
    agent._soft_stop = False
    agent._soft_stop_consumed = False
    agent._incoming_handoff = None
    agent._insights = []
    agent._work_completed = []
    agent._sub_format_retry = 0
    agent._gate_observations = []
    agent._timeout_config = {"tool_timeout": 60}
    agent._conversation_history = []
    # run() 在流式调用前读 backend_capabilities；显式置 None 走文本协议，
    # 避免 MagicMock 真值误启用 submit_findings 工具形态
    agent.llm_service = MagicMock()
    agent.llm_service.backend_capabilities = None
    return agent


def _patch_verification_heavy(agent) -> None:
    """屏蔽与本测试无关的重方法（PoC 构建/确定性执行/收口绑定）。"""
    agent._build_sandbox_commands = MagicMock(return_value=[])
    agent._prepare_sandbox_files = MagicMock(return_value="")
    agent._run_deterministic_sandbox_commands = AsyncMock()
    agent._finalize_findings_without_final_answer = MagicMock(
        side_effect=lambda findings: list(findings)
    )
    agent._backfill_original_metadata = MagicMock()
    agent._attach_runtime_sandbox_attempts = MagicMock()
    agent._bind_runtime_evidence_to_all = MagicMock()
    agent._bind_unbound_runtime_evidence = MagicMock()
    agent._trace_verification_results = MagicMock()
    agent._create_verification_handoff = MagicMock(return_value=None)


# ============ P9-1a：无标签提取参数化（三 Agent） ============

def _parse_with(agent_cls, text: str):
    agent = agent_cls.__new__(agent_cls)
    agent.config = MagicMock()
    agent.config.name = agent_cls.__name__
    return agent._parse_llm_response(text)


_PARSE_CASES = [
    pytest.param(
        "single_line",
        "Thought: 先读文件\nAction: read_file\n"
        '{"file_path": "a.py"}\n',
        {"file_path": "a.py"},
        id="single_line",
    ),
    pytest.param(
        "multi_line",
        "Thought: 多行 JSON\nAction: read_file\n"
        '{"file_path": "a.py",\n "start_line": 1}\n',
        {"file_path": "a.py", "start_line": 1},
        id="multi_line",
    ),
    pytest.param(
        "fenced",
        "Thought: 围栏 JSON\nAction: read_file\n"
        '```json\n{"file_path": "a.py"}\n```\n',
        {"file_path": "a.py"},
        id="fenced",
    ),
    pytest.param(
        "observation_truncated",
        "Thought: 截断到 Observation\nAction: read_file\n"
        '{"file_path": "a.py"}\nObservation: 这里是观察结果\n',
        {"file_path": "a.py"},
        id="observation_truncated",
    ),
    pytest.param(
        "thought_reference",
        # Thought 中引用 "Action: read_file" 不得造成假阴性，真正 Action 行须命中
        "Thought: 记住 Action: read_file 的写法\nAction: read_file\n"
        '{"file_path": "real.py"}\n',
        {"file_path": "real.py"},
        id="thought_reference_false_negative_guard",
    ),
    pytest.param(
        "exact_empty_object",
        "Thought: 无参工具\nAction: list_files\n{}\n",
        {},
        id="exact_empty_object_allowed",
    ),
    pytest.param(
        "bad_fragment_text",
        "Thought: 坏输出\nAction: read_file\nplease wait\n",
        None,
        id="bad_fragment_returns_none",
    ),
    pytest.param(
        "no_trailing_newline",
        "Thought: 尾换行省略\nAction: read_file\n"
        '{"file_path": "a.py"}',
        {"file_path": "a.py"},
        id="no_trailing_newline",
    ),
    pytest.param(
        "brace_fragment_repairs_to_list",
        "Thought: 坏花括号碎片\nAction: read_file\n{...}\n",
        None,
        id="degraded_brace_fragment_none",
    ),
]


@pytest.mark.parametrize("case_name,text,expected_input", _PARSE_CASES)
@pytest.mark.parametrize("agent_cls", [
    pytest.param(
        __import__(
            "app.services.agent.agents.verification", fromlist=["VerificationAgent"]
        ).VerificationAgent,
        id="verification",
    ),
    pytest.param(
        __import__(
            "app.services.agent.agents.analysis", fromlist=["AnalysisAgent"]
        ).AnalysisAgent,
        id="analysis",
    ),
    pytest.param(
        __import__(
            "app.services.agent.agents.recon", fromlist=["ReconAgent"]
        ).ReconAgent,
        id="recon",
    ),
])
def test_unlabeled_input_three_agents(agent_cls, case_name, text, expected_input):
    """P9-1a：三 Agent 对 9 种无标签/坏输出形态的提取行为一致。"""
    step = _parse_with(agent_cls, text)
    if expected_input is None:
        assert step is None, f"{case_name}: 坏碎片应返回 None，实际 {step!r}"
        return
    assert step is not None, f"{case_name}: 应解析成功"
    # 动作名由各用例文本决定（exact {} 用例为 list_files，其余为 read_file），
    # 参数化层只锁定输入；动作名另由 test_exact_empty_object_action_is_list_files 锁定
    assert step.action_input == expected_input, (
        f"{case_name}: 期望 {expected_input}，实际 {step.action_input}"
    )


def test_exact_empty_object_action_is_list_files():
    """精确 {} 用例动作名锁定 list_files（参数化用例共用文本）。"""
    from app.services.agent.agents.verification import VerificationAgent

    step = _parse_with(
        VerificationAgent, "Thought: t\nAction: list_files\n{}\n"
    )
    assert step is not None and step.action == "list_files"
    assert step.action_input == {}


# ============ P9-1b：格式 nudge 集成（坏 → 好） ============

BAD_OUTPUT = "Thought: 没想好\nAction: read_file\nplease wait\n"
GOOD_OUTPUT = (
    "Thought: 修正格式\nAction: read_file\n"
    '{"file_path": "a.py"}\n'
)


class TestSubagentFormatNudgeIntegration:
    def test_bad_then_good_two_calls(self, monkeypatch):
        """坏轮 nudge 不写 assistant 历史；好轮工具执行；计数归零；恰 2 次 LLM 调用。"""
        import app.services.agent.agents.verification as vmod

        # run() 内既有弹性预算会把 max_iterations 上调（20+per_finding*n），
        # 测试需锁定上限：坏、好两轮后 for 循环自然结束
        monkeypatch.setattr(vmod, "_elastic_budget", lambda n, p: 2)

        captured = _Captured()
        agent = _bare_verification_agent(max_iterations=2, captured=captured)
        _patch_verification_heavy(agent)
        agent.stream_llm_call = AsyncMock(
            side_effect=[(BAD_OUTPUT, 10), (GOOD_OUTPUT, 12)]
        )
        agent.execute_tool = AsyncMock(return_value="file contents here")

        result = asyncio.run(agent.run({
            "previous_results": {
                "findings": [
                    {"file_path": "a.py", "vulnerability_type": "sql_injection"}
                ]
            }
        }))

        # 恰 2 次 LLM 调用
        assert agent.stream_llm_call.call_count == 2
        # 好轮工具被执行（恰好 1 次，参数为提取出的 JSON）
        assert agent.execute_tool.call_count == 1
        call_args = agent.execute_tool.call_args
        assert call_args.args[0] == "read_file"
        assert call_args.args[1] == {"file_path": "a.py"}
        # 成功解析后格式计数归零
        assert agent._sub_format_retry == 0
        # run 正常返回
        assert result.success is True

        history = agent._conversation_history
        roles = [m["role"] for m in history]
        # run() 预置 system + initial user；坏轮只追加 user nudge（无 assistant）；
        # 好轮：assistant + observation
        assert roles == ["system", "user", "user", "assistant", "user"]
        # nudge 文案：含两种合法形态/次数/file_path 示例
        nudge_text = history[2]["content"]
        assert "第 1 次" in nudge_text
        assert "Action Input:" in nudge_text
        assert "file_path" in nudge_text
        # 坏输出原文不得出现在任何历史消息中
        assert all("please wait" not in m["content"] for m in history)
        # 好轮 assistant 原文保留
        assert history[3]["content"] == GOOD_OUTPUT

        # 前端可见格式 warning：文案含「输出格式」、不含「空响应」
        # （排除循环前"沙箱空环境"等既有 warning）
        warnings = [
            msg for typ, msg in captured.events
            if typ == "warning" and "输出格式" in msg
        ]
        assert len(warnings) == 1
        assert "输出格式" in warnings[0]
        assert "空响应" not in warnings[0]


# ============ P9-1b：连续 8 次格式错误 → gate 停止 ============

class TestSubagentFormatStallGate:
    def test_eight_consecutive_failures_stops(self, monkeypatch):
        """连续 8 次坏输出：第 8 轮记 gate + elastic-exit 豁免，停止重试（仅 8 次调用）。"""
        import app.services.agent.agents.verification as vmod

        # 锁定弹性预算上限为 10（保证 break 不是因迭代上限自然结束）
        monkeypatch.setattr(vmod, "_elastic_budget", lambda n, p: 10)

        captured = _Captured()
        agent = _bare_verification_agent(max_iterations=10, captured=captured)
        _patch_verification_heavy(agent)
        agent.stream_llm_call = AsyncMock(side_effect=[(BAD_OUTPUT, 5)] * 10)

        result = asyncio.run(agent.run({
            "previous_results": {
                "findings": [
                    {"file_path": "a.py", "vulnerability_type": "sql_injection"}
                ]
            }
        }))

        # 第 8 次 nudge 后即 break：不发生第 9/10 次 LLM 调用
        assert agent.stream_llm_call.call_count == 8
        # gate observation 留痕（恰 1 条）
        stalls = [
            o for o in agent._gate_observations
            if o.get("gate") == "subagent_format_stalled"
        ]
        assert len(stalls) == 1
        assert "8" in stalls[0]["reason"]
        # 未验证 finding 已写 elastic-exit 豁免
        findings = result.data["findings"]
        assert len(findings) == 1
        assert findings[0]["sandbox_skip_reason"] == "elastic_exit"
        # 全程无 assistant 历史（坏轮一律不写）
        assert all(m["role"] != "assistant" for m in agent._conversation_history)
        # 每轮都有前端可见格式 warning（排除循环前"沙箱空环境"等既有 warning）
        warnings = [
            msg for typ, msg in captured.events
            if typ == "warning" and "输出格式" in msg
        ]
        assert len(warnings) == 8
        # 8 份格式 nudge（循环还会注入既有强制 sandbox 引导消息，按 nudge
        # 标记「输出格式纠正」精确计数）
        nudge_msgs = [
            m for m in agent._conversation_history
            if m["role"] == "user" and "输出格式纠正" in str(m["content"])
        ]
        assert len(nudge_msgs) == 8
        # 计数 1→8 连续（每轮仅一次格式纠正，无重复）
        for i, m in enumerate(nudge_msgs, start=1):
            assert f"第 {i} 次" in str(m["content"])


# ============ D8：Orchestrator 无标签接入 ============

class TestOrchestratorUnlabeledD8:
    def _parse(self, text):
        from app.services.agent.agents.orchestrator import OrchestratorAgent

        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent.config = MagicMock()
        agent.config.name = "Orchestrator"
        return agent._parse_llm_response(text)

    def test_dispatch_agent_unlabeled_json_extracted(self):
        text = (
            "Thought: 先派 recon\nAction: dispatch_agent\n"
            '{"agent": "recon", "task": "侦察项目结构"}\n'
        )
        step = self._parse(text)
        assert step is not None
        assert step.action == "dispatch_agent"
        assert step.action_input == {
            "agent": "recon", "task": "侦察项目结构"
        }

    def test_bare_dispatch_agent_still_none(self):
        step = self._parse("Thought: t\nAction: dispatch_agent\n")
        assert step is None

    def test_bare_finish_remains_empty(self):
        step = self._parse("Thought: t\nAction: finish\n")
        assert step is not None and step.action_input == {}


# ============ P9-1c：_enhance_missing_arg_error 模式组 ============

class TestEnhanceMissingArgP9:
    def test_single_positional(self):
        raw = "FileReadTool._execute() missing 1 required positional argument: 'file_path'"
        out = BaseAgent_source()._enhance_missing_arg_error(raw)
        assert "file_path" in out
        assert "参数缺失" in out
        assert "参数示例" in out
        assert "JSON" in out

    def test_plural_positional_captures_all(self):
        raw = "X._execute() missing 2 required positional arguments: 'a' and 'b'"
        out = BaseAgent_source()._enhance_missing_arg_error(raw)
        assert "`a`" in out and "`b`" in out
        example = '"a": "<实际值>"'
        assert example in out and '"b": "<实际值>"' in out

    def test_keyword_only(self):
        raw = "X._execute() missing 1 required keyword-only argument: 'keyword'"
        out = BaseAgent_source()._enhance_missing_arg_error(raw)
        assert "`keyword`" in out
        assert "参数示例" in out

    def test_str_no_attr_shape_guidance(self):
        raw = "'str' object has no attribute 'get'"
        out = BaseAgent_source()._enhance_missing_arg_error(raw)
        assert "JSON" in out
        assert "字符串" in out
        assert "参数示例" in out

    def test_r1_chinese_missing(self):
        raw = "必填参数缺失: file_path"
        out = BaseAgent_source()._enhance_missing_arg_error(raw)
        assert "file_path" in out
        assert "参数示例" in out
        assert "JSON" in out

    def test_short_circuit_parameter_example(self):
        raw = "必填参数缺失: file_path\n参数示例：{\"file_path\": \"x\"}"
        assert BaseAgent_source()._enhance_missing_arg_error(raw) == raw

    def test_short_circuit_already_enhanced(self):
        raw = "某错误\n参数必须是完整 JSON 对象，请勿省略字段"
        assert BaseAgent_source()._enhance_missing_arg_error(raw) == raw

    def test_unrelated_error_untouched(self):
        raw = "connection refused"
        assert BaseAgent_source()._enhance_missing_arg_error(raw) == raw

    def test_execute_tool_exception_path_enhanced(self):
        """2129 兜底分支：工具 execute 抛 TypeError 时错误信息同样过增强。"""
        agent = _bare_verification_agent(max_iterations=1)
        bad_tool = MagicMock()
        bad_tool.execute = AsyncMock(
            side_effect=TypeError("'str' object has no attribute 'get'")
        )
        agent.tools = {"bad_tool": bad_tool}

        msg = asyncio.run(agent.execute_tool("bad_tool", {}))
        assert "错误信息" in msg
        assert "🎯" in msg
        assert "JSON" in msg
        assert "字符串" in msg


def BaseAgent_source():
    from app.services.agent.agents.base import BaseAgent

    return BaseAgent
