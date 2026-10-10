"""严标准计数锁定测试（P10-T4 2026-10-09 口径演进版）。

旧口径 verified_count=仅 confirmed；新口径在 is_verified=True 前提下
confirmed + static_confirmed 并列计入。
"""
from app.models.agent_task import VERIFIED_COUNT_STATUSES, finding_counts_as_verified


def test_strict_task_counts_are_computed_from_findings():
    findings = [
        {"verification_status": "confirmed"},
        {"verification_status": "false_positive"},
        {"verification_status": "not_reproducible"},
        {"verification_status": "needs_context"},
    ]

    counts = {
        "confirmed": sum(1 for f in findings if f["verification_status"] == "confirmed"),
        "false_positive": sum(1 for f in findings if f["verification_status"] == "false_positive"),
        "not_reproducible": sum(1 for f in findings if f["verification_status"] == "not_reproducible"),
        "needs_context": sum(1 for f in findings if f["verification_status"] == "needs_context"),
    }

    assert counts == {
        "confirmed": 1,
        "false_positive": 1,
        "not_reproducible": 1,
        "needs_context": 1,
    }


def test_verified_count_includes_confirmed_and_static_confirmed():
    """P10-T4：新口径合计 = confirmed + static_confirmed（均 is_verified）"""
    findings = [
        {"verification_status": "confirmed", "is_verified": True},
        {"verification_status": "false_positive", "is_verified": False},
        {"verification_status": "confirmed", "is_verified": True},
        {"verification_status": "static_confirmed", "is_verified": True},
        {"verification_status": "needs_context", "is_verified": False},
    ]

    verified_count = sum(1 for f in findings if finding_counts_as_verified(f))
    false_pos_count = sum(1 for f in findings if f["verification_status"] == "false_positive")

    assert verified_count == 3
    assert false_pos_count == 1


def test_static_confirmed_is_verified_in_response():
    """P10-T4：static_confirmed 的 is_verified=True（仅 confirmed 的旧锁定作废）"""
    findings = [
        {"verification_status": "confirmed", "is_verified": True},
        {"verification_status": "static_confirmed", "is_verified": True},
        {"verification_status": "false_positive", "is_verified": False},
        {"verification_status": "not_reproducible", "is_verified": False},
        {"verification_status": "needs_context", "is_verified": False},
    ]

    for f in findings:
        assert f["is_verified"] == (
            f["verification_status"] in VERIFIED_COUNT_STATUSES
        )


def test_verified_count_matches_verified_statuses_after_sandbox_evidence_preserved():
    findings = [
        {
            "verification_status": "confirmed",
            "is_verified": True,
            "sandbox_attempts": [{"success": True, "evidence_summary": "sandbox proof"}],
        },
        {
            "verification_status": "static_confirmed",
            "is_verified": True,
            "sandbox_attempts": [{"success": True, "evidence_summary": "code reasoning"}],
        },
        {"verification_status": "false_positive", "is_verified": False},
        {"verification_status": "not_reproducible", "is_verified": False},
    ]

    verified_count = sum(1 for f in findings if finding_counts_as_verified(f))
    false_pos_count = sum(1 for f in findings if f["verification_status"] == "false_positive")

    assert verified_count == 2
    assert false_pos_count == 1
    assert all(
        f["is_verified"] == (f["verification_status"] in VERIFIED_COUNT_STATUSES)
        for f in findings
    )
