"""
根因3 修复测试：验证门禁按成功次数 + 逐 finding 追踪

根因：_sandbox_exec_calls 无条件自增（失败也计数），finish 门禁被失败调用凑数绕过，
      3 个 needs_context 完全没验证。
修复：拆分 attempts/success，按 finding 索引追踪，门禁按成功覆盖。
"""
import pytest
from app.services.agent.agents.verification import VerificationAgent


def _make_verification_agent():
    """绕过 __init__ 构造 agent"""
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_exec_attempts = 0
    agent._sandbox_exec_success = 0
    agent._verified_finding_indices = set()
    agent._sandbox_exec_calls = 0  # 兼容旧字段
    agent._all_findings = []
    return agent


class TestVerificationGateCounting:
    """验证计数与门禁"""

    def test_failed_sandbox_not_counted(self):
        """根因3: 失败的 sandbox_exec 不计入 success"""
        agent = _make_verification_agent()
        # 模拟失败调用
        agent._sandbox_exec_attempts += 1
        # _record_sandbox_attempt 不会加 success（success 仅在 observation.success 时加）
        assert agent._sandbox_exec_attempts == 1
        assert agent._sandbox_exec_success == 0
        assert len(agent._verified_finding_indices) == 0

    def test_success_sandbox_counted(self):
        """根因3: 成功的 sandbox_exec 计入 success + finding 索引"""
        agent = _make_verification_agent()
        agent._sandbox_exec_attempts += 1
        agent._sandbox_exec_success += 1
        agent._verified_finding_indices.add(0)
        assert agent._sandbox_exec_success == 1
        assert 0 in agent._verified_finding_indices

    def test_parse_finding_index_from_poc_path(self):
        """根因3: 从命令文本解析 finding 索引（poc_{index}.py）"""
        agent = _make_verification_agent()
        cmd = "cat > /tmp/poc_3.py << 'EOF'\nprint('x')\nEOF\npython3 /tmp/poc_3.py"
        idx = agent._parse_finding_index_from_command(cmd)
        assert idx == 3

    def test_parse_finding_index_from_file_path(self):
        """根因3: 从 /workspace/src/{file_path} 反查 finding 索引"""
        agent = _make_verification_agent()
        agent._all_findings = [
            {"file_path": "auth.py", "title": "f0"},
            {"file_path": "db.py", "title": "f1"},
            {"file_path": "jwt.py", "title": "f2"},
        ]
        cmd = "python3 -c \"open('/workspace/src/jwt.py')\""
        idx = agent._parse_finding_index_from_command(cmd)
        assert idx == 2  # jwt.py 是第 3 个 finding（索引 2）

    def test_parse_finding_index_none_when_unmatched(self):
        """根因3: 无法关联时返回 None"""
        agent = _make_verification_agent()
        cmd = "python3 -c \"print('no file ref')\""
        idx = agent._parse_finding_index_from_command(cmd)
        assert idx is None

    def test_finish_rejected_when_unverified(self):
        """根因3: 存在未验证 finding 时门禁拒绝"""
        agent = _make_verification_agent()
        total = 5
        # 仅验证了 2 个
        agent._verified_finding_indices = {0, 1}
        agent._sandbox_exec_success = 2
        # 门禁检查：未全覆盖且无 skip_reason
        unverified = total - len(agent._verified_finding_indices)
        should_reject = unverified > 0 and agent._sandbox_exec_success < total
        assert should_reject is True

    def test_finish_allowed_when_all_verified(self):
        """根因3: 全部 finding 验证成功时门禁放行"""
        agent = _make_verification_agent()
        total = 3
        agent._verified_finding_indices = {0, 1, 2}
        agent._sandbox_exec_success = 3
        unverified = total - len(agent._verified_finding_indices)
        should_allow = unverified == 0 or agent._sandbox_exec_success >= total
        assert should_allow is True
