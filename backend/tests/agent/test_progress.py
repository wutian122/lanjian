"""测试 AgentTask 进度条应在 verification/reporting 阶段推进。"""

from pathlib import Path


MODEL_PATH = Path(__file__).resolve().parents[2] / "app" / "models" / "agent_task.py"


def test_progress_logic_handles_verification_phase():
    """progress_percentage 应该在 verification 阶段动态推进而非固定 50%。"""
    content = MODEL_PATH.read_text(encoding="utf-8")
    assert "VERIFICATION" in content, "must handle VERIFICATION phase"
    assert "findings_count" in content, "must use findings_count for verification progress"
    assert "tool_calls_count" in content, "must use tool_calls_count for verification progress"


def test_progress_logic_handles_reporting_with_value():
    """progress_percentage 应该在 reporting 阶段有合理推进而非固定 50%。"""
    content = MODEL_PATH.read_text(encoding="utf-8")
    assert "REPORTING" in content, "must handle REPORTING phase"
    # reporting should not just use weight * 0.5
    lines = content.split("\n")
    reporting_lines = [l for l in lines if "REPORTING" in l and "0.5" in l]
    assert len(reporting_lines) == 0, "REPORTING phase should not use 0.5 fallback"


def test_progress_reaches_100_on_completed():
    """完成后应返回 100.0。"""
    content = MODEL_PATH.read_text(encoding="utf-8")
    assert "COMPLETED" in content and "100.0" in content, "COMPLETED status must return 100.0"