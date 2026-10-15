"""P13 T2（P0-C 软证据兜底放宽）：分层软证据判定。

背景：软证据兜底（_normalize_verification_outcome）原要求「类型白名单 +
dataflow_path + code_snippet + confidence>=0.75 + verification_method」五项同时
满足，真实静态漏洞（confidence 常落 0.6~0.74 或 verification_method 缺失）易卡
needs_context/not_reproducible（"有些漏洞没被沙箱验证"错觉）。

P13 T2 改为分层：
- 强档（保持现行为）：五项齐（类型白名单 + dataflow + snippet + 0.75 + method）
  → static_confirmed；
- 中档（新增，降阈）：code_snippet 非空 + dataflow_path 非空 +
  confidence >= settings.SOFT_EVIDENCE_CONFIDENCE_THRESHOLD（默认 0.6）
  → static_confirmed，note 标注「软证据（降阈）」；
- 弱档：仅 code_snippet + confidence>=阈值（无 dataflow）→ 仍 needs_context
  （不升，防滥升）。

前置条件不放宽（P9 证据前置为准）：至少一个非 fabricated/非 infra_error 的真实
attempt，且任一 attempt 不得 poc_error——放宽与洗白防护不冲突。
"""

from app.services.agent.agents.verification import VerificationAgent


def _make_agent():
    return VerificationAgent.__new__(VerificationAgent)


def _finding(**overrides):
    """默认：真实执行过（exit 0，无动态铁证）的 SSRF finding，中档证据齐备。

    刻意不设 verification_method——中档（降阈）不要求 method，用于与强档区分。
    """
    f = {
        "title": "SSRF 元数据探测",
        "vulnerability_type": "ssrf",
        "file_path": "app/http.py",
        "line_start": 42,
        "dataflow_path": [{"source": "user_input", "sink": "requests.get"}],
        "code_snippet": "requests.get(url, timeout=2)",
        "ai_confidence": 0.65,
        # 真实执行过但无动态铁证 → 状态引擎判 not_reproducible，交软证据兜底
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "PoC executed in container: request sent, no confirmation marker",
                "command": "python3 /tmp/poc_0.py",
            }
        ],
    }
    f.update(overrides)
    return f


# ============ ① 中档（snippet + dataflow + 0.65）→ static_confirmed ============


def test_mid_tier_snippet_dataflow_below_075_upgraded():
    """P13 T2 核心：code_snippet + dataflow_path + confidence 0.65（<0.75 但>=0.6）
    且无 verification_method → 由 needs_context/not_reproducible 升 static_confirmed。

    RED（实现前）：旧判定要求 0.75 与 method，中档证据不满足 → not_reproducible。
    """
    agent = _make_agent()
    finding = _finding(ai_confidence=0.65)
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "static_confirmed"
    ), f"中档软证据应升 static_confirmed，got {normalized['verification_status']}"
    assert normalized["is_verified"] is True
    note = str(normalized.get("verification_note") or "")
    assert "软证据（降阈）" in note, f"中档须标注「软证据（降阈）」，got note={note!r}"


def test_mid_tier_threshold_is_configurable_via_settings():
    """阈值取自 settings.SOFT_EVIDENCE_CONFIDENCE_THRESHOLD（默认 0.6，可配）。"""
    from app.core.config import settings

    assert hasattr(settings, "SOFT_EVIDENCE_CONFIDENCE_THRESHOLD"), (
        "须新增 settings.SOFT_EVIDENCE_CONFIDENCE_THRESHOLD"
    )
    assert float(settings.SOFT_EVIDENCE_CONFIDENCE_THRESHOLD) == 0.6


def test_mid_tier_boundary_exactly_at_threshold_upgraded():
    """边界：confidence 恰等于阈值 0.6 → 中档升级（>= 语义）。"""
    agent = _make_agent()
    finding = _finding(ai_confidence=0.6)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


# ============ ② 弱档（仅 snippet + 0.65，无 dataflow）→ 仍 needs_context ============


def test_weak_tier_snippet_only_not_upgraded():
    """仅 code_snippet + confidence>=阈值（缺 dataflow_path）→ 不升，防滥升。

    真实执行过但无动态铁证 → not_reproducible；弱档证据不足以洗成 static_confirmed。
    """
    agent = _make_agent()
    finding = _finding(ai_confidence=0.65)
    finding.pop("dataflow_path")
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "not_reproducible"
    ), f"弱档不得升级，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False
    assert "软证据（降阈）" not in str(normalized.get("verification_note") or "")


# ============ ③ confidence 低于阈值（0.5）→ 不升 ============


def test_below_threshold_confidence_not_upgraded():
    """confidence 0.5 < 阈值 0.6 → 中档条件不满足，不升级。"""
    agent = _make_agent()
    finding = _finding(ai_confidence=0.5)
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "not_reproducible"
    ), f"低于阈值不得升级，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False


# ============ ④ 前置防护：全 infra_error attempt 不得因降阈而升级 ============


def test_all_infra_attempts_not_upgraded_by_relaxed_threshold():
    """全部真实 attempt 为 infra_error（镜像缺失/Docker 缺席）→ needs_context 保持，
    中档降阈不得洗白为 static_confirmed（P9 证据前置不放宽）。"""
    agent = _make_agent()
    finding = _finding(
        ai_confidence=0.65,
        sandbox_attempts=[
            {
                "success": False,
                "exit_code": None,
                "evidence_summary": "工具执行失败: Docker not available: Error while creating mount source path",
                "command": "python3 /tmp/poc_0.py",
                "infra_error": True,
            },
            {
                "success": False,
                "exit_code": None,
                "evidence_summary": "工具执行失败: No such image: lanjian-sandbox:latest (ImageNotFound)",
                "command": "python3 /tmp/poc_1.py",
                "infra_error": True,
            },
        ],
    )
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "needs_context"
    ), f"全 infra 不得被降阈软证据洗白，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False
    assert "静态确认" not in str(normalized.get("verification_note") or "")


# ============ ⑤ 前置防护：poc_error attempt 不得升级 ============


def test_poc_error_attempt_not_upgraded_by_relaxed_threshold():
    """REQ-VE-2 保持：任一 attempt poc_error（PoC 崩溃）→ 不得走软证据兜底，
    即使存在真实执行 attempt、中档证据齐备、降阈也不得升级。"""
    agent = _make_agent()
    finding = _finding(
        ai_confidence=0.65,
        sandbox_attempts=[
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "PoC executed, no confirmation marker",
                "command": "python3 /tmp/poc_0.py",
            },
            {
                "success": False,
                "exit_code": 1,
                "evidence_summary": "Traceback (most recent call last): re.error: unterminated subpattern",
                "command": "python3 /tmp/poc_1.py",
                "poc_error": True,
                "poc_error_type": "pre-generated PoC crashed",
            },
        ],
    )
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] != "static_confirmed"
    ), "存在 poc_error 时不得软证据升级"
    assert normalized["is_verified"] is False


def test_fabricated_attempt_not_counted_for_mid_tier():
    """仅 fabricated attempt 视同零执行：中档降阈也不得升级。"""
    agent = _make_agent()
    finding = _finding(
        ai_confidence=0.65,
        sandbox_attempts=[
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "VULNERABILITY_CONFIRMED: simulated demo output (not real)",
                "command": "python3 /tmp/poc_0.py",
                "fabricated": True,
            }
        ],
    )
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "needs_context"
    ), f"fabricated 不得满足中档前置，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False


# ============ ⑥ 强档回归：五件套齐 → static_confirmed（现行为不变）============


def test_strong_tier_unchanged_with_all_five_pieces():
    """五件套齐（含 verification_method + 0.9）→ static_confirmed，且不标注降阈。"""
    agent = _make_agent()
    finding = _finding(ai_confidence=0.9, verification_method="sandbox_exec")
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True
    assert "软证据（降阈）" not in str(normalized.get("verification_note") or "")


# ============ ⑦ P13 终审 Important #3：中档类型白名单收口 ============


def test_mid_tier_non_whitelisted_type_not_upgraded():
    """非白名单类型（other/自定义）+ snippet+dataflow+0.65 → 不升级。

    P13 终审 Important #3：中档刻意取消类型白名单超出任务书契约——任何
    vuln_type 只要 snippet+dataflow+阈值即升 static_confirmed（滥升面）。
    修复：中档保留 _type_ok（与强档同白名单，仅降阈）。
    """
    agent = _make_agent()
    for bad_type in ("other", "hardcoded_secret", "custom_unknown_type"):
        finding = _finding(vulnerability_type=bad_type, ai_confidence=0.65)
        normalized = agent._normalize_verification_outcome(finding)
        assert normalized["verification_status"] != "static_confirmed", (
            f"非白名单类型 {bad_type} 不得走中档升级（got "
            f"{normalized['verification_status']}）"
        )
        assert normalized["is_verified"] is False


def test_mid_tier_whitelisted_type_still_upgraded():
    """回归：白名单类型（ssrf）同条件仍升级（白名单收口不误伤）。"""
    agent = _make_agent()
    finding = _finding(vulnerability_type="ssrf", ai_confidence=0.65)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True
