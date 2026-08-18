"""B1 误降级修复测试：沙箱已 VULNERABILITY_CONFIRMED 但路径不匹配时不误降级。"""
from app.services.agent.agents.verification import VerificationAgent


def test_sandbox_match_relaxed_for_confirmed_evidence():
    """模拟环境 PoC 含 VULNERABILITY_CONFIRMED + vuln_type 匹配 → 判定有沙箱证据。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    attempt = {
        "success": True,
        "exit_code": 0,
        "target_ref": "/tmp/test_root/../../../etc/passwd",
        "evidence_summary": "VULNERABILITY_CONFIRMED: Path traversal escapes root directory",
    }
    finding = {
        "vulnerability_type": "path_traversal",
        "file_path": "openhands/app_server/file_store/local.py",
        "line_start": 21,
        "title": "LocalFileStore path traversal",
    }
    assert agent._sandbox_attempt_matches_finding(attempt, finding) is True


def test_sandbox_match_not_overmatch_unrelated():
    """VULNERABILITY_CONFIRMED 但 vuln_type/title 都不匹配 → 不误关联。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    attempt = {
        "success": True,
        "exit_code": 0,
        "target_ref": "/tmp/test_root",
        "evidence_summary": "VULNERABILITY_CONFIRMED: SQL injection verified",
    }
    finding = {
        "vulnerability_type": "path_traversal",
        "file_path": "app/server.py",
        "line_start": 10,
        "title": "LocalFileStore path traversal",
    }
    assert agent._sandbox_attempt_matches_finding(attempt, finding) is False


def test_path_traversal_not_downgraded():
    """路径遍历 finding 沙箱已复现 → 不误降级 not_reproducible，判 confirmed。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "vulnerability_type": "path_traversal",
        "file_path": "openhands/app_server/file_store/local.py",
        "line_start": 21,
        "title": "LocalFileStore path traversal",
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "target_ref": "/tmp/test_root/../../../etc/passwd",
                "evidence_summary": "VULNERABILITY_CONFIRMED: Path traversal escapes root directory",
            }
        ],
    }
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True
