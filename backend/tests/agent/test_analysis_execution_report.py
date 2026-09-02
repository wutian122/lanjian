"""
Task 4（fix-audit-observability-time-governance）：Analysis 执行状态上报

spec Requirement: Analysis 执行状态 MUST 上报以推进跨轮去重
- Analysis Agent 完成时（无论 findings 是否为 0），result.data SHALL 包含
  files_read（read_file 的 file_path 去重排序列表）与 grep_patterns
  （search_code 的 keyword 模式列表；pattern 为 LLM 偶发别名兜底）；
- 0 工具调用时两字段为空列表（显式 []，不是缺省缺席）；
- action_input 异常形态（None/非 dict/缺字段/空白值）不得导致聚合崩溃。

字段名取舍（design 第 5 节）：
- read_file 工具参数为 file_path（file_tool.py FileReadInput）；
- search_code 工具参数为 keyword（工具描述明确要求 keyword、禁用 pattern/query
  别名，但 design 保留 pattern 兜底以容忍 LLM 偶发违例）；
- semgrep_scan 参数为 target_path/rules（固定规则集枚举 p/security-audit 等），
  无 keyword/pattern 字段；rules 是规则集名而非"搜索模式"，上报进跨轮
  "已执行搜索（禁止重复）"提示会污染语义，故事实上不贡献（design 表达式
  对其自然取到 None，被防御过滤）。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent, AnalysisStep


# ---------- 轻量构造（照 Task 3 test_observation_truncation 的 mock 模式） ----------

def _make_analysis_agent(monkeypatch, max_iterations=12):
    agent = AnalysisAgent(llm_service=SimpleNamespace(), tools={})
    for name in (
        "emit_thinking", "emit_event", "emit_llm_decision", "emit_llm_thought",
        "emit_finding", "emit_llm_action", "emit_llm_observation", "emit_llm_complete",
    ):
        monkeypatch.setattr(agent, name, AsyncMock())
    monkeypatch.setattr(agent, "_check_token_budget_exceeded", lambda: False)
    agent.config.max_iterations = max_iterations
    return agent


_FINAL_ANSWER = (
    'Thought: 分析完成\n'
    'Final Answer: {"findings": [{"title": "t", "severity": "info"}], "summary": "done"}'
)


def _scripted_stream(actions):
    """按轮次返回脚本：actions 为 (action, action_input_json_dict) 列表，其后返回 Final Answer。"""
    calls = {"n": 0}

    async def _fake_stream(messages, **kwargs):
        i = calls["n"]
        calls["n"] += 1
        if i < len(actions):
            name, payload = actions[i]
            return (
                f'Thought: 执行 {name}\n'
                f'Action: {name}\n'
                f'Action Input: {payload}',
                10,
            )
        return (_FINAL_ANSWER, 10)

    return _fake_stream


# ---------- Scenario ①：上报与实际执行一致（去重 + 排序） ----------

@pytest.mark.asyncio
async def test_analysis_reports_files_read_and_grep_patterns_dedup_sorted(monkeypatch):
    """4 次 read_file（含 1 次重复路径）+ 2 次 search_code：
    files_read 去重排序为 3 项、grep_patterns 排序为 2 项，不多报不漏报。"""
    agent = _make_analysis_agent(monkeypatch)
    actions = [
        ("read_file", '{"file_path": "src/zebra.py"}'),
        ("read_file", '{"file_path": "src/alpha.py"}'),
        ("read_file", '{"file_path": "src/mid.py"}'),
        ("read_file", '{"file_path": "src/zebra.py"}'),  # 重复读取同一文件
        ("search_code", '{"keyword": "zzz_secret"}'),
        ("search_code", '{"keyword": "aaa_token"}'),
    ]
    monkeypatch.setattr(agent, "stream_llm_call", _scripted_stream(actions))
    monkeypatch.setattr(agent, "execute_tool", AsyncMock(return_value="observation ok"))

    result = await agent.run({"project_info": {}, "config": {}})
    assert result.success is True

    data = result.data
    # 去重：zebra.py 读两次只报一次；排序：字典序
    assert data["files_read"] == ["src/alpha.py", "src/mid.py", "src/zebra.py"]
    # 排序：aaa_token < zzz_secret
    assert data["grep_patterns"] == ["aaa_token", "zzz_secret"]
    # 与实际执行一致：恰为 6 个工具 step（4 read + 2 search）
    tool_steps = [s for s in agent._steps if s.action in ("read_file", "search_code")]
    assert len(tool_steps) == 6


# ---------- Scenario ②：0 工具调用 → 空列表（显式，不缺省） ----------

@pytest.mark.asyncio
async def test_analysis_zero_tool_calls_reports_empty_lists_present(monkeypatch):
    """首轮直接 Final Answer（0 工具调用）：两字段必须存在且为 []，不得缺省缺席。"""
    agent = _make_analysis_agent(monkeypatch)

    async def _final_only(messages, **kwargs):
        return (_FINAL_ANSWER, 10)

    monkeypatch.setattr(agent, "stream_llm_call", _final_only)
    monkeypatch.setattr(agent, "execute_tool", AsyncMock(return_value="unused"))

    result = await agent.run({"project_info": {}, "config": {}})
    assert result.success is True

    assert "files_read" in result.data, "0 工具调用时 files_read 必须显式存在（空列表），不得缺省"
    assert "grep_patterns" in result.data, "0 工具调用时 grep_patterns 必须显式存在（空列表），不得缺省"
    assert result.data["files_read"] == []
    assert result.data["grep_patterns"] == []


# ---------- 防御：action_input 异常形态不崩 + 字段取舍 ----------

def test_collect_report_defends_malformed_action_input_and_semgrep_exclusion():
    """聚合直接单测：None/非 dict/缺字段/空白值全部安全跳过；
    pattern 别名兜底生效；semgrep_scan（rules 固定枚举）不贡献 grep_patterns。"""
    agent = AnalysisAgent.__new__(AnalysisAgent)
    agent._steps = [
        AnalysisStep(thought="", action="read_file", action_input=None),
        AnalysisStep(thought="", action="read_file", action_input="not-a-dict"),
        AnalysisStep(thought="", action="read_file", action_input={}),
        AnalysisStep(thought="", action="read_file", action_input={"file_path": ""}),
        AnalysisStep(thought="", action="read_file", action_input={"file_path": "good.py"}),
        AnalysisStep(thought="", action=None, action_input={"file_path": "no-action.py"}),
        AnalysisStep(thought="", action="search_code", action_input=None),
        AnalysisStep(thought="", action="search_code", action_input={"keyword": "   "}),
        AnalysisStep(thought="", action="search_code", action_input={"pattern": "fallback_pat"}),
        AnalysisStep(thought="", action="search_code", action_input={"keyword": "real_kw"}),
        # semgrep_scan：只有 target_path/rules（固定规则集），无 keyword/pattern → 不贡献
        AnalysisStep(
            thought="", action="semgrep_scan",
            action_input={"target_path": ".", "rules": "p/security-audit"},
        ),
        # 无关工具不贡献
        AnalysisStep(thought="", action="list_files", action_input={"directory": "."}),
    ]

    report = agent._collect_execution_report()

    assert report["files_read"] == ["good.py"], "仅合法 file_path 上报，异常形态全部跳过"
    assert report["grep_patterns"] == ["fallback_pat", "real_kw"], (
        "keyword 优先、pattern 别名兜底；空白/None 跳过；semgrep rules 不得污染"
    )


def test_collect_report_empty_steps_returns_empty_lists():
    """无任何 step（防御边界）：两字段为空列表，不抛异常。"""
    agent = AnalysisAgent.__new__(AnalysisAgent)
    agent._steps = []

    report = agent._collect_execution_report()
    assert report == {"files_read": [], "grep_patterns": []}
