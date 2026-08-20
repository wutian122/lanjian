"""
P1 修复测试：token 预算硬门禁

根因：token_budget 字段在 agent 代码中无任何引用，token 实际无上限
      （历史任务消耗 5.6M~12.7M，是 budget 的 56~127 倍）。
修复：orchestrator 主循环 + base.py 子 agent 循环检查 token_budget，
      超限设 COMPLETED_WITH_GAPS(reason=token_budget_exhausted) 并退出。
"""
import pytest
from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent(total_tokens=0, sub_tokens=0):
    """绕过 __init__ 构造 agent。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._total_tokens = total_tokens
    agent._sub_agent_total_tokens = sub_tokens
    agent._coverage_bypassed = False
    agent._coverage_bypass_info = {}
    agent._all_findings = []
    return agent


class TestTokenBudgetGate:
    """验证 token 预算门禁"""

    def test_token_budget_exceeded(self):
        """P1: 累计 token 达预算时返回 True"""
        agent = _make_agent(total_tokens=5000, sub_tokens=5000)
        # budget=10000, total=10000, 达预算
        assert agent._check_token_budget_exceeded(budget=10000) is True

    def test_token_budget_not_exceeded(self):
        """P1: 累计 token 未达预算时返回 False"""
        agent = _make_agent(total_tokens=1000, sub_tokens=2000)
        # budget=10000, total=3000, 未达
        assert agent._check_token_budget_exceeded(budget=10000) is False

    def test_token_budget_includes_sub_agent_tokens(self):
        """P1: 预算检查须聚合 orchestrator + 子 agent token"""
        agent = _make_agent(total_tokens=4000, sub_tokens=7000)
        # budget=10000, total=11000, 超
        assert agent._check_token_budget_exceeded(budget=10000) is True

    def test_token_budget_zero_budget_disabled(self):
        """P1: budget<=0 视为未启用门禁，返回 False"""
        agent = _make_agent(total_tokens=999999, sub_tokens=999999)
        assert agent._check_token_budget_exceeded(budget=0) is False
        assert agent._check_token_budget_exceeded(budget=-1) is False

    def test_token_budget_default_reads_from_config(self):
        """P1: 不传 budget 时从 get_agent_config() 读取（默认值 60M，见 README「Token 预算」）"""
        from app.services.agent.config import get_agent_config
        cfg = get_agent_config()
        # 默认预算 60,000,000（README 声明），非旧的 100K/10M
        assert cfg.token_budget == 60_000_000
