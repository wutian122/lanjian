"""Task 6: 软证据升级前置——非 infra 真实 attempt 非空。

spec delta「每个 finding 终态前 SHALL 至少一次沙箱执行或显式豁免标记」b 条：
软证据四件套（dataflow_path + code_snippet + ai_confidence>=0.75 +
verification_method）升级 static_confirmed 的前置条件 SHALL 增加
"存在非 fabricated 且非 infra_error 的真实 attempt"。

封堵 Task 1 review 探针 8b 实证的洗白链路：全 infra（needs_context）或零执行的
finding 仅凭四件套被升级为 static_confirmed/is_verified=True，note 里同时留
infra_error=True 自相矛盾。软证据语义是"有真实尝试但无动态铁证"时的代码推理
补充，不是零执行/基础设施故障的洗白通道。
"""

from app.services.agent.agents.verification import VerificationAgent


def _make_agent():
    return VerificationAgent.__new__(VerificationAgent)


def _soft_finding(**overrides):
    """四件套齐备的 SSRF finding（默认零 attempt）。"""
    f = {
        "title": "SSRF 元数据探测",
        "vulnerability_type": "ssrf",
        "file_path": "app/http.py",
        "line_start": 42,
        "verification_method": "sandbox_exec",
        "dataflow_path": [{"source": "user_input", "sink": "requests.get"}],
        "code_snippet": "requests.get(url, timeout=2)",
        "ai_confidence": 0.9,
        "sandbox_attempts": [],
    }
    f.update(overrides)
    return f


# ============ ① 全 infra attempt + 四件套：不得升级（needs_context 保持）============


def test_soft_evidence_all_infra_attempts_not_upgraded():
    """全部真实 attempt 为 infra_error（镜像缺失/Docker 缺席）→ needs_context 保持，
    四件套不得洗白为 static_confirmed；note 保留基础设施诊断且无"静态确认"自相矛盾。"""
    agent = _make_agent()
    finding = _soft_finding(
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
        ]
    )
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "needs_context"
    ), f"全 infra 不得被软证据洗白，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False
    note = str(normalized.get("verification_note") or "")
    assert "沙箱环境故障" in note, "needs_context 须保留基础设施诊断 note"
    assert "infra_error" in note, "infra_error 语义须保留在 note"
    assert "静态确认" not in note, "不得出现升级文案与 infra_error 自相矛盾"
    assert "沙箱受限" not in str(
        normalized.get("verification_method") or ""
    ), "未升级时 verification_method 不得追加软证据后缀"


# ============ ② 零 attempt + 四件套：不得升级（needs_context）============


def test_soft_evidence_zero_attempts_not_upgraded():
    """spec Scenario「零执行 + 四件套齐备不再直接 static_confirmed」：
    无任何沙箱 attempt → needs_context，代码推理不能替代执行。"""
    agent = _make_agent()
    finding = _soft_finding()
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "needs_context"
    ), f"零执行不得被软证据洗白，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False
    assert "静态确认" not in str(normalized.get("verification_note") or "")


# ============ ③ 真实执行过（未复现 exit 0）+ 四件套：升级保持（向上兼容）============


def test_soft_evidence_with_real_attempt_still_upgraded():
    """PoC 在沙箱内真实执行（exit 0）但无动态铁证 + 四件套齐备 → static_confirmed 保持。
    软证据是"有尝试无铁证"的代码推理补充，新前置不应误杀真实执行过的 finding。"""
    agent = _make_agent()
    finding = _soft_finding(
        sandbox_attempts=[
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "PoC executed in container: request sent, no confirmation marker observed",
                "command": "python3 /tmp/poc_0.py",
            }
        ]
    )
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "static_confirmed"
    ), f"真实执行过的 finding 软证据升级应保持，got {normalized['verification_status']}"
    assert normalized["is_verified"] is True
    assert "沙箱受限" in str(normalized.get("verification_method") or "")


# ============ ④ poc_error 与新前置的交互：崩溃 attempt 仍阻断升级 ============


def test_soft_evidence_poc_error_blocks_upgrade_even_with_real_attempt():
    """REQ-VE-2 既有规则保持：任一 attempt poc_error（PoC 崩溃）→ 不得软证据升级，
    即使同时存在真实执行 attempt（验证器故障不被洗成已确认）。"""
    agent = _make_agent()
    finding = _soft_finding(
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
        ]
    )
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] != "static_confirmed"
    ), "存在 poc_error 时不得软证据升级"
    assert normalized["is_verified"] is False


# ============ ⑤ fabricated attempt 不计入真实 attempt ============


def test_soft_evidence_fabricated_attempts_not_counted():
    """仅有 fabricated（伪造/演示输出被反伪造标记）attempt 视同零执行：
    新前置要求真实 attempt，fabricated 不得凑数。"""
    agent = _make_agent()
    finding = _soft_finding(
        sandbox_attempts=[
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "VULNERABILITY_CONFIRMED: simulated demo output (not real)",
                "command": "python3 /tmp/poc_0.py",
                "fabricated": True,
            }
        ]
    )
    normalized = agent._normalize_verification_outcome(finding)
    assert (
        normalized["verification_status"] == "needs_context"
    ), f"fabricated attempt 不得满足软证据前置，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False


# ============ ⑥ xss/Task 4 STATIC_ONLY 流不受新前置影响 ============


def test_static_only_attempt_confirmed_without_soft_evidence_pieces():
    """attempt 自带 static_evidence 标记（确定性 PoC 的 VULNERABILITY_STATIC_ONLY）→
    由状态引擎分支 2 直接推导 static_confirmed，不经软证据兜底块，新前置不影响。"""
    agent = _make_agent()
    finding = {
        "title": "XSS 反射点",
        "vulnerability_type": "xss",
        "file_path": "app/view.py",
        "line_start": 7,
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "VULNERABILITY_STATIC_ONLY: sink confirmed by source analysis",
                "command": "python3 /tmp/poc_xss.py",
                "static_evidence": True,
            }
        ],
    }
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True
