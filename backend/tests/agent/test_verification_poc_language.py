"""REQ-VP-1: 预生成 PoC 检测 pattern 按目标文件语言分流。

生产实测：Java 反序列化漏洞（ObjectInputStream）被用 Python pickle.load pattern
grep Java 源码，PoC 空转 -> not_reproducible 误判。本测试锁定语言分流行为。
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
    return VerificationAgent(llm_service=MagicMock(), tools={})


def test_java_deserialization_patterns_use_java_sinks():
    """REQ-VP-1: .java 反序列化目标必须识别 ObjectInputStream，不得用 Python pickle。"""
    pats = _language_sink_patterns(
        "deserialization", "java/org/apache/catalina/tribes/io/XByteBuffer.java"
    )
    assert any("ObjectInputStream" in p for p in pats)
    assert all("pickle" not in p for p in pats)


def test_python_deserialization_patterns_keep_python_sinks():
    """REQ-VP-1: .py 反序列化目标保留 Python sink。"""
    pats = _language_sink_patterns("deserialization", "app/main.py")
    assert any("pickle" in p for p in pats)


def test_unknown_extension_falls_back_to_default():
    """REQ-VP-1: 未知扩展名走 default 兜底，非空且不崩。"""
    pats = _language_sink_patterns("deserialization", "src/core/ngx_string.c")
    assert pats


def test_java_ssrf_patterns_include_java_http_sinks():
    """REQ-VP-1: .java SSRF 目标识别 Java HTTP sink。"""
    pats = _language_sink_patterns(
        "ssrf", "ai/src/main/java/com/alibaba/nacos/ai/service/McpExternalDataAdaptor.java"
    )
    assert any(p in ("HttpURLConnection", "OkHttp", "RestTemplate", "HttpClient") for p in pats)


def test_gen_sandbox_command_java_deser_injects_java_patterns():
    """REQ-VP-1: 生成的 PoC 命令对 .java 反序列化目标注入 Java sink，不含 pickle。"""
    agent = _agent()
    cmd = agent._gen_sandbox_command(
        "deserialization",
        "java/org/apache/catalina/tribes/io/XByteBuffer.java",
        731,
        "Tribes deserialization",
        1,
    )
    assert cmd is not None
    assert "ObjectInputStream" in cmd["input"]["command"]
    # import pickle 是模板运行时导入，不算 pattern；校验 pattern 列表不含 python sink
    assert "r'pickle\\.load'" not in cmd["input"]["command"]


def test_gen_sandbox_command_python_deser_keeps_python_patterns():
    """REQ-VP-1: .py 反序列化目标生成的 PoC 保留 pickle pattern。"""
    agent = _agent()
    cmd = agent._gen_sandbox_command(
        "deserialization", "app/main.py", 12, "Python deserialization", 1
    )
    assert cmd is not None
    assert "r'pickle\\.load'" in cmd["input"]["command"]


def test_gen_sandbox_command_ssrf_java_injects_java_http_sinks():
    """REQ-VP-1: .java SSRF 目标生成的 PoC 注入 Java HTTP sink。"""
    agent = _agent()
    cmd = agent._gen_sandbox_command(
        "ssrf",
        "config/src/main/java/com/example/HttpClient.java",
        50,
        "SSRF in import",
        1,
    )
    assert cmd is not None
    assert any(
        s in cmd["input"]["command"]
        for s in ("HttpURLConnection", "OkHttp", "RestTemplate", "HttpClient")
    )
