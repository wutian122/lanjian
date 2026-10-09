"""X1/X2/X3：tool_calls 通道健壮化测试。

生产实证（任务 2ccc598a/059300c9）：
- sglang qwen3_coder parser 流式分片概率性损坏 tool_calls（name 插空格如
  "ve rification"、arguments 丢失/半截 JSON）；
- 四类 agent 的 tool_calls 通道旧实现只认 submit_findings 终态（recon 完全
  缺失）——中间工具（read_file 等）全部降级空 step 空名执行
  （missing positional argument），收口 findings 因半截 JSON 全丢。

契约：
- X1：非终态已知工具的 tool_calls → 非终态 step（action/action_input），
  走既有 execute_tool 链（analysis/verification/recon 三处泛化）；
- X2：坏 JSON（半截 submit_findings / dispatch 参数）经 json-repair 抢救；
- X3：function.name 空格/下划线损坏按已知集合归一修复（'read file'→
  'read_file'；"ve rification" 不在 orchestrator 已知集则原样交 Task 21）；
- orchestrator：坏 JSON 抢救后 Task 21 不再判 bad_json。
"""

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.analysis import AnalysisAgent, AnalysisStep
from app.services.agent.agents.orchestrator import OrchestratorAgent
from app.services.agent.agents.recon import ReconAgent, ReconStep
from app.services.agent.agents.verification import VerificationAgent, VerificationStep


def _stub_tools(*names: str) -> Dict[str, Any]:
    return {n: MagicMock() for n in names}


def _tc(name: Optional[str], arguments: Any) -> Dict[str, Any]:
    return {"name": name, "arguments": arguments}


class TestAnalysisGeneralization:
    def _agent(self) -> AnalysisAgent:
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.config = MagicMock()
        agent.config.name = "Analysis"
        agent.tools = _stub_tools("read_file", "search_code", "list_files")
        agent._last_empty_kind = None
        return agent

    def test_intermediate_tool_genericized(self):
        """X1：read_file 的 tool_calls → 非终态 step（走 execute_tool 链）"""
        agent = self._agent()
        step = agent._final_step_from_tool_calls(
            [_tc("read_file", json.dumps({"file_path": "src/http/core/x.c"}))]
        )
        assert step is not None and not step.is_final
        assert step.action == "read_file"
        assert step.action_input == {"file_path": "src/http/core/x.c"}

    def test_submit_findings_terminal_regression(self):
        """回归：submit_findings 完整 JSON 仍为终态"""
        agent = self._agent()
        payload = {"findings": [{"title": "t", "severity": "high"}], "summary": "s"}
        step = agent._final_step_from_tool_calls(
            [_tc("submit_findings", json.dumps(payload, ensure_ascii=False))]
        )
        assert step is not None and step.is_final
        assert len(step.final_answer["findings"]) == 1

    def test_broken_json_salvaged_by_json_repair(self):
        """X2：submit_findings 半截 JSON → json-repair 抢救出 findings（不再 0 发现）"""
        agent = self._agent()
        broken = '{"findings": [{"title": "alias path traversal", "severity": "high"'
        step = agent._final_step_from_tool_calls([_tc("submit_findings", broken)])
        assert step is not None and step.is_final, "半截 JSON 必须被 json-repair 抢救"
        assert isinstance(step.final_answer.get("findings"), list)

    def test_name_with_space_repaired(self):
        """X3：'read file' 空格损坏 → 归一修复为 read_file"""
        agent = self._agent()
        step = agent._final_step_from_tool_calls(
            [_tc("read file", json.dumps({"file_path": "x"}))]
        )
        assert step is not None and step.action == "read_file"

    def test_unknown_tool_returns_none(self):
        """未知工具名（不在工具表）→ None 降级文本解析"""
        agent = self._agent()
        step = agent._final_step_from_tool_calls([_tc("totally_unknown", "{}")])
        assert step is None

    def test_unsalvageable_returns_none(self):
        """name 缺失 / 非对象参数 → None 降级"""
        agent = self._agent()
        assert agent._final_step_from_tool_calls([_tc(None, "{}")]) is None
        assert agent._final_step_from_tool_calls([_tc("read_file", '"just a string"')]) is None


class TestVerificationGeneralization:
    def _agent(self) -> VerificationAgent:
        agent = VerificationAgent.__new__(VerificationAgent)
        agent.config = MagicMock()
        agent.config.name = "Verification"
        agent.tools = _stub_tools("sandbox_exec", "reverify")
        agent._last_empty_kind = None
        return agent

    def test_intermediate_sandbox_tool_genericized(self):
        agent = self._agent()
        step = agent._final_step_from_tool_calls(
            [_tc("sandbox_exec", json.dumps({"cmd": "gcc poc.c"}))]
        )
        assert step is not None and not step.is_final
        assert step.action == "sandbox_exec"

    def test_broken_submit_findings_salvaged(self):
        agent = self._agent()
        broken = '{"findings": [{"title": "ssrf via upstream", "is_verified": true'
        step = agent._final_step_from_tool_calls([_tc("submit_findings", broken)])
        assert step is not None and step.is_final
        assert step.final_answer["findings"][0]["title"].startswith("ssrf")


class TestReconGeneralization:
    def _agent(self) -> ReconAgent:
        agent = ReconAgent.__new__(ReconAgent)
        agent.config = MagicMock()
        agent.config.name = "Recon"
        agent.tools = _stub_tools("list_files", "read_file", "search_code")
        agent._last_empty_kind = None
        return agent

    def test_tool_call_step_mapped(self):
        """recon 补齐 tool_calls 处理（旧实现完全缺失）"""
        agent = self._agent()
        step = agent._step_from_tool_calls_recon(
            [_tc("read_file", json.dumps({"file_path": "src/core/main.c"}))]
        )
        assert step is not None and not step.is_final
        assert step.action == "read_file"

    def test_unknown_returns_none(self):
        agent = self._agent()
        assert agent._step_from_tool_calls_recon([_tc("nope", "{}")]) is None


class TestOrchestratorRepair:
    def _agent(self) -> OrchestratorAgent:
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent.config = MagicMock()
        agent.config.name = "Orchestrator"
        agent._last_empty_kind = None
        return agent

    def test_dispatch_name_space_repaired(self):
        """X3：'dispatch_ agent' 空格损坏 → 修复为 dispatch_agent（Task 21 不再拦）"""
        agent = self._agent()
        step = agent._step_from_tool_calls(
            [_tc("dispatch_ agent", json.dumps({"agent": "recon", "task": "侦察"}))]
        )
        assert step is not None and step.action == "dispatch_agent"
        assert step.action_input == {"agent": "recon", "task": "侦察"}

    def test_broken_dispatch_args_salvaged(self):
        """X2：dispatch 参数半截 JSON → json-repair 抢救，Task 21 不再判 bad_json"""
        agent = self._agent()
        step = agent._step_from_tool_calls(
            [_tc("dispatch_agent", '{"agent": "analysis", "task": "深入分析 prox')]
        )
        assert step is not None
        assert step.action_input.get("agent") == "analysis"

    def test_ve_rification_not_force_repaired(self):
        """'ve rification' 不在 orchestrator 已知集——原样保留交 Task 21
        unknown_name 分类（不误改为其他工具）"""
        agent = self._agent()
        step = agent._step_from_tool_calls(
            [_tc("ve rification", json.dumps({"agent": "recon"}))]
        )
        assert step is not None and step.action == "ve rification"
