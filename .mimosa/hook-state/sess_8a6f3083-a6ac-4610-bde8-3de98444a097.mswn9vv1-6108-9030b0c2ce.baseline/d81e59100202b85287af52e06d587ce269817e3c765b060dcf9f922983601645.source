from app.models.agent_task import AgentFinding, VerificationStatus


def test_agent_finding_has_verification_status_column():
    assert hasattr(AgentFinding, "verification_status")


def test_verification_status_values_are_strict_four_state_set():
    assert VerificationStatus.CONFIRMED == "confirmed"
    assert VerificationStatus.STATIC_CONFIRMED == "static_confirmed"
    assert VerificationStatus.FALSE_POSITIVE == "false_positive"
    assert VerificationStatus.NOT_REPRODUCIBLE == "not_reproducible"
    assert VerificationStatus.NEEDS_CONTEXT == "needs_context"
    assert VerificationStatus.ALL == {
        "confirmed",
        "static_confirmed",
        "false_positive",
        "not_reproducible",
        "needs_context",
    }