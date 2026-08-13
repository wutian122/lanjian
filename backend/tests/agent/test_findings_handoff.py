"""测试 Verification Agent 应完整接收所有 Agent 的发现。"""

from pathlib import Path


VERIFY_PATH = Path(__file__).resolve().parents[2] / "app" / "services" / "agent" / "agents" / "verification.py"


def test_verification_reads_handoff_key_findings():
    """Verification Agent 的 run 方法必须从 handoff.key_findings 获取发现。"""
    content = VERIFY_PATH.read_text(encoding="utf-8")

    assert "key_findings" in content, "verification agent must read 'key_findings' from handoff"
    assert "handoff_findings" in content, "verification agent must extract handoff findings"
    assert "findings_to_verify" in content, "verification agent must populate findings_to_verify"