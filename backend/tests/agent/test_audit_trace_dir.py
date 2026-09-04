"""
Task 13（sandbox-verification-hard-gate / Phase 4）: audit_trace 路径 env 化与目录自动创建验证。

覆盖：
1. settings.AUDIT_TRACE_DIR 控制 AuditTraceManager 默认根目录
2. 环境变量 AUDIT_TRACE_DIR 被 pydantic Settings 读到（env → settings 链路）
3. 显式 base_dir 入参优先生效（向后兼容 orchestrator 调用）
4. 任意深度路径自动 mkdir（parents=True, exist_ok=True）
5. 写点 add_tool_call 落到配置目录（端到端）
"""

from pathlib import Path

import pytest

from app.core.config import Settings, settings
from app.services.agent.audit_trace import AuditTraceManager


def test_settings_field_default_is_relative_audit_traces():
    """字段存在且默认值保持 ./audit_traces（不破坏现有行为）。"""
    fresh = Settings(_env_file=None)
    assert fresh.AUDIT_TRACE_DIR == "./audit_traces"


def test_settings_reads_audit_trace_dir_from_env(tmp_path, monkeypatch):
    """env AUDIT_TRACE_DIR 必须被 pydantic Settings 读到。"""
    target = tmp_path / "env_traces"
    monkeypatch.setenv("AUDIT_TRACE_DIR", str(target))
    fresh = Settings(_env_file=None)
    assert fresh.AUDIT_TRACE_DIR == str(target)


def test_default_base_dir_reads_settings_audit_trace_dir(tmp_path, monkeypatch):
    """不传 base_dir 时，构造路径 = settings.AUDIT_TRACE_DIR/<task_id前8>。"""
    target = tmp_path / "default_traces"
    monkeypatch.setattr(settings, "AUDIT_TRACE_DIR", str(target))

    mgr = AuditTraceManager(task_id="abc123def456", project_name="demo")

    assert mgr.base_dir == Path(str(target))
    assert mgr.task_dir == Path(str(target)) / "abc123de"
    assert mgr.task_dir.exists(), "task_dir 必须自动创建"


def test_explicit_base_dir_overrides_settings(tmp_path, monkeypatch):
    """显式 base_dir 入参优先生效（保持向后兼容：orchestrator 显式传入）。"""
    monkeypatch.setattr(settings, "AUDIT_TRACE_DIR", str(tmp_path / "should_be_ignored"))
    explicit = tmp_path / "explicit_traces"

    mgr = AuditTraceManager(
        task_id="explicit0001",
        project_name="demo",
        base_dir=str(explicit),
    )

    assert mgr.base_dir == Path(str(explicit))
    assert (mgr.base_dir / "explicit").exists()


def test_directory_auto_created_recursively(tmp_path, monkeypatch):
    """任意深度路径都自动创建（parents=True, exist_ok=True）。"""
    deep = tmp_path / "a" / "b" / "c" / "traces"
    monkeypatch.setattr(settings, "AUDIT_TRACE_DIR", str(deep))

    mgr = AuditTraceManager(task_id="auto00001", project_name="demo")

    assert mgr.base_dir == Path(str(deep)), "深路径必须来自 settings.AUDIT_TRACE_DIR"
    assert mgr.task_dir.exists()
    assert mgr.trace_md.exists(), "trace_md 头必须自动写入"
    assert "审计追踪报告" in mgr.trace_md.read_text(encoding="utf-8")


def test_writing_tool_call_creates_file_in_configured_dir(tmp_path, monkeypatch):
    """写点 add_tool_call 落到 settings.AUDIT_TRACE_DIR 指定目录（端到端）。"""
    target = tmp_path / "write_test"
    monkeypatch.setattr(settings, "AUDIT_TRACE_DIR", str(target))

    mgr = AuditTraceManager(task_id="write0001", project_name="demo")
    mgr.add_tool_call(
        tool_name="test_tool",
        input_params={"k": "v"},
        output="ok",
        duration_ms=10,
        success=True,
    )

    md_content = mgr.trace_md.read_text(encoding="utf-8")
    assert "test_tool" in md_content
    # 文件位于 target/<task_id前8>/
    assert mgr.trace_md.parent.parent == Path(str(target))
