"""P13 T1（验证呈现口径根治 · P0-A）：模板静态确认标记口径统一。

背景（老板反馈"有些漏洞没被沙箱验证；有些验证了却显示未验证"）：
沙箱默认断网，SSRF 等网络类 PoC 走降级分支打印**裸** `STATIC_CONFIRMED`
（verification.py SSRF 模板 degraded 分支），但状态引擎 `_record_sandbox_attempt`
的 static_evidence 判定只认 `VULNERABILITY_CONFIRMED(STATIC)` /
`VULNERABILITY_STATIC_ONLY` 两个 VULNERABILITY_ 前缀标记 → 这些模板实际产出的
裸标记（SSRF 降级 / insecure_cookie / expression_injection / 通用模板）被丢弃 →
终态跌 `not_reproducible`（"验证了却显示未验证"）。

契约（T1）：
1. static_evidence 识别集扩展为覆盖模板**真实产出**的裸标记——`STATIC_CONFIRMED`
   及带 `:<detail>` 后缀变体（大小写容忍），使"PoC 静态确认"证据不再空转；
2. 有静态确认证据但无动态复现 → `static_confirmed`（不再跌 not_reproducible），
   且不得被误升为 `confirmed`；
3. 网络类（ssrf）在"沙箱无网且未真实外联"降级时，verification_note 明确写
   "静态确认（沙箱无网不可动态复现）"；
4. 对照：真未复现（NO_SINK，无任何确认标记）仍 not_reproducible，不被误升。
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import VerificationAgent


def _record_agent() -> VerificationAgent:
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}

    class _Cfg:
        name = "Verification"

    agent.config = _Cfg()
    return agent


def _ssrf_finding() -> dict:
    return {
        "title": "SSRF via unvalidated URL",
        "vulnerability_type": "ssrf",
        "file_path": "app/ssrf_vuln.py",
        "line_start": 10,
        "_sandbox_finding_id": "f-t1-ssrf",
    }


def _record_static_attempt(marker_line: str, finding_id: str = "f-t1-ssrf") -> VerificationAgent:
    agent = _record_agent()
    obs = (
        "沙箱执行结果\n退出码: 0\n标准输出:\n```\n"
        "Source: 100 chars loaded\n"
        f"{marker_line}\n"
        "=== Verification Complete ===\n```"
    )
    agent._record_sandbox_attempt(
        {"command": f"# FINDING_ID:{finding_id}\npython3 /tmp/poc_0.py"},
        obs,
        finding_id=finding_id,
    )
    return agent


# ============ ① 裸 STATIC_CONFIRMED → static_confirmed（当前 not_reproducible）============

def test_bare_static_confirmed_marker_derives_static_confirmed():
    """裸 `STATIC_CONFIRMED:`（SSRF 降级分支实际产出）须被识别为 static_evidence，
    终态 static_confirmed（当前跌 not_reproducible = 缺陷本体）。"""
    agent = _record_static_attempt(
        "STATIC_CONFIRMED: SSRF sink present without URL filter/validate; "
        "degraded 检查 URL 解析逻辑 (bridge unavailable, 未真实联网)"
    )
    attempt = agent._sandbox_attempts[-1]
    assert attempt["static_evidence"] is True, "裸 STATIC_CONFIRMED 未被识别为静态证据"
    assert attempt["success"] is True
    # 静态证据不得被当作动态铁证
    assert agent._attempt_has_vuln_evidence(attempt) is False

    finding = _ssrf_finding()
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


def test_bare_static_confirmed_with_detail_suffix_recognized():
    """`STATIC_CONFIRMED:<detail>` 后缀变体（insecure_cookie/expression_injection/
    通用模板实际产出）须同样识别，后缀不得影响判定。"""
    for marker in (
        "STATIC_CONFIRMED: cookie missing flags: ['HttpOnly', 'Secure']",
        "STATIC_CONFIRMED: dynamic expression sinks found: 2",
        "STATIC_CONFIRMED: 源码存在可疑 sink pattern，需结合数据流人工复核",
    ):
        agent = _record_static_attempt(marker)
        attempt = agent._sandbox_attempts[-1]
        assert attempt["static_evidence"] is True, f"带 detail 后缀未识别: {marker}"

        finding = _ssrf_finding()
        agent._attach_runtime_sandbox_attempts(finding)
        normalized = agent._normalize_verification_outcome(finding)
        assert normalized["verification_status"] == "static_confirmed", marker
        assert normalized["is_verified"] is True


def test_static_confirmed_case_insensitive():
    """大小写容忍：模板/LLM 自写 PoC 可能产出小写/混合大小写标记。"""
    agent = _record_static_attempt("static_confirmed: ssrf sink present")
    attempt = agent._sandbox_attempts[-1]
    assert attempt["static_evidence"] is True


# ============ ② 网络类呈现：无网降级 note 口径 ============

def test_no_network_degradation_note_mentions_sandbox_offline():
    """SSRF 在沙箱无网、未真实外联的降级分支 → verification_note 明确写
    "静态确认（沙箱无网不可动态复现）"，消除"验证了却显示未验证"的错觉。"""
    agent = _record_static_attempt(
        "STATIC_CONFIRMED: SSRF sink present without URL filter/validate; "
        "degraded 检查 URL 解析逻辑 (bridge unavailable, 未真实联网)"
    )
    finding = _ssrf_finding()
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    note = str(normalized.get("verification_note") or "")
    assert "静态确认" in note
    assert "沙箱无网" in note, f"网络类降级 note 须注明沙箱无网: {note!r}"


# ============ ③ 对照：真未复现不被误升 ============

def test_no_sink_observation_stays_not_reproducible():
    """无确认标记（NO_SINK）→ 不打 static_evidence，终态 not_reproducible（不误升）。"""
    agent = _record_agent()
    obs = (
        "沙箱执行结果\n退出码: 0\n标准输出:\n```\n"
        "Source: 100 chars loaded\n"
        "NO_SINK: 目标源码未发现匹配的 sink pattern\n"
        "=== Verification Complete ===\n```"
    )
    agent._record_sandbox_attempt(
        {"command": "# FINDING_ID:f-t1-ns\npython3 /tmp/poc_0.py"},
        obs,
        finding_id="f-t1-ns",
    )
    attempt = agent._sandbox_attempts[-1]
    assert attempt["static_evidence"] is False
    assert agent._attempt_has_vuln_evidence(attempt) is False

    finding = _ssrf_finding()
    finding["_sandbox_finding_id"] = "f-t1-ns"
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


# ============ ④ P13 终审 Important #2：伪造检测与 static_evidence 解耦 ============

def _record_attempt_with_obs(obs_body: str, finding_id: str) -> VerificationAgent:
    agent = _record_agent()
    obs = (
        "沙箱执行结果\n退出码: 0\n标准输出:\n```\n"
        f"{obs_body}\n"
        "=== Verification Complete ===\n```"
    )
    agent._record_sandbox_attempt(
        {"command": f"# FINDING_ID:{finding_id}\npython3 /tmp/poc_0.py"},
        obs,
        finding_id=finding_id,
    )
    return agent


def test_simulated_static_confirmed_attempt_is_fabricated():
    """伪造标记（simulated）+ STATIC_CONFIRMED → fabricated=True。

    P13 终审 Important #2：fabricated 判定此前依赖 has_vuln_evidence（其定义
    排除了 static_evidence）→「Simulated + 静态确认标记」可逃逸伪造检测并
    被升级 static_confirmed（新洗白路径）。修复：fabricated 独立判定为
    「任意确认类标记（含 static_evidence）+ 伪造标记共现」。
    """
    agent = _record_attempt_with_obs(
        "simulated output for demo purposes\n"
        "STATIC_CONFIRMED: sink present without filter",
        "f-p13-fab",
    )
    attempt = agent._sandbox_attempts[-1]
    assert attempt["static_evidence"] is True
    assert attempt["fabricated"] is True, (
        "Simulated + STATIC_CONFIRMED 必须判伪造（当前逃逸：fabricated 依赖 "
        "has_vuln_evidence 而后者排除 static_evidence）"
    )
    assert attempt["success"] is False, "伪造证据强制降级为失败"


def test_source_not_found_static_confirmed_is_fabricated():
    """另一伪造标记族（source not found）与静态确认共现同样判伪造。"""
    agent = _record_attempt_with_obs(
        "Source not found: app/missing.c\n"
        "STATIC_CONFIRMED: sink present",
        "f-p13-fab2",
    )
    attempt = agent._sandbox_attempts[-1]
    assert attempt["fabricated"] is True


def test_real_static_confirmed_not_fabricated():
    """对照：真实源码读取 + STATIC_CONFIRMED（无任何伪造标记）→ fabricated=False，
    终态仍 static_confirmed（修复不得误伤真静态确认）。"""
    agent = _record_attempt_with_obs(
        "Source: 153724 chars loaded\n"
        "STATIC_CONFIRMED: SSRF sink present without URL filter",
        "f-p13-real",
    )
    attempt = agent._sandbox_attempts[-1]
    assert attempt["static_evidence"] is True
    assert attempt["fabricated"] is False, "真实静态确认被误判伪造"

    finding = _ssrf_finding()
    finding["_sandbox_finding_id"] = "f-p13-real"
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"


def test_fabricated_static_attempt_not_upgraded_end_to_end():
    """端到端：fabricated 静态确认 attempt 不得使 finding 升 static_confirmed
    （证据被伪造检测剔除 → 维持 needs_context）。"""
    agent = _record_attempt_with_obs(
        "simulated run\nSTATIC_CONFIRMED: sink present",
        "f-p13-fab3",
    )
    finding = _ssrf_finding()
    finding["_sandbox_finding_id"] = "f-p13-fab3"
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] != "static_confirmed", (
        "伪造的静态确认证据被采信并升级（洗白路径未封堵）"
    )
    assert normalized["is_verified"] is False
