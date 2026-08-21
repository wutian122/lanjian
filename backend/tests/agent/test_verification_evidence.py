"""R1/R2/R3 验证证据根治测试：确定性状态引擎 + 反伪造 + 全量证据绑定。"""
from app.services.agent.agents.verification import (
    VerificationAgent,
    compute_verification_status,
)


def _make_agent():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    # ID 匹配分支的日志用到 self.name → self.config.name，mock 一个 config
    class _Cfg:
        name = "Verification"
    agent.config = _Cfg()
    return agent


def _finding(**overrides):
    f = {
        "title": "SSRF in MCP",
        "vulnerability_type": "ssrf",
        "file_path": "console/AppController.java",
        "line_start": 113,
        "verification_method": "sandbox_exec",
    }
    f.update(overrides)
    return f


# ============ R1: 确定性状态引擎 ============

def test_confirmed_from_evidence_even_when_llm_omitted_verdict():
    """有铁证（success+exit0+VULNERABILITY_CONFIRMED+匹配）但 LLM 未写 confirmed → 仍判 confirmed（根治根因1）。"""
    finding = _finding()
    attempts = [
        {
            "success": True,
            "exit_code": 0,
            "evidence_summary": "VULNERABILITY_CONFIRMED: SSRF via URI.create()",
            "target_ref": "console/AppController.java:113",
        }
    ]
    agent = _make_agent()
    agent._sandbox_attempts = attempts
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True


def test_no_evidence_is_needs_context():
    """无任何证据 → needs_context（LLM 自述 confirmed 不再被信任为起点）。"""
    finding = _finding()
    normalized = _make_agent()._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_attempts_without_confirmation_are_not_reproducible():
    """有尝试但无确认证据 → not_reproducible（跑了但没复现）。"""
    finding = _finding(sandbox_attempts=[{"success": True, "exit_code": 0, "evidence_summary": "no vuln marker"}])
    normalized = _make_agent()._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_false_positive_preserved():
    """LLM 显式 false_positive 且无 confirmed 证据 → false_positive。"""
    finding = _finding(verdict="false_positive", verification_status="false_positive")
    normalized = _make_agent()._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "false_positive"
    assert normalized["is_verified"] is False


# ============ R3: 反伪造 ============

def test_fabricated_attempt_marked_and_excluded():
    """Simulated + VULNERABILITY_CONFIRMED → fabricated=True，不判 confirmed（根治根因4）。"""
    agent = _make_agent()
    # 直接调用确定性引擎：伪造 attempt 不得作为证据
    finding = _finding()
    attempts = [
        {
            "success": True,
            "exit_code": 0,
            "fabricated": True,
            "evidence_summary": "Simulated trust-all context: verify_mode=0 ... VULNERABILITY_CONFIRMED",
            "target_ref": "console/AppController.java:113",
        }
    ]
    status, is_verified, _ = compute_verification_status(
        finding, attempts,
        attempt_has_vuln_evidence_fn=agent._attempt_has_vuln_evidence,
        attempt_matches_finding_fn=agent._sandbox_attempt_matches_finding,
    )
    assert status != "confirmed"
    assert is_verified is False


def test_fabricated_evidence_marked_in_record_sandbox_attempt():
    """_record_sandbox_attempt 对 Simulated + 确认标记输出打 fabricated=True 且 success=False。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 -c 'simulated poc'"},
        "Sandbox result\n退出码: 0\nSimulated trust-all context ... VULNERABILITY_CONFIRMED",
    )
    assert len(agent._sandbox_attempts) == 1
    a = agent._sandbox_attempts[0]
    assert a["fabricated"] is True
    assert a["success"] is False


def test_real_sandbox_evidence_not_fabricated():
    """真实读到源码并输出确认标记 → 不判 fabricated，可判 confirmed。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_0.py"},
        "Sandbox result\n退出码: 0\nSource: 4039 chars loaded\nVULNERABILITY_CONFIRMED: Trust-all X509TrustManager",
    )
    assert len(agent._sandbox_attempts) == 1
    a = agent._sandbox_attempts[0]
    assert a.get("fabricated") in (None, False)
    assert a["success"] is True


# ============ R2: 全量证据强制绑定 ============

def test_evidence_bound_for_all_findings_including_llm_omitted():
    """LLM Final Answer 漏报的 finding 仍获得运行时证据（根治根因2：4/5 丢失）。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "cat > /tmp/poc_0.py << 'POC_EOF' ... Target: console/AppController.java:113",
            "target_ref": "console/AppController.java:113",
            "evidence_summary": "VULNERABILITY_CONFIRMED: SSRF",
            "finding_id": "f1",
        },
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "cat > /tmp/poc_1.py << 'POC_EOF' ... Target: app/Other.java:10",
            "target_ref": "app/Other.java:10",
            "evidence_summary": "VULNERABILITY_CONFIRMED: XSS",
            "finding_id": "f2",
        },
    ]
    # 两个 finding，其中一个带 _sandbox_finding_id（ID 绑定）
    f1 = _finding(file_path="console/AppController.java", _sandbox_finding_id="f1")
    f2 = _finding(file_path="app/Other.java", vulnerability_type="xss", _sandbox_finding_id="f2")
    # 模拟 LLM Final Answer 只报告了 f1
    verified = []
    for f in (f1,):
        agent._attach_runtime_sandbox_attempts(f)
        verified.append(agent._normalize_verification_outcome(f))
    # R2 兜底：对全部 findings_to_verify 强制绑定
    agent._bind_runtime_evidence_to_all(verified, [f1, f2])
    assert len(verified) == 2
    # f2（LLM 漏报）现在也有证据并判 confirmed
    f2_merged = [vf for vf in verified if vf.get("file_path") == "app/Other.java"][0]
    assert f2_merged.get("sandbox_attempts")
    assert f2_merged["verification_status"] == "confirmed"
