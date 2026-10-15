"""P12 Wave 3 · 口径硬化与观测补齐单测。

覆盖（方案第二章 Wave 3；裁决③⑦⑧⑩）：
- 3.4 max_dispatch 常量化：两处引用 settings.LLM_MAX_DISPATCH_PER_AGENT（默认 3）；
- 3.5 P11 修正：F4 反向 boost 灰度开关默认关（详见 test_p11_degenerate_retry_boost.py
  的 TestF4ReverseBoost）；rp 基线统一 1.1（settings 断言）；
- 3.6 观测：degraded 公式纳入 garbled_drops；崩坏浪费 est 入 observations。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings as _settings
from app.services.agent.agents.orchestrator import OrchestratorAgent


class TestMaxDispatchConstant:
    def test_settings_default_is_three(self):
        assert _settings.LLM_MAX_DISPATCH_PER_AGENT == 3

    def test_orchestrator_reads_setting_not_hardcoded(self):
        """守卫与 finish 门禁两处引用共享常量（源码级防回潮：不得再出现裸 3 比较）。"""
        import inspect

        src = inspect.getsource(OrchestratorAgent)
        assert "LLM_MAX_DISPATCH_PER_AGENT" in src, "两处守卫必须引用共享常量"
        assert 'get("analysis", 0) >= 3' not in src, "finish 门禁不得再硬编码 3"


class TestRpBaselineUnified:
    def test_settings_rp_default_is_1_1(self):
        """裁决⑩：rp 基线与生产实测统一为 1.1（1.2 臂格式错误 40 次）。"""
        assert _settings.LLM_REPETITION_PENALTY == 1.1

    def test_f4_reverse_switch_default_off(self):
        """F4 反向 boost 灰度开关默认关（不改既有行为）。"""
        assert _settings.LLM_DEGENERATE_F4_REVERSE_BOOST is False


class TestDegradedFormulaIncludesGarbled:
    @pytest.mark.asyncio
    async def test_garbled_drops_count_toward_degraded(self):
        """崩坏主导的劣化计入 degraded（8a3a713f 实证 15 次崩坏但 degraded=False）。"""
        from app.api.v1.endpoints.agent_tasks import _compute_llm_health

        rows = []
        # 10 次调用：6 次崩坏 warning（无截断/空响应）→ 应判 degraded
        for _ in range(10):
            rows.append(("thinking_start", "开始思考..."))
        for _ in range(6):
            rows.append(("warning", "LLM 输出崩坏（charwall/loop），本轮内容丢弃并重试"))

        db = MagicMock()
        result = MagicMock()
        result.all.return_value = rows
        db.execute = AsyncMock(return_value=result)

        health = await _compute_llm_health(db, "task-x")

        assert health["llm_calls"] == 10
        assert health["garbled_drops"] == 6
        assert health["empty_responses"] == 0
        assert health["degraded"] is True, "崩坏率 60% 必须判 degraded（P12 Wave3）"

    @pytest.mark.asyncio
    async def test_low_garbled_rate_not_degraded(self):
        from app.api.v1.endpoints.agent_tasks import _compute_llm_health

        rows = [("thinking_start", "开始思考...") for _ in range(10)]
        rows.append(("warning", "LLM 输出崩坏（charwall/loop），本轮内容丢弃并重试"))

        db = MagicMock()
        result = MagicMock()
        result.all.return_value = rows
        db.execute = AsyncMock(return_value=result)

        health = await _compute_llm_health(db, "task-y")
        assert health["garbled_drops"] == 1
        assert health["degraded"] is False, "10% 崩坏率不判 degraded"


class TestDegenerateWasteObservations:
    @pytest.mark.asyncio
    async def test_unified_closeout_records_waste(self):
        """崩坏浪费 est 累计入 observations（gate=degenerate_waste）。"""
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent._gate_observations = []
        agent._semgrep_leads_registered = True  # 跳过 leads（本测只验 waste）
        agent._semgrep_fallback_applied = True
        agent._output_floor_violated = False
        agent._all_findings = []
        sub = SimpleNamespace(_degenerate_waste_est=1234)
        agent.sub_agents = {"analysis": sub}

        with patch.object(agent, "_apply_semgrep_fallback", AsyncMock(return_value=0)):
            await agent._unified_closeout()

        gates = {o.get("gate") for o in agent._gate_observations}
        assert "degenerate_waste" in gates
        entry = next(o for o in agent._gate_observations if o["gate"] == "degenerate_waste")
        assert "1234" in entry.get("reason", "")

    @pytest.mark.asyncio
    async def test_no_waste_no_observation(self):
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent._gate_observations = []
        agent._semgrep_leads_registered = True
        agent._semgrep_fallback_applied = True
        agent._output_floor_violated = False
        agent._all_findings = []
        agent.sub_agents = {}

        with patch.object(agent, "_apply_semgrep_fallback", AsyncMock(return_value=0)):
            await agent._unified_closeout()

        assert not [o for o in agent._gate_observations if o.get("gate") == "degenerate_waste"]


class TestDocNoisePath:
    def test_doc_paths_detected(self):
        f = OrchestratorAgent._is_doc_noise_path
        assert f("docs/xml/nginx/changes.xml")
        assert f("docs/guide/index.md")
        assert f("CHANGELOG.md")
        assert f("src/notes.rst")
        assert f("nginx-release-1.31.4/changes.xml")

    def test_code_paths_kept(self):
        f = OrchestratorAgent._is_doc_noise_path
        assert not f("src/ngx_http_core_module.c")
        assert not f("tests/test_x.py")     # 裁决⑦：测试代码可能是靶场
        assert not f("examples/demo.py")    # 不排 examples
        assert not f("")