"""REQ-CM-1~4: 验证过程四缺陷修复——空 file_path 崩溃/铁证匹配/绑定丢失/SSRF 上限。

生产实证：tomact 289d88e3（空 file_path → IsADirectoryError）、nacos SQL（铁证被
same_line 丢弃）、nginx hardcoded_secret（无 _sandbox_finding_id 绑定丢失）、
nginx 7b76b3a8（SSRF sandbox_exec=106 死循环）。
"""

import asyncio
import hashlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import (
    VerificationAgent,
    compute_verification_status,
)


def _agent() -> VerificationAgent:
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}
    agent._merge_attempts_deduped = lambda existing, new: list(existing) + list(new)
    agent._sandbox_exec_calls = 0
    agent._sandbox_exec_attempts = 0
    agent._sandbox_exec_success = 0
    return agent


def _sql_finding() -> dict:
    return {
        "file_path": "config/src/main/java/com/alibaba/nacos/config/server/controller/v3/ConfigOpsControllerV3.java",
        "line_start": 62,
        "vulnerability_type": "sql_injection",
        "title": "ConfigOpsControllerV3.derbyOps 直接执行用户传入的SQL语句",
    }


def _lineless_attempt() -> dict:
    return {
        "success": True,
        "exit_code": 0,
        "target_ref": "config/src/main/java/com/alibaba/nacos/config/server/controller/v3/ConfigOpsControllerV3.java",
        "evidence_summary": "VULNERABILITY_CONFIRMED: derbyOps executes arbitrary SELECT SQL",
        "command": "python3 -c 'print(\"VULNERABILITY_CONFIRMED\")'",
        "fabricated": False,
    }


def test_empty_file_path_returns_none():
    """REQ-CM-1: 空 file_path 不生成崩溃 PoC。"""
    agent = _agent()
    assert agent._gen_sandbox_command("ssrf", "", 0, "SSRF no path", 0) is None
    assert agent._gen_sandbox_command("ssrf", "/", 0, "SSRF slash", 0) is None


def test_lineless_target_ref_with_evidence_matches():
    """REQ-CM-2: 缺 :line 的 target_ref + 铁证应匹配（same_line 放宽）。"""
    agent = _agent()
    assert agent._sandbox_attempt_matches_finding(_lineless_attempt(), _sql_finding()) is True


def test_lineless_confirmed_by_status_engine():
    """REQ-CM-2: 状态引擎应判 confirmed 而非 not_reproducible。"""
    agent = _agent()
    status, verified, _ = compute_verification_status(
        _sql_finding(),
        [_lineless_attempt()],
        attempt_has_vuln_evidence_fn=agent._attempt_has_vuln_evidence,
        attempt_matches_finding_fn=agent._sandbox_attempt_matches_finding,
    )
    assert status == "confirmed"
    assert verified is True


def test_no_id_finding_binds_via_index_backfill():
    """REQ-CM-3: 无 _sandbox_finding_id 的 finding 按 file_path 回填 ID 后命中索引。"""
    agent = _agent()
    agent._all_findings = [
        {
            "file_path": "src/stream/ngx_stream_proxy_module.c",
            "line_start": 651,
            "vulnerability_type": "ssrf",
            "title": "Stream Proxy SSRF",
            "_sandbox_finding_id": "f-9",
        }
    ]
    agent._runtime_attempts_by_finding_id = {
        "f-9": [
            {
                "success": True,
                "exit_code": 0,
                "finding_id": "f-9",
                "target_ref": "src/stream/ngx_stream_proxy_module.c:651",
                "command": "python3 /tmp/poc_0.py",
                "evidence_summary": "沙箱执行结果\n退出码: 0\n标准输出:\nSource loaded",
                "fabricated": False,
            }
        ]
    }
    finding = {
        "file_path": "src/stream/ngx_stream_proxy_module.c",
        "line_start": 651,
        "vulnerability_type": "ssrf",
        "title": "Stream Proxy SSRF",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    assert finding.get("sandbox_attempts"), "回填 ID 后应命中索引"


def test_no_id_finding_without_match_keeps_null():
    """REQ-CM-3: 回填失败且无其它匹配时 sandbox_attempts 保持 null（needs_context 保留）。"""
    agent = _agent()
    agent._all_findings = []
    agent._runtime_attempts_by_finding_id = {}
    agent._sandbox_attempts = []
    finding = {
        "file_path": "src/x/y.c",
        "line_start": 3,
        "vulnerability_type": "ssrf",
        "title": "no match",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    assert not finding.get("sandbox_attempts")

def test_ssrf_capped_registered_after_deterministic_run():
    """REQ-CM-4: ssrf finding 确定性执行后登记 capped 路径。"""
    agent = _agent()
    mgr = MagicMock()
    mgr.execute_with_files = AsyncMock(
        return_value={"exit_code": 1, "stdout": "", "stderr": "", "error": None}
    )
    agent._get_sandbox_manager = MagicMock(return_value=mgr)
    agent.emit_event = AsyncMock()
    agent._parse_finding_index_from_command = MagicMock(return_value=None)
    cmds = [
        {
            "label": "L",
            "finding_id": "f-1",
            "vuln_type": "ssrf",
            "file_path": "src/stream/ngx_stream_proxy_module.c",
            "input": {"command": "python3 /tmp/poc_0.py", "timeout": 30},
        }
    ]
    asyncio.run(agent._run_deterministic_sandbox_commands(cmds, "/tmp/p"))
    caps = getattr(agent, "_network_capped_paths", set())
    assert "src/stream/ngx_stream_proxy_module.c" in caps


def test_ssrf_capped_blocks_repeated_exec():
    """REQ-CM-4: capped 路径的 sandbox_exec 被拦截，其它不受限。"""
    agent = _agent()
    agent._network_capped_paths = {"src/stream/ngx_stream_proxy_module.c"}
    msg = asyncio.run(
        agent._block_network_capped(
            {"command": "curl -v http://target src/stream/ngx_stream_proxy_module.c"}
        )
    )
    assert msg is not None
    msg2 = asyncio.run(agent._block_network_capped({"command": "python3 /tmp/other.py"}))
    assert msg2 is None
    # 空 capped 集合不拦截
    agent2 = _agent()
    assert asyncio.run(agent2._block_network_capped({"command": "python3 x.py"})) is None
