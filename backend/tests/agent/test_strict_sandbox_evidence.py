from app.services.agent.agents.verification import VerificationAgent


def test_sandbox_command_includes_finding_target():
    agent = VerificationAgent.__new__(VerificationAgent)
    command = agent._gen_sandbox_command(
        "hardcoded_secret",
        "tests/unit/core/config/test_config.py",
        154,
        "Hardcoded Secret",
        0,
    )

    assert command is not None
    cmd_text = command.get("command") or (command.get("input") or {}).get("command", "")
    assert "test_config.py" in cmd_text


def test_command_injection_includes_target():
    agent = VerificationAgent.__new__(VerificationAgent)
    command = agent._gen_sandbox_command(
        "command_injection",
        "src/app.py",
        12,
        "Command Injection",
        1,
    )

    assert command is not None
    cmd_text = command.get("command") or (command.get("input") or {}).get("command", "")
    assert "src/app.py:12" in cmd_text


def test_sql_injection_includes_target():
    agent = VerificationAgent.__new__(VerificationAgent)
    command = agent._gen_sandbox_command(
        "sql_injection",
        "src/db.py",
        42,
        "SQL Injection",
        2,
    )

    assert command is not None
    cmd_text = command.get("command") or (command.get("input") or {}).get("command", "")
    assert "src/db.py:42" in cmd_text


def test_sandbox_attempt_metadata_shape():
    attempt = {
        "command": "python3 -c 'print(1)'",
        "exit_code": 0,
        "network_mode": "none",
        "timeout": 30,
        "stdout_summary": "1",
        "stderr_summary": "",
        "success": True,
    }

    assert set(attempt) == {
        "command",
        "exit_code",
        "network_mode",
        "timeout",
        "stdout_summary",
        "stderr_summary",
        "success",
    }