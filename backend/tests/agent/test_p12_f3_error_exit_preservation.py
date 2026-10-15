"""P12 Wave 0 · F3 回归夹具（批 1 三连 charwall 崩坏 → 确定性保全，禁裸 error）。

事件证据（任务 8a3a713f，seq 1447/1475/1511 三连 charwall）：第一批 analysis
仅 6 个工具动作即连续 3 次崩坏空响应，走「连续收到空响应，使用回退结果」错误
退出（analysis.py error_message 分支）——该分支不跑强制总结、已读码上下文全丢，
返回体只有裸 findings=[]（断链③）。

Wave 1.5 契约（方案第二章 1.5）：error_message 退出但此前已有 ≥3 个成功工具
动作时，不赌第二次 LLM（guided_json 两轮同通道同模型双崩已被证伪），确定性合成：
- salvage 候选（F5 管线，本用例为空）；
- steps 已读文件/搜索命中骨架写入返回 data（files_read/grep_patterns/steps）；
- failed_zero_output=true 替换硬编码 output_floor_violated=False。
失败返回体仍 success=False（R7 诚实终态），但证据不裸退。

T0 阶段 RED：当前 error 分支返回体无 failed_zero_output/files_read/steps。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent


def _read_action(file_path: str) -> str:
    return (
        f"Thought: 读取 {file_path}\n"
        f"Action: read_file\n"
        f"Action Input: {json.dumps({'file_path': file_path}, ensure_ascii=False)}"
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
    agent._last_empty_kind = None
    agent._last_tool_calls = None
    return agent


def _run_until_charwall_exit(monkeypatch, agent, files_read):
    """3 个成功 read_file 后连续 3 轮空响应（charwall 掐断形态：stream 返回空串）。"""
    script = [_read_action(p) for p in files_read] + ["", "", ""]

    async def _fake_stream(messages, **kwargs):
        i = len(calls)
        calls.append(i)
        return script[min(i, len(script) - 1)], 0

    calls = []
    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_fake_stream))

    observations = {
        f"src/{p.split('/')[-1]}": f"/* {p} 的真实源码内容，含危险函数 */"
        for p in files_read
    }

    async def _fake_execute(name, payload):
        return observations.get(payload.get("file_path", ""), "/* source */")

    monkeypatch.setattr(agent, "execute_tool", AsyncMock(side_effect=_fake_execute))
    return calls


@pytest.mark.asyncio
async def test_f3_charwall_exit_with_prior_tool_work_preserves_skeleton(monkeypatch):
    """三连 charwall 空响应退出（此前 4 次成功读码）：返回体必须确定性保全
    已读文件骨架，且标 failed_zero_output=true，不得裸 error 丢失上下文。"""
    agent = _agent(monkeypatch)
    files = ["src/ngx_http_core_module.c", "src/ngx_http_proxy.c", "src/ngx_http_upstream.c", "src/ngx_http_request.c"]
    calls = _run_until_charwall_exit(monkeypatch, agent, files)

    result = await agent.run({"project_info": {"name": "nginx"}, "config": {}})

    # 仍是诚实失败终态（不伪装成功）
    assert result.success is False
    assert "空响应" in (result.error or "")

    data = result.data
    assert data is not None and isinstance(data, dict)
    assert data.get("failed_zero_output") is True, (
        "崩溃零产出退出必须标 failed_zero_output=true（与模型拒交 floor 违规区分）"
    )
    # 骨架：已读文件不随裸 error 销毁
    assert data.get("files_read") == sorted(files), (
        f"必须确定性保全已读文件骨架，实际: {data.get('files_read')}"
    )
    steps = data.get("steps")
    assert isinstance(steps, list) and len(steps) >= 3, (
        f"必须回传已执行工具 steps 骨架，实际: {steps!r}"
    )
    assert all(s.get("action") for s in steps), "骨架 steps 需保留 action"
    # findings 通道存在（F5 salvage 候选经同一通道；本形态无 salvage 候选）
    assert data.get("findings") == []
    # 崩溃零产出不得被硬编码洗成 floor=False 之外的干净语义
    assert data.get("output_floor_violated") is False


@pytest.mark.asyncio
async def test_f3_charwall_exit_counts_only_successful_tool_actions(monkeypatch):
    """骨架按**成功**工具动作计数：失败的工具尝试（observation 确证失败）
    不得计入 files_read 骨架。3 次成功读码 + 1 次失败读码后三连空响应。"""
    agent = _agent(monkeypatch)
    good_files = ["src/a.c", "src/b.c", "src/c.c"]

    script = [_read_action("src/a.c"), _read_action("src/b.c"),
              _read_action("src/missing.c"), _read_action("src/c.c"),
              "", "", ""]
    calls = []

    async def _fake_stream(messages, **kwargs):
        i = len(calls)
        calls.append(i)
        return script[min(i, len(script) - 1)], 0

    monkeypatch.setattr(agent, "stream_llm_call", AsyncMock(side_effect=_fake_stream))

    async def _fake_execute(name, payload):
        if payload.get("file_path") == "src/missing.c":
            return "⚠️ 工具执行失败：文件不存在"
        return "/* source */"

    monkeypatch.setattr(agent, "execute_tool", AsyncMock(side_effect=_fake_execute))

    result = await agent.run({"project_info": {"name": "nginx"}, "config": {}})

    assert result.success is False
    data = result.data
    assert data.get("failed_zero_output") is True
    assert data.get("files_read") == sorted(good_files), (
        "失败工具尝试不得计入已读文件骨架（missing.c 必须缺席）"
    )
