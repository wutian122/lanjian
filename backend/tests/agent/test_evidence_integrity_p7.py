"""P7-1 验证证据绑定防伪（2026-10-07 工作流实证）。

证据（B 机 c8686a20 / A 机多任务实测）：
- L5 绑定失真：占位符命令 `python3 -c "... check setHttpOnly ..."`（实测
  SyntaxError）与动作描述命令被 LLM 自述短路绑定落库；真实执行未绑定。
- L6 口径分裂：`VULNERABILITY_CONFIRMED: false_positive - ...`（标记+语义
  冲突）被判"有漏洞证据"→ trace 显示 1 confirmed，落库又被 strict 过滤。

契约：
1. `_is_executable_command`：拒占位符（裸省略号/过短/空），放行真实命令；
2. `_attempt_has_vuln_evidence` 标记+语义双条件：VULNERABILITY_CONFIRMED
   后随否定语义（false_positive 等）不得作为证据；
3. 短路收紧：LLM 自述 attempt 仅当命令可执行且证据成立才短路；不可执行的
   自述 attempt 从落库列表剔除。
"""
import pytest

from app.services.agent.agents.verification import (
    VerificationAgent,
    _is_executable_command,
)


class TestExecutableCommandGuard:
    def test_placeholder_ellipsis_rejected(self):
        assert _is_executable_command('python3 -c "... check setHttpOnly ..."') is False

    def test_action_description_rejected(self):
        assert _is_executable_command("check setHttpOnly flag in source") is False

    def test_empty_rejected_short_real_accepted(self):
        # P8：空串拒；短真实命令（"ls"）不再因长度被误伤
        assert _is_executable_command("") is False
        assert _is_executable_command("ls") is True
        # 短描述句仍拒
        assert _is_executable_command("check the file") is False

    def test_real_poc_command_accepted(self):
        real = (
            "cat > /tmp/poc_1.py << 'POC_EOF'\n"
            "import sqlite3\n"
            "con = sqlite3.connect(':memory:')\n"
            "con.execute(\"SELECT * FROM u WHERE n='\" + payload + \"'\")\n"
            "POC_EOF\npython3 /tmp/poc_1.py"
        )
        assert _is_executable_command(real) is True

    def test_short_python_oneliner_accepted(self):
        assert _is_executable_command("python3 -c \"print(1+1)\"") is True


class TestMarkerSemanticsP7:
    def _agent(self):
        return VerificationAgent.__new__(VerificationAgent)

    def test_confirmed_with_false_positive_semantics_rejected(self):
        attempt = {
            "success": True, "exit_code": 0,
            "evidence_summary": "VULNERABILITY_CONFIRMED: false_positive - HttpOnly flag is explicitly set in source code.",
        }
        assert self._agent()._attempt_has_vuln_evidence(attempt) is False

    def test_confirmed_with_real_semantics_accepted(self):
        attempt = {
            "success": True, "exit_code": 0,
            "evidence_summary": "VULNERABILITY_CONFIRMED: exploit successful, payload executed",
        }
        assert self._agent()._attempt_has_vuln_evidence(attempt) is True


class TestShortcutTighteningP7:
    def test_placeholder_self_claim_does_not_shortcut(self):
        agent = VerificationAgent.__new__(VerificationAgent)
        from unittest.mock import MagicMock
        agent.config = MagicMock()
        agent.config.name = "Verification"
        agent._sandbox_attempts = [{
            "success": True, "exit_code": 0, "finding_id": "f1",
            "command": "cat > /tmp/real_poc.py << 'EOF'\nprint('x')\nEOF\npython3 /tmp/real_poc.py",
            "evidence_summary": "runtime evidence",
        }]
        agent._runtime_attempts_by_finding_id = {"f1": agent._sandbox_attempts}
        finding = {
            "title": "t", "file_path": "a.java", "_sandbox_finding_id": "f1",
            "sandbox_attempts": [{
                "success": True, "exit_code": 0,
                "command": 'python3 -c "... check setHttpOnly ..."',
                "evidence_summary": "VULNERABILITY_CONFIRMED: exploit successful",
            }],
        }
        agent._attach_runtime_sandbox_attempts(finding)
        # 占位符自述不得短路：运行时 attempt 应被绑定
        cmds = [a.get("command", "") for a in finding["sandbox_attempts"]]
        assert any("real_poc" in c for c in cmds), f"运行时证据未绑定: {cmds}"
        # 占位符命令被剔除
        assert not any("..." in c for c in cmds)
