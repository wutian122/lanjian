"""REQ-VP-3: 理论风险 finding（缺精确 file_path 但 confidence>=0.7 且有 title+description）保留落库。

生产实测：nginx "防御纵深缺失" finding 被 is_strict_finding 整条过滤（日志铁证
"Filtered by is_strict_finding"），0 落库。本测试锁定放宽行为。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.strict_finding import is_strict_finding


def test_theoretical_risk_finding_preserved():
    """REQ-VP-3: 缺 file_path 但 confidence>=0.7 且有 title+description 的理论风险保留。"""
    finding = {
        "vulnerability_type": "path_traversal",
        "file_path": "",
        "line_start": 0,
        "confidence": 0.8,
        "title": "Rewrite规则中用户可控变量可注入路径遍历序列（防御纵深缺失）",
        "description": "nginx rewrite 模块使用用户可控变量构造 URI 时未重新经过 unsafe_uri 检查",
    }
    assert is_strict_finding(finding) is True


def test_theoretical_risk_without_file_path_but_high_confidence():
    """REQ-VP-3: 只有 ai_confidence（无 confidence 字段）也应保留。"""
    finding = {
        "vulnerability_type": "ssrf",
        "file_path": "unknown",
        "line_start": 0,
        "ai_confidence": 0.9,
        "title": "SSRF potential in import endpoint",
        "description": "认证用户可提供任意 URL 发起请求",
    }
    assert is_strict_finding(finding) is True


def test_low_confidence_theoretical_still_filtered():
    """REQ-VP-3: 缺 file_path 且 confidence<0.7 仍过滤（防幻觉）。"""
    finding = {
        "vulnerability_type": "path_traversal",
        "file_path": "",
        "line_start": 0,
        "confidence": 0.6,
        "title": "Maybe something",
        "description": "vague description",
    }
    assert is_strict_finding(finding) is False


def test_missing_title_filtered():
    """REQ-VP-3: 缺 file_path 且无 title 的理论风险仍过滤。"""
    finding = {
        "vulnerability_type": "path_traversal",
        "file_path": "",
        "line_start": 0,
        "confidence": 0.8,
        "title": "",
        "description": "some description",
    }
    assert is_strict_finding(finding) is False


def test_normal_finding_with_path_and_line_still_strict():
    """REQ-VP-3: 有精确 file_path+line_start 的常规 finding 仍通过。"""
    finding = {
        "vulnerability_type": "sql_injection",
        "file_path": "config/ConfigOpsControllerV3.java",
        "line_start": 123,
        "confidence": 0.95,
        "title": "Sql Injection in ConfigOpsControllerV3",
        "description": "UNION SELECT payload bypasses SELECT-only check",
    }
    assert is_strict_finding(finding) is True


def test_missing_vuln_type_filtered():
    """REQ-VP-3: 无 vulnerability_type 仍过滤。"""
    finding = {
        "vulnerability_type": "",
        "file_path": "x.java",
        "line_start": 10,
        "confidence": 0.9,
        "title": "Something",
        "description": "desc",
    }
    assert is_strict_finding(finding) is False
