"""层 2a：Orchestrator 输出预算治理（2026-09-29 根治）。

生产实证（任务 c6d6cd09 / 327b6430）：orchestrator 4096 预算下 dispatch 任务
描述（n 文件清单 + 维度 + 格式包装）常态超限 → finish_reason=length 截断 →
坏 JSON → json-repair 续写崩坏 → 乱码任务文本传给子 Agent（"~ff files" 级）。
2048（P1.1 时期）→ 4096（P1.1）→ 8192（本次）：内容型截断必须在源头消灭。
"""
import pytest

from app.services.agent.config import get_agent_config, get_agent_type_config


class TestOrchestratorOutputBudget:
    def test_orchestrator_default_budget_8192(self):
        get_agent_config.cache_clear()
        assert get_agent_config().llm_max_tokens_orchestrator == 8192

    def test_agent_type_config_orchestrator(self):
        get_agent_config.cache_clear()
        assert get_agent_type_config("orchestrator").max_tokens == 8192

    def test_deep_agent_budgets_unchanged(self):
        get_agent_config.cache_clear()
        assert get_agent_type_config("recon").max_tokens == 8192
        assert get_agent_type_config("analysis").max_tokens == 8192
        assert get_agent_type_config("verification").max_tokens == 8192

    def test_forced_summary_budget_kept(self):
        get_agent_config.cache_clear()
        assert get_agent_config().llm_max_tokens_forced_summary == 32768
