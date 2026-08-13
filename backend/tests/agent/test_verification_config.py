"""Verification Agent 配置测试。"""

from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[2] / "app" / "services" / "agent" / "config.py"


def test_verification_agent_uses_registered_sandbox_exec_tool() -> None:
    """verification agent 配置必须与真实注册工具名保持一致。"""
    content = CONFIG_PATH.read_text(encoding="utf-8")

    assert 'tools=["verify_vulnerability", "dataflow_analysis", "sandbox_exec"]' in content
    assert "sandbox_execute" not in content
    assert "validate_vulnerability" not in content
