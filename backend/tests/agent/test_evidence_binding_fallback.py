"""REQ-VP-4: 运行时沙箱证据三级兜底绑定。

生产实测：tomcat Tribes 反序列化 finding 的 sandbox_attempts=null（line 不匹配致
绑定失败）-> needs_context。本测试锁定第三级路径后缀 + vuln_type 组合兜底。
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import VerificationAgent


def _agent() -> VerificationAgent:
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}
    agent._merge_attempts_deduped = lambda existing, new: list(existing) + list(new)
    return agent


def test_path_suffix_fallback_binds_when_line_mismatch():
    """REQ-VP-4: line 不匹配（position fallback 失败）时，路径末 2 段 + vuln_type 组合兜底命中。"""
    agent = _agent()
    agent._runtime_attempts_by_finding_id = {
        "f-1": [
            {
                "success": True,
                "exit_code": 0,
                "finding_id": "f-1",
                "target_ref": "java/org/apache/catalina/tribes/io/XByteBuffer.java:731",
                "command": (
                    "cat > /tmp/poc_1.py << 'POC_EOF'\n"
                    "import os, re, sys\n"
                    "print('=== SANDBOX Deserialization Verification ===')"
                ),
                "evidence_summary": "Source loaded",
            }
        ]
    }
    finding = {
        "file_path": "java/org/apache/catalina/tribes/io/XByteBuffer.java",
        "line_start": 999,  # 与 attempt 的 731 不匹配 -> position fallback 失败
        "vulnerability_type": "deserialization",
        "title": "Tribes deserialization",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    assert finding.get("sandbox_attempts"), "REQ-VP-4: 路径后缀+type 兜底应绑定"


def test_id_match_preferred_over_fallback():
    """REQ-VP-4: finding_id 精确命中优先，不使用第三级兜底。"""
    agent = _agent()
    agent._runtime_attempts_by_finding_id = {
        "abc": [
            {
                "success": True,
                "exit_code": 0,
                "finding_id": "abc",
                "target_ref": "a/b.java:1",
                "command": "python3 x.py",
                "evidence_summary": "",
            }
        ]
    }
    finding = {
        "_sandbox_finding_id": "abc",
        "file_path": "totally/different/path.java",
        "line_start": 5,
        "vulnerability_type": "ssrf",
        "title": "x",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    assert finding.get("sandbox_attempts")
    assert finding["sandbox_attempts"][0]["finding_id"] == "abc"


def test_all_levels_miss_keeps_null():
    """REQ-VP-4: 三级全失配 sandbox_attempts 保持 null（needs_context 语义保留）。"""
    agent = _agent()
    agent._runtime_attempts_by_finding_id = {
        "f-1": [
            {
                "success": True,
                "exit_code": 0,
                "finding_id": "f-1",
                "target_ref": "other/Module.java:10",
                "command": "python3 x.py",
                "evidence_summary": "",
            }
        ]
    }
    finding = {
        "file_path": "completely/unrelated/path.java",
        "line_start": 3,
        "vulnerability_type": "xss",
        "title": "y",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    assert not finding.get("sandbox_attempts")
