"""REQ-VC-1~4: 验证模板覆盖其它/XXE/命令注入 + poc_error 识别 IndentationError。

生产实证：v6.2.0 回归任务 31889212 四 finding 全 not_reproducible——command_injection
模板 IndentationError 崩溃、other/xxe 走 default 空转模板、poc_error 漏 IndentationError。
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import (
    VerificationAgent,
    _language_sink_patterns,
)


def _agent() -> VerificationAgent:
    agent = VerificationAgent(llm_service=MagicMock(), tools={})
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}
    return agent


def _extract_poc_source(command: str) -> str:
    """从 'cat > /tmp/poc_N.py << 'POC_EOF' ... POC_EOF' 提取 Python 源码。"""
    start = command.find("POC_EOF'")
    end = command.rfind("POC_EOF")
    if start == -1 or end == -1 or end <= start:
        return ""
    return command[start + len("POC_EOF'"):end]


def test_command_injection_template_compiles():
    """REQ-VC-1: command_injection 模板生成的 PoC 无 IndentationError，可编译。"""
    agent = _agent()
    cmd = agent._gen_sandbox_command("command_injection", "app/main.py", 10, "Cmd injection", 0)
    assert cmd is not None
    src = _extract_poc_source(cmd["input"]["command"])
    assert src, "应能提取 PoC Python 源码"
    compile(src, "<poc>", "exec")  # IndentationError 会在此抛出


def test_xxe_java_patterns_include_xml_sinks():
    """REQ-VC-2: xxe .java 目标识别 DocumentBuilderFactory 等 XML sink。"""
    pats = _language_sink_patterns(
        "xxe", "java/org/apache/catalina/servlets/WebdavServlet.java"
    )
    assert any(
        p in ("DocumentBuilderFactory", "SAXParserFactory", "XMLReader", "SAXParser")
        for p in pats
    )


def test_other_type_has_sink_detection():
    """REQ-VC-2: other 类型 PoC 含检测循环（非纯 cat 空转）。"""
    agent = _agent()
    cmd = agent._gen_sandbox_command(
        "other",
        "java/org/apache/catalina/servlets/WebdavServlet.java",
        572,
        "WebDAV XXE",
        0,
    )
    assert cmd is not None
    src = _extract_poc_source(cmd["input"]["command"])
    assert "for pat in" in src, "other 类型 PoC 应含 sink 检测循环"


def test_default_template_has_verdict():
    """REQ-VC-3: default fallback 模板含判定输出（STATIC_CONFIRMED/NO_SINK/FALSE_POSITIVE）。"""
    agent = _agent()
    cmd = agent._gen_sandbox_command("unknown_type_xyz", "app/x.java", 5, "Some issue", 0)
    assert cmd is not None
    src = _extract_poc_source(cmd["input"]["command"])
    assert any(
        v in src for v in ("STATIC_CONFIRMED", "NO_SINK", "FALSE_POSITIVE")
    ), "default 模板应输出判定"


def test_poc_error_recognizes_indentation_error():
    """REQ-VC-4: IndentationError 崩溃标记 poc_error=True。"""
    agent = _agent()
    obs = (
        "沙箱执行结果\n退出码: 1\n标准错误:\n```\n"
        '  File "/tmp/poc_3.py", line 11\n'
        "    sink_found = False\n"
        "    ^\n"
        "IndentationError: expected an indented block\n```"
    )
    attempt = agent._record_sandbox_attempt({"command": "python3 /tmp/poc_3.py"}, obs)
    assert attempt is not None
    assert attempt["poc_error"] is True
