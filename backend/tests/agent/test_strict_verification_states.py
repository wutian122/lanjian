from app.services.agent.agents.verification import VerificationAgent


def test_confirmed_without_sandbox_evidence_is_downgraded():
    """R1: 无任何沙箱尝试 → needs_context（未尝试/无法验证），而非 not_reproducible。
    not_reproducible 语义是"尝试过但未复现"；无证据说明未尝试。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "sandbox_attempts": [],
        "failure_reason": "no sandbox proof",
    }

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_confirmed_with_sandbox_evidence_stays_confirmed():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/app_server/file_store/local.py",
        "line_start": 21,
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "target_ref": "openhands/app_server/file_store/local.py:21",
                "evidence_summary": "VULNERABILITY_CONFIRMED: path traversal proof",
            }
        ],
    }

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True


def test_false_positive_is_not_collapsed():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {"verification_status": "false_positive", "sandbox_attempts": [{"success": True}]}

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "false_positive"
    assert normalized["is_verified"] is False


def test_empty_finding_defaults_to_needs_context():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {}

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_likely_is_converted_to_needs_context():
    """R1: verdict=likely 但无任何沙箱尝试 → needs_context（不信任 LLM 自述，无证据未尝试）。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {"verdict": "likely", "sandbox_attempts": []}

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_uncertain_with_attempts_is_not_reproducible():
    """R1: verdict=uncertain 但有沙箱尝试（失败）→ not_reproducible（尝试过但未复现）。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {"verdict": "uncertain", "sandbox_attempts": [{"success": False}]}

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_confirmed_uses_runtime_sandbox_attempts_when_llm_omits_them():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 poc.py",
            "target_ref": "openhands/app_server/file_store/local.py:21",
            "evidence_summary": "VULNERABILITY_CONFIRMED: Path Traversal dynamically verified in sandbox",
        }
    ]

    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/app_server/file_store/local.py",
        "line_start": 21,
        "verification_details": "sandbox_exec verification in Docker sandbox succeeded",
    }

    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True
    assert normalized["sandbox_attempts"][0]["success"] is True


def test_failed_sandbox_attempt_does_not_confirm_finding():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "sandbox_attempts": [
            {
                "success": False,
                "exit_code": 1,
                "evidence_summary": "Traceback: PoC failed before reaching target",
            }
        ],
    }

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_runtime_sandbox_attempts_do_not_attach_to_unmatched_finding():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "target_ref": "openhands/app_server/file_store/local.py:21",
            "evidence_summary": "VULNERABILITY_CONFIRMED: path traversal sandbox proof",
        }
    ]

    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/server/app.py",
        "line_start": 1,
    }

    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)

    assert "sandbox_attempts" not in finding
    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_llm_supplied_unmatched_sandbox_attempt_does_not_confirm_finding():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/server/app.py",
        "line_start": 1,
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "target_ref": "openhands/app_server/file_store/local.py:21",
                "evidence_summary": "path traversal sandbox proof",
            }
        ],
    }

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_record_sandbox_attempt_zero_exit_ignores_incidental_failure_marker():
    """V6 B2（REQ-VE-2）：exit 0 时 stdout 正文里 incidental 的 'Traceback' 子串不再误杀成功输出。

    旧语义（任意失败子串全文匹配判失败）在 v6 收窄：子串级标记仅在 exit_code!=0
    或位于 stderr/错误段内生效。真失败识别的用例见
    tests/agent/test_verification_evidence.py 的
    test_exit0_with_stderr_traceback_still_fails 与 test_exit1_with_traceback_fails。
    """
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []

    agent._record_sandbox_attempt(
        {"command": "python3 -c \"print('Target: app.py:1')\""},
        "🐳 沙箱执行结果\n退出码: 0\n标准输出:\nTraceback: PoC failed before reaching target",
    )

    assert agent._sandbox_attempts[0]["success"] is True
    assert agent._sandbox_attempts[0]["target_ref"] == "app.py:1"


def test_record_sandbox_attempt_marks_nonzero_exit_as_failed():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []

    agent._record_sandbox_attempt(
        {"command": "python3 -c \"print('Target: app.py:1')\""},
        "🐳 沙箱执行结果\n退出码: 1\n标准输出:\nVerification Complete",
    )

    assert agent._sandbox_attempts[0]["success"] is False


def test_runtime_matching_attempt_is_merged_when_llm_supplies_invalid_attempts():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "target_ref": "openhands/app_server/file_store/local.py:21",
            "evidence_summary": "VULNERABILITY_CONFIRMED: runtime sandbox proof",
        }
    ]
    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/app_server/file_store/local.py",
        "line_start": 21,
        "sandbox_attempts": [
            {
                "success": False,
                "exit_code": 1,
                "target_ref": "openhands/app_server/file_store/local.py:21",
                "evidence_summary": "LLM supplied stale failed attempt",
            }
        ],
    }

    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)

    assert len(finding["sandbox_attempts"]) == 2
    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True


def test_record_sandbox_attempt_allows_success_with_standard_error_heading():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []

    agent._record_sandbox_attempt(
        {"command": "python3 -c \"print('Target: app.py:1')\""},
        "🐳 沙箱执行结果\n退出码: 0\n标准错误:\nwarning: noisy but not fatal",
    )

    assert agent._sandbox_attempts[0]["success"] is True


def test_attempt_without_line_does_not_match_finding_with_line():
    agent = VerificationAgent.__new__(VerificationAgent)
    attempt = {"success": True, "target_ref": "openhands/server/app.py"}
    finding = {"file_path": "openhands/server/app.py", "line_start": 80}

    assert agent._sandbox_attempt_matches_finding(attempt, finding) is False


def test_llm_supplied_success_with_nonzero_exit_does_not_confirm_finding():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/server/app.py",
        "line_start": 80,
        "sandbox_attempts": [
            {"success": True, "exit_code": 1, "target_ref": "openhands/server/app.py:80"}
        ],
    }

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_llm_supplied_success_without_exit_code_does_not_confirm_finding():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/server/app.py",
        "line_start": 80,
        "sandbox_attempts": [{"success": True, "target_ref": "openhands/server/app.py:80"}],
    }

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_llm_supplied_success_with_failure_summary_does_not_confirm_finding():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "verification_status": "confirmed",
        "file_path": "openhands/server/app.py",
        "line_start": 80,
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "target_ref": "openhands/server/app.py:80",
                "evidence_summary": "Traceback: PoC failed before reaching target",
            }
        ],
    }

    normalized = agent._normalize_verification_outcome(finding)

    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_attempt_with_line_matches_finding_without_line():
    agent = VerificationAgent.__new__(VerificationAgent)
    attempt = {
        "success": True,
        "exit_code": 0,
        "target_ref": "openhands/server/app.py:80",
        "evidence_summary": "VULNERABILITY_CONFIRMED: sandbox dynamically verified",
    }
    finding = {"file_path": "openhands/server/app.py"}

    assert agent._sandbox_attempt_matches_finding(attempt, finding) is True


def test_file_suffix_match_requires_path_segment_boundary():
    agent = VerificationAgent.__new__(VerificationAgent)
    attempt = {
        "success": True,
        "exit_code": 0,
        "target_ref": "src/badapp.py:80",
        "evidence_summary": "VULNERABILITY_CONFIRMED: sandbox dynamically verified",
    }
    finding = {"file_path": "app.py", "line_start": 80}

    assert agent._sandbox_attempt_matches_finding(attempt, finding) is False
