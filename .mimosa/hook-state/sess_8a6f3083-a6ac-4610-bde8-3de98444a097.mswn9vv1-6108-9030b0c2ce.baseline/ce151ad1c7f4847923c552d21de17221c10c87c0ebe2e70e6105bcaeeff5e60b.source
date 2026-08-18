"""方案C 宽松兜底 + weak_evidence 路径测试（code review I-4）。

覆盖 _attach_runtime_sandbox_attempts 的宽松兜底分支与 has_weak_evidence 认 weak_evidence 标记的逻辑。
"""
from app.services.agent.agents.verification import VerificationAgent


def _make_agent():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    return agent


def test_weak_evidence_fallback_attaches_when_llm_poC_succeeds_without_marker():
    """LLM 自写 PoC success 但无 VULNERABILITY_CONFIRMED + 有 verification_method → 宽松兜底补入 weak_evidence。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 poc.py Target: app/server.py:80",
            "target_ref": "app/server.py:80",
            "evidence_summary": "path traversal: TOP SECRET DATA 路径遍历成功读取 root 外文件" * 2,
        }
    ]
    finding = {
        "verification_status": "confirmed",
        "file_path": "app/server.py",
        "line_start": 80,
        "vulnerability_type": "path_traversal",
        "title": "LocalFileStore path traversal",
        "verification_method": "sandbox_exec",
        # sandbox_attempts 为空（LLM 没填）
    }
    agent._attach_runtime_sandbox_attempts(finding)
    # 应补入 1 个带 weak_evidence 标记的 attempt
    assert len(finding["sandbox_attempts"]) == 1
    assert finding["sandbox_attempts"][0]["weak_evidence"] is True


def test_weak_evidence_fallback_then_static_confirmed():
    """宽松兜底补入后 → has_weak_evidence=True → 判 static_confirmed（非 confirmed，非 not_reproducible）。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 poc.py Target: app/server.py:80",
            "target_ref": "app/server.py:80",
            "evidence_summary": "IDOR 越权成功读取他人资源 sk-user-B" * 2,
        }
    ]
    finding = {
        "verification_status": "confirmed",
        "file_path": "app/server.py",
        "line_start": 80,
        "vulnerability_type": "idor",
        "title": "IDOR 用户身份隔离缺失",
        "verification_method": "sandbox_exec",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


def test_strict_match_takes_priority_over_weak_fallback():
    """严匹配命中（含 VULNERABILITY_CONFIRMED）→ 不走宽松兜底，直接判 confirmed。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 poc.py Target: app/server.py:80",
            "target_ref": "app/server.py:80",
            "evidence_summary": "VULNERABILITY_CONFIRMED: path traversal verified",
        }
    ]
    finding = {
        "verification_status": "confirmed",
        "file_path": "app/server.py",
        "line_start": 80,
        "vulnerability_type": "path_traversal",
        "title": "path traversal",
        "verification_method": "sandbox_exec",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    # 严匹配补入的 attempt 无 weak_evidence 标记
    assert len(finding["sandbox_attempts"]) == 1
    assert "weak_evidence" not in finding["sandbox_attempts"][0] or finding["sandbox_attempts"][0].get("weak_evidence") is not True
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "confirmed"


def test_weak_fallback_not_overwrite_llm_strict_evidence():
    """LLM 已填含 VULNERABILITY_CONFIRMED 的成功 attempt → 宽松兜底不覆盖。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 runtime.py Target: app/server.py:80",
            "target_ref": "app/server.py:80",
            "evidence_summary": "runtime output without marker",
        }
    ]
    finding = {
        "verification_status": "confirmed",
        "file_path": "app/server.py",
        "line_start": 80,
        "vulnerability_type": "path_traversal",
        "title": "path traversal",
        "verification_method": "sandbox_exec",
        "sandbox_attempts": [
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "VULNERABILITY_CONFIRMED: LLM 填的强证据",
                "target_ref": "app/server.py:80",
            }
        ],
    }
    agent._attach_runtime_sandbox_attempts(finding)
    # LLM 填的不被覆盖，runtime 不补入
    assert len(finding["sandbox_attempts"]) == 1
    assert "VULNERABILITY_CONFIRMED" in finding["sandbox_attempts"][0]["evidence_summary"]


def test_weak_fallback_no_cross_finding_overmatch():
    """I-1: 共享文件名的两个 finding 不应跨漏洞类型沾光。

    finding B 是 sql_injection，runtime attempt 是 path_traversal 的 PoC（同文件 app/server.py），
    宽松兜底应因 vuln_type 不匹配而拒绝补入。
    """
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 poc.py Target: app/server.py:80",
            "target_ref": "app/server.py:80",
            "evidence_summary": "路径遍历成功 path traversal" * 2,
        }
    ]
    finding_b = {
        "verification_status": "confirmed",
        "file_path": "app/server.py",
        "line_start": 80,
        "vulnerability_type": "sql_injection",  # 不同漏洞类型
        "title": "SQL 注入",
        "verification_method": "sandbox_exec",
    }
    agent._attach_runtime_sandbox_attempts(finding_b)
    # 不应补入（vuln_type 不匹配）
    assert not finding_b.get("sandbox_attempts") or len(finding_b["sandbox_attempts"]) == 0


def test_weak_fallback_no_overmatch_unrelated_file():
    """I-1: 不同文件的 finding 不应被无关 attempt 沾光（file_name 路径段边界）。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 poc.py Target: myapp.py:10",
            "target_ref": "myapp.py:10",
            "evidence_summary": "path traversal path traversal" * 2,
        }
    ]
    finding = {
        "verification_status": "confirmed",
        "file_path": "app.py",  # app.py 不应命中 myapp.py
        "line_start": 10,
        "vulnerability_type": "path_traversal",
        "title": "path traversal",
        "verification_method": "sandbox_exec",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    assert not finding.get("sandbox_attempts") or len(finding["sandbox_attempts"]) == 0


def test_weak_fallback_rejects_empty_evidence():
    """I-2: evidence 过短（<20字符，仅 has_output 跑完但无实质）→ 不补入。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "python3 poc.py Target: app/server.py:80",
            "target_ref": "app/server.py:80",
            "evidence_summary": "done",  # 过短
        }
    ]
    finding = {
        "verification_status": "confirmed",
        "file_path": "app/server.py",
        "line_start": 80,
        "vulnerability_type": "path_traversal",
        "title": "path traversal",
        "verification_method": "sandbox_exec",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    assert not finding.get("sandbox_attempts") or len(finding["sandbox_attempts"]) == 0
