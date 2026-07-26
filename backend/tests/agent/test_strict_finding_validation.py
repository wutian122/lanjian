from app.services.agent.agents.verification import VerificationAgent


def test_is_valid_finding_accepts_real_vulnerability():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {
        "file_path": "openhands/app_server/file_store/local.py",
        "line_start": 21,
        "vulnerability_type": "path_traversal",
        "title": "LocalFileStore 路径遍历漏洞",
        "description": "get_full_path 方法未验证路径，可构造 ../ 绕过",
    }
    assert agent._is_valid_finding(finding) is True


def test_is_valid_finding_rejects_missing_file_path():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {"line_start": 1, "vulnerability_type": "ssrf"}
    assert agent._is_valid_finding(finding) is False


def test_is_valid_finding_rejects_empty_vulnerability_type():
    agent = VerificationAgent.__new__(VerificationAgent)
    finding = {"file_path": "x.py", "line_start": 1, "vulnerability_type": ""}
    assert agent._is_valid_finding(finding) is False


def test_is_valid_finding_rejects_descriptive_not_finding():
    agent = VerificationAgent.__new__(VerificationAgent)
    descriptives = [
        {"file_path": "x.py", "line_start": 1, "vulnerability_type": "other",
         "title": "FastAPI 路由结构清晰", "description": "路由定义规范"},
        {"file_path": "x.py", "line_start": 1, "vulnerability_type": "other",
         "title": "用户认证依赖中间件", "description": "依赖 auth 中间件"},
        {"file_path": "x.py", "line_start": 1, "vulnerability_type": "other",
         "title": "openhands/server/app.py - FastAPI 应用定义，包含所有路由和中间件", "description": ""},
    ]
    for f in descriptives:
        assert agent._is_valid_finding(f) is False, f"Should reject: {f['title'][:50]}"


def test_backfill_fallback_distributes_unique_file_paths():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._backfill_used_indices = set()
    originals = [
        {"file_path": "a.py", "line_start": 1, "vulnerability_type": "xss", "title": "XSS in a", "severity": "high"},
        {"file_path": "b.py", "line_start": 10, "vulnerability_type": "sqli", "title": "SQLi in b", "severity": "medium"},
        {"file_path": "c.py", "line_start": 20, "vulnerability_type": "rce", "title": "RCE in c", "severity": "critical"},
    ]

    f1 = {"file_path": "", "line_start": 0, "vulnerability_type": "unknown"}
    f2 = {"file_path": "", "line_start": 0, "vulnerability_type": "unknown"}
    f3 = {"file_path": "", "line_start": 0, "vulnerability_type": "unknown"}

    agent._backfill_original_metadata(f1, originals)
    agent._backfill_original_metadata(f2, originals)
    agent._backfill_original_metadata(f3, originals)

    assert f1["file_path"] == "a.py"
    assert f2["file_path"] == "b.py"
    assert f3["file_path"] == "c.py"
    assert len(agent._backfill_used_indices) == 3


def test_backfill_matches_by_title_similarity_not_order():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._backfill_used_indices = set()
    originals = [
        {"file_path": "a.py", "line_start": 1, "vulnerability_type": "xss", "title": "XSS in login form", "severity": "high"},
        {"file_path": "b.py", "line_start": 10, "vulnerability_type": "sqli", "title": "SQL injection in search", "severity": "medium"},
    ]

    f1 = {"file_path": "", "line_start": 0, "vulnerability_type": "unknown", "title": "SQL injection in search query"}
    f2 = {"file_path": "", "line_start": 0, "vulnerability_type": "unknown", "title": "XSS in login form input"}

    agent._backfill_original_metadata(f1, originals)
    agent._backfill_original_metadata(f2, originals)

    assert f1["file_path"] == "b.py", f"Expected b.py for SQLi, got {f1['file_path']}"
    assert f2["file_path"] == "a.py", f"Expected a.py for XSS, got {f2['file_path']}"
