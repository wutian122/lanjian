"""
P2 修复测试：覆盖率放行携带完整 coverage_info

根因：orchestrator 各放行分支 coverage_info 字段不全（分支1缺 reason）。
修复：提取 _build_coverage_bypass_info 统一构造 5 字段。
"""
import pytest
from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._hard_coverage_block_count = 5
    return agent


class TestCoverageBypassInfo:
    """验证 coverage_bypass_info 完整性"""

    def test_coverage_bypass_info_complete(self):
        """P2: 放行分支携带完整 coverage_info（5 字段）"""
        agent = _make_agent()
        info = agent._build_coverage_bypass_info(
            reason="coverage_gate_max_blocks_exceeded",
            covered_count=2,
            total_dimensions=10,
            gaps=["D2_auth", "D3_authz"],
            block_count=5,
        )
        required = {"reason", "covered_count", "total_dimensions", "gaps", "block_count"}
        assert required.issubset(info.keys()), f"缺字段: {required - info.keys()}"
        assert info["reason"] == "coverage_gate_max_blocks_exceeded"
        assert info["covered_count"] == 2
        assert info["total_dimensions"] == 10
        assert info["block_count"] == 5
        assert info["gaps"] == ["D2_auth", "D3_authz"]

    def test_coverage_bypass_info_handles_none_gaps(self):
        """P2: gaps 为 None 时返回空列表"""
        agent = _make_agent()
        info = agent._build_coverage_bypass_info(
            reason="token_budget_exhausted",
            covered_count=0,
            total_dimensions=10,
            gaps=None,
            block_count=0,
        )
        assert info["gaps"] == []

    def test_coverage_bypass_info_extra_fields(self):
        """P2: extra 字段（如 tokens_used）正确合并"""
        agent = _make_agent()
        info = agent._build_coverage_bypass_info(
            reason="token_budget_exhausted",
            covered_count=0,
            total_dimensions=10,
            gaps=[],
            block_count=0,
            extra={"tokens_used": 5600000, "budget": 10000000},
        )
        assert info["tokens_used"] == 5600000
        assert info["budget"] == 10000000
        # 5 个必填字段仍在
        assert {"reason", "covered_count", "total_dimensions", "gaps", "block_count"}.issubset(info.keys())
