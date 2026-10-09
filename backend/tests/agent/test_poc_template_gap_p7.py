"""P7-4 PoC 模板缺口+类型规范化（2026-10-07 工作流实证）。

证据（四任务全量 15 attempts 分类）：
- 33.3%（4/12）落入通用模板：insecure_cookie 用 XXE/反序列化五件套 grep
  （DocumentBuilderFactory/...）必然 NO_SINK——生产实证 B 机 c8686a20；
- PoC 路径与保存路径的类型规范化逻辑重复且不一致；
- 无适配模板时空跑（浪费沙箱预算）应标 no_poc_template。

契约：
- `normalize_vuln_type(raw)`：模块级唯一规范化（保存路径与 PoC 路径共用）；
- cmd_templates 补 insecure_cookie / expression_injection 专用模板；
- 无适配模板且非语言命中 → sandbox_commands 不入队，skip 标 no_poc_template。
"""
import pytest

from app.services.agent.agents.verification import (
    VERIFICATION_TEMPERATURE,
    normalize_vuln_type,
)


def _build(findings):
    """测试辅助：实例化最小 VerificationAgent 调用真实 _build_sandbox_commands。"""
    from app.services.agent.agents.verification import VerificationAgent
    agent = VerificationAgent.__new__(VerificationAgent)
    return VerificationAgent._build_sandbox_commands(agent, findings)


class TestNormalizeVulnTypeP7:
    @pytest.mark.parametrize("raw,expected", [
        ("SQL Injection", "sql_injection"),
        ("XSS", "xss"),
        ("sqli", "sql_injection"),
        ("RCE", "command_injection"),
        ("path traversal", "path_traversal"),
        ("ssrf", "ssrf"),
        ("deserialization", "deserialization"),
        ("unknown_xyz", "other"),
    ])
    def test_alias_normalization(self, raw, expected):
        assert normalize_vuln_type(raw) == expected


class TestCookieTemplateP7:
    def test_insecure_cookie_has_dedicated_template(self):
        cmds = _build([{
            "vulnerability_type": "insecure_cookie",
            "file_path": "HttpServletResponseWrapper.java",
            "line_start": 57, "title": "Cookie Missing Secure",
            "severity": "medium",
        }])
        assert len(cmds) == 1
        command = cmds[0]["command"]
        # 语义匹配：检查 cookie 安全调用，而非 XML/反序列化 sink
        assert "setSecure" in command or "setHttpOnly" in command
        assert "DocumentBuilderFactory" not in command

    def test_expression_injection_has_dedicated_template(self):
        cmds = _build([{
            "vulnerability_type": "expression_injection",
            "file_path": "ELProcessor.java",
            "line_start": 31, "title": "EL eval",
            "severity": "high",
        }])
        assert len(cmds) == 1
        command = cmds[0]["command"]
        assert "ELProcessor" in command or "eval" in command


class TestNoTemplateHandlingP7:
    def test_other_conf_gets_no_xmldoc_grep_template(self):
        # 真实场景：.conf 配置类文件走 other 通用模板会 grep XML sink（无验证价值）。
        # 契约：非源码语言扩展名（.conf/.yaml 配置文件等）不生成 sink-grep 型 PoC
        cmds = _build([{
            "vulnerability_type": "configuration_style",
            "file_path": "server.conf",
            "line_start": 1, "title": "t", "severity": "low",
        }])
        # 不得携带 XML/反序列化 sink grep（对配置文件是 guaranteed NO_SINK 的空跑）
        assert all(
            "DocumentBuilderFactory" not in c.get("command", "")
            for c in cmds
        )
