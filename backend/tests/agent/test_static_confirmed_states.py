"""B3 严标准 + static_confirmed 状态测试。"""
from app.models.agent_task import VerificationStatus
from app.services.agent.agents.verification import VerificationAgent


def test_static_confirmed_is_valid_status():
    assert VerificationStatus.STATIC_CONFIRMED == "static_confirmed"
    assert VerificationStatus.STATIC_CONFIRMED in VerificationStatus.ALL


def test_confirmed_with_vuln_evidence_stays_confirmed():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "file_path": "app.py",
        "line_start": 10,
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "target_ref": "app.py:10",
                "evidence_summary": "VULNERABILITY_CONFIRMED: SSTI verified",
            }
        ],
    }
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True


def test_static_confirmed_when_weak_evidence_no_sandbox_but_soft_evidence():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "vulnerability_type": "path_traversal",
        "file_path": "app.py",
        "line_start": 10,
        "sandbox_attempts": [],
        "verification_method": "read_file + code analysis",
        "dataflow_path": [{"source": "input", "sink": "open"}],
        "code_snippet": "open(path)",
        "ai_confidence": 0.85,
    }
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


def test_not_reproducible_when_no_evidence():
    """R1: 无沙箱尝试且无软证据 → needs_context（未尝试），而非 not_reproducible。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "vulnerability_type": "path_traversal",
        "file_path": "app.py",
        "line_start": 10,
        "sandbox_attempts": [],
        "verification_method": "",
        "dataflow_path": None,
        "code_snippet": "",
        "ai_confidence": 0.3,
    }
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_static_confirmed_low_confidence_not_promoted():
    """ai_confidence < 0.75 不应升为 static_confirmed；无沙箱尝试 → needs_context。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "vulnerability_type": "xss",
        "file_path": "app.py",
        "line_start": 10,
        "sandbox_attempts": [],
        "verification_method": "read_file",
        "dataflow_path": [{"source": "input"}],
        "code_snippet": "innerHTML",
        "ai_confidence": 0.6,
    }
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_static_confirmed_excluded_from_verified_count_semantic():
    """static_confirmed 的 is_verified=True 但状态不是 confirmed。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "vulnerability_type": "ssrf",
        "file_path": "app.py",
        "line_start": 10,
        "sandbox_attempts": [],
        "verification_method": "code reasoning",
        "dataflow_path": [{"source": "url", "sink": "requests.get"}],
        "code_snippet": "requests.get(url)",
        "ai_confidence": 0.9,
    }
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == VerificationStatus.STATIC_CONFIRMED
    assert normalized["is_verified"] is True
    # verified_count 仅统计 CONFIRMED，static_confirmed 不计入
    assert normalized["verification_status"] != VerificationStatus.CONFIRMED
