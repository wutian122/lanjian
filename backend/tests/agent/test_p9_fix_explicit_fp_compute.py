"""P9 Fix-1（D10）：compute_verification_status 显式 FP 锁定缺失根治测试。

终审发现：compute_verification_status 第 1 步动态 confirmed 升级先于 FP 分支执行，
LLM 在 Final Answer 中显式 verdict=false_positive、但 finding 又绑定了含
VULNERABILITY_CONFIRMED 的真实 attempt（如 LLM 循环前已跑完的确定性 PoC）时，
finding 被错误升级为 confirmed，违反 D10「显式标注维持 FP」口径。

本文件覆盖：
1. 显式 FP + confirmed/static 铁证 → 维持 false_positive（锁定，核心缺陷）
2. 推导型 FP（无显式 verdict 标注）+ 新成功证据 → 如实重算（D10 另一半，防过头）
3. 显式 FP + 无 attempt / 全 infra 证据 → 维持 false_positive（回归守卫）
4. 显式 FP 锁定路径 verification_note 口径与既有显式 FP 路径一致（不新增说明）
"""
from app.services.agent.agents.verification import (
    VerificationAgent,
    compute_verification_status,
)


def _agent():
    """构造裸 VerificationAgent（不触发布料/LLM 装配）。"""
    return VerificationAgent.__new__(VerificationAgent)


def _confirmed_attempt():
    """含动态铁证、匹配目标 finding 的真实沙箱 attempt。"""
    return {
        "success": True,
        "exit_code": 0,
        "target_ref": "proj/app/sink.py:12",
        "evidence_summary": "VULNERABILITY_CONFIRMED: sql injection proof executed in sandbox",
    }


def _static_attempt():
    """成功执行但仅静态证据档（第 2 步 static_confirmed 输入）。"""
    return {
        "success": True,
        "exit_code": 0,
        "static_evidence": True,
        "target_ref": "proj/app/sink.py:12",
        "evidence_summary": "VULNERABILITY_CONFIRMED(STATIC): code reasoning only",
    }


def _finding(**overrides):
    base = {
        "file_path": "proj/app/sink.py",
        "line_start": 12,
        "vulnerability_type": "sql_injection",
        "title": "Sql Injection in sink.py",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1) 显式 FP 锁定：遇 confirmed/static 铁证维持 false_positive（核心 RED）
# ---------------------------------------------------------------------------
def test_explicit_fp_with_confirmed_attempt_stays_false_positive():
    """D10：LLM Final Answer 显式 verdict=false_positive，即使绑定含
    VULNERABILITY_CONFIRMED 的真实 attempt 也维持 FP，不被第 1 步升级 confirmed。"""
    agent = _agent()
    finding = _finding(verdict="false_positive", sandbox_attempts=[_confirmed_attempt()])

    out = agent._normalize_verification_outcome(finding)

    assert out["verification_status"] == "false_positive"
    assert out["verdict"] == "false_positive"
    assert out["is_verified"] is False


def test_explicit_fp_with_static_evidence_attempt_stays_false_positive():
    """D10：显式 FP 同样跳过第 2 步 static_confirmed 升级。"""
    agent = _agent()
    finding = _finding(verdict="false_positive", sandbox_attempts=[_static_attempt()])

    out = agent._normalize_verification_outcome(finding)

    assert out["verification_status"] == "false_positive"
    assert out["is_verified"] is False


# ---------------------------------------------------------------------------
# 2) 推导型 FP：无显式 verdict 标注时如实重算（防修复过头）
# ---------------------------------------------------------------------------
def test_derived_fp_with_confirmed_evidence_recomputes_confirmed():
    """D10 另一半：finding 仅带历史/推导 verification_status=false_positive、
    无显式 verdict=FP，新成功铁证到达时如实重算为 confirmed。"""
    agent = _agent()
    finding = _finding(
        verification_status="false_positive",
        sandbox_attempts=[_confirmed_attempt()],
    )

    out = agent._normalize_verification_outcome(finding)

    assert out["verification_status"] == "confirmed"
    assert out["is_verified"] is True


def test_derived_fp_with_static_evidence_recomputes_static_confirmed():
    """推导型 FP + 仅静态证据 → static_confirmed（第 2 步推导口径保留）。"""
    agent = _agent()
    finding = _finding(
        verification_status="false_positive",
        sandbox_attempts=[_static_attempt()],
    )

    out = agent._normalize_verification_outcome(finding)

    assert out["verification_status"] == "static_confirmed"
    assert out["is_verified"] is True


def test_reverify_path_derived_fp_recomputes_without_lock_flag():
    """agent_tasks endpoint PoC 重跑路径：历史状态经 verdict 位传入纯函数，
    但不注入显式锁定标志——历史 FP 遇新铁证如实重算为 confirmed（推导型）。"""
    finding_view = {"verdict": "false_positive"}

    status, is_verified, notes = compute_verification_status(
        finding_view, [_confirmed_attempt()]
    )

    assert status == "confirmed"
    assert is_verified is True


# ---------------------------------------------------------------------------
# 3) 显式 FP + 无 attempt / needs_context 类证据 → 维持 FP（回归守卫）
# ---------------------------------------------------------------------------
def test_explicit_fp_without_attempts_stays_false_positive():
    agent = _agent()
    finding = _finding(verdict="false_positive", sandbox_attempts=[])

    out = agent._normalize_verification_outcome(finding)

    assert out["verification_status"] == "false_positive"
    assert out["is_verified"] is False


def test_explicit_fp_with_all_infra_attempts_stays_false_positive():
    """显式 FP 在前时 infra 分支本就不可达；锁定提前后行为必须一致（FP）。"""
    agent = _agent()
    infra_attempt = {
        "success": False,
        "infra_error": True,
        "evidence_summary": "沙箱环境不可用",
    }
    finding = _finding(verdict="false_positive", sandbox_attempts=[infra_attempt])

    out = agent._normalize_verification_outcome(finding)

    assert out["verification_status"] == "false_positive"
    assert out["is_verified"] is False


# ---------------------------------------------------------------------------
# 4) 锁定路径 verification_note / 说明字段口径
# ---------------------------------------------------------------------------
def test_explicit_fp_lock_adds_no_verification_note():
    """与既有显式 FP 路径（无 confirmed 证据时第 3 步返回空 notes）口径一致：
    锁定本身不新增 verification_note，verified_at 为 None。"""
    agent = _agent()
    finding = _finding(verdict="false_positive", sandbox_attempts=[_confirmed_attempt()])

    out = agent._normalize_verification_outcome(finding)

    assert "verification_note" not in out
    assert out["verified_at"] is None


def test_explicit_fp_lock_preserves_preexisting_verification_note():
    """finding 自带 verification_note 时原样保留，锁定逻辑不覆盖也不追加。"""
    agent = _agent()
    finding = _finding(
        verdict="false_positive",
        sandbox_attempts=[_confirmed_attempt()],
        verification_note="人工已确认为测试桩代码，非真实 sink",
    )

    out = agent._normalize_verification_outcome(finding)

    assert out["verification_note"] == "人工已确认为测试桩代码，非真实 sink"
    assert out["verification_status"] == "false_positive"
