"""Task 1: infra_error 标记与状态机分离——沙箱基础设施故障不再伪装成"漏洞未复现"。

覆盖 spec Requirement 「基础设施故障 MUST NOT 伪装成漏洞未复现」两个 Scenario：
  ① 全部 attempt infra_error → needs_context + 诊断说明，而非 not_reproducible
  ② 真实执行未复现仍为 not_reproducible（行为不变）

并补充：
  ③ 成功铁证 confirmed 不受 infra_error 分支干扰
  ④ fabricated attempt 与 infra_error 标记的交互（fabricated 排除 + infra_error 判定）
"""
from app.services.agent.agents.verification import (
    VerificationAgent,
    compute_verification_status,
    INFRA_ERROR_SIGNATURES,
)


def _make_agent():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}

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


# ============ Scenario 1: 全部 attempt infra_error → needs_context ============


def test_all_attempts_infra_error_returns_needs_context():
    """全部 attempt 因 ImageNotFound/Docker not available 失败 → needs_context 而非 not_reproducible。"""
    finding = _finding(
        sandbox_attempts=[
            {
                "success": False,
                "exit_code": -1,
                "evidence_summary": "工具执行失败: Docker not available: Error while creating mount source path",
                "infra_error": True,
            },
            {
                "success": False,
                "exit_code": -1,
                "evidence_summary": "工具执行失败: 沙箱环境不可用（Docker 未安装或未运行）",
                "infra_error": True,
            },
        ]
    )
    status, is_verified, notes = compute_verification_status(
        finding,
        finding["sandbox_attempts"],
        attempt_has_vuln_evidence_fn=lambda a: False,
        attempt_matches_finding_fn=lambda a, f: False,
    )
    assert status == "needs_context", f"expected needs_context, got {status}"
    assert is_verified is False
    assert notes.get("infra_error") is True
    assert "沙箱环境故障" in (notes.get("reason") or "")


def test_record_sandbox_attempt_marks_docker_not_available():
    """_record_sandbox_attempt 命中 'Docker not available' 签名 → infra_error=True。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_0.py"},
        "工具执行失败: Docker not available: Error while creating mount source path",
    )
    assert len(agent._sandbox_attempts) == 1
    assert agent._sandbox_attempts[0].get("infra_error") is True


def test_record_sandbox_attempt_marks_image_not_found():
    """_record_sandbox_attempt 命中 ImageNotFound/No such image 签名 → infra_error=True。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_1.py"},
        "Traceback: docker.errors.ImageNotFound: No such image: lanjian-sandbox:latest",
    )
    assert agent._sandbox_attempts[0].get("infra_error") is True


def test_record_sandbox_attempt_marks_pull_access_denied():
    """_record_sandbox_attempt 命中 'pull access denied' 签名 → infra_error=True。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_2.py"},
        "denied: pull access denied for lanjian-sandbox, repository does not exist",
    )
    assert agent._sandbox_attempts[0].get("infra_error") is True


def test_record_sandbox_attempt_normal_poc_error_not_infra():
    """普通 PoC 崩溃（Traceback 但无 docker 错误）→ 走 poc_error 分支，不打 infra_error。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_3.py"},
        "Traceback (most recent call last):\n  File 'poc.py', line 5\nNameError: name 'x' is not defined",
    )
    attempt = agent._sandbox_attempts[0]
    # 不应被错判为 infra_error
    assert attempt.get("infra_error") in (None, False)
    # 仍命中 poc_error
    assert attempt.get("poc_error") is True


# ============ Scenario 2: 真实执行未复现仍为 not_reproducible ============


def test_real_execution_not_reproducible_unchanged():
    """PoC 真实执行（exit_code=0）但无确认证据 → 仍 not_reproducible。"""
    finding = _finding(
        sandbox_attempts=[
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "ran poc, no marker",
                "infra_error": False,
            }
        ]
    )
    status, is_verified, _ = compute_verification_status(
        finding,
        finding["sandbox_attempts"],
        attempt_has_vuln_evidence_fn=lambda a: False,
        attempt_matches_finding_fn=lambda a, f: False,
    )
    assert status == "not_reproducible"
    assert is_verified is False


def test_mixed_infra_error_and_real_attempt_still_not_reproducible():
    """混合（1 infra_error + 1 真实执行未复现）→ 仍 not_reproducible（真实执行过）。"""
    finding = _finding(
        sandbox_attempts=[
            {
                "success": False,
                "exit_code": -1,
                "evidence_summary": "Docker not available",
                "infra_error": True,
            },
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "ran real poc, no marker",
                "infra_error": False,
            },
        ]
    )
    status, is_verified, _ = compute_verification_status(
        finding,
        finding["sandbox_attempts"],
        attempt_has_vuln_evidence_fn=lambda a: False,
        attempt_matches_finding_fn=lambda a, f: False,
    )
    # 部分 infra_error + 部分真实执行过 → 维持 not_reproducible（不冒充 needs_context）
    assert status == "not_reproducible"
    assert is_verified is False


# ============ Scenario 3: 成功铁证 confirmed 不受影响 ============


def test_confirmed_evidence_unaffected_by_infra_error_branch():
    """成功铁证 attempt 存在 → confirmed 优先于 infra_error 分支。"""
    finding = _finding(
        sandbox_attempts=[
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "VULNERABILITY_CONFIRMED: SSRF via URI.create()",
                "target_ref": "console/AppController.java:113",
            },
            {
                "success": False,
                "exit_code": -1,
                "evidence_summary": "Docker not available",
                "infra_error": True,
            },
        ]
    )
    status, is_verified, _ = compute_verification_status(
        finding,
        finding["sandbox_attempts"],
        attempt_has_vuln_evidence_fn=lambda a: True,
        attempt_matches_finding_fn=lambda a, f: True,
    )
    assert status == "confirmed"
    assert is_verified is True


# ============ Scenario 4: fabricated 与 infra_error 交互 ============


def test_fabricated_attempt_excluded_from_infra_error_judgement():
    """fabricated attempt 不计入 infra_error 全量判定（与既有 real_attempts 排除一致）。"""
    # 全部"真" attempt 均为 infra_error，但混一个 fabricated → 应判 needs_context
    finding = _finding(
        sandbox_attempts=[
            {
                "success": False,
                "exit_code": -1,
                "evidence_summary": "Docker not available",
                "infra_error": True,
            },
            {
                # fabricated：simulated + claimed confirmation, R3 反伪造降级
                "success": False,
                "exit_code": 0,
                "fabricated": True,
                "evidence_summary": "Simulated poc VULNERABILITY_CONFIRMED",
            },
        ]
    )
    status, is_verified, _ = compute_verification_status(
        finding,
        finding["sandbox_attempts"],
        attempt_has_vuln_evidence_fn=lambda a: False,
        attempt_matches_finding_fn=lambda a, f: False,
    )
    # real_attempts（排除 fabricated 后）只剩 1 个 infra_error → needs_context
    assert status == "needs_context"
    assert is_verified is False


# ============ 签名清单确认 ============


def test_infra_error_signatures_complete():
    """INFRA_ERROR_SIGNATURES 包含所有任务要求的 8 个签名（小写匹配）。"""
    required = {
        "docker not available",
        "沙箱环境不可用",
        "imagenotfound",
        "no such image",
        "pull access denied",
        "error while creating mount source path",
        "connection aborted",
        "connection refused",
    }
    assert required.issubset(set(INFRA_ERROR_SIGNATURES)), (
        f"missing signatures: {required - set(INFRA_ERROR_SIGNATURES)}"
    )
