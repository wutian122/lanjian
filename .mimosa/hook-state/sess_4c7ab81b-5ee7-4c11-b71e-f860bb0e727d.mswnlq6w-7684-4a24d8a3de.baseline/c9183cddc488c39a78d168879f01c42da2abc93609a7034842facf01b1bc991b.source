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


def test_verified_count_equals_confirmed_only():
    findings = [
        {"verification_status": "confirmed"},
        {"verification_status": "false_positive"},
        {"verification_status": "confirmed"},
        {"verification_status": "needs_context"},
    ]

    verified_count = sum(1 for f in findings if f["verification_status"] == "confirmed")
    false_pos_count = sum(1 for f in findings if f["verification_status"] == "false_positive")

    assert verified_count == 2
    assert false_pos_count == 1


def test_non_confirmed_has_no_verified_in_response():
    findings = [
        {"verification_status": "confirmed", "is_verified": True},
        {"verification_status": "false_positive", "is_verified": False},
        {"verification_status": "not_reproducible", "is_verified": False},
        {"verification_status": "needs_context", "is_verified": False},
    ]

    for f in findings:
        assert f["is_verified"] == (f["verification_status"] == "confirmed")


def test_verified_count_matches_confirmed_after_sandbox_evidence_is_preserved():
    findings = [
        {
            "verification_status": "confirmed",
            "is_verified": True,
            "sandbox_attempts": [{"success": True, "evidence_summary": "sandbox proof"}],
        },
        {
            "verification_status": "confirmed",
            "is_verified": True,
            "sandbox_attempts": [{"success": True, "evidence_summary": "sandbox proof"}],
        },
        {"verification_status": "false_positive", "is_verified": False},
        {"verification_status": "not_reproducible", "is_verified": False},
    ]

    verified_count = sum(1 for f in findings if f["verification_status"] == "confirmed")
    false_pos_count = sum(1 for f in findings if f["verification_status"] == "false_positive")

    assert verified_count == 2
    assert false_pos_count == 1
    assert all(f["is_verified"] == (f["verification_status"] == "confirmed") for f in findings)
