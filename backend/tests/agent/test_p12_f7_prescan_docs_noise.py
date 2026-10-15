"""P12 Wave 0 · F7 回归夹具（预扫排噪：docs/changes.xml/*.md/*.rst 不进 hot files）。

事件证据（任务 8a3a713f）：10 条预扫命中全部是 docs/xml/nginx/changes.xml
文档示例同一条 generic 规则，去重折叠 1 条后 type=other 被 F1 过滤器过滤——
落库反而错；真缺陷是预扫不排 docs：噪声占据唯一 hot file，模型每批都要花
轮次声明忽略（04:16:34 兜底实际执行但零有效候选）。

Wave 3 契约（方案第二章 Wave 3，裁决⑦）：
- ``_run_semgrep_prescan`` 排除 docs/ 目录、**/*.md、**/*.rst、
  CHANGELOG*/changes.xml 类文档路径；
- **不排除** tests/、examples/（测试代码可能是靶场线索）；
- gate 留痕计数保留（被过滤的噪声数可观测），hot_files/findings 零文档噪声。

T0 阶段 RED：当前预扫不过滤路径，文档示例全部进入 hot_files。
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


def _raw_hit(path, line, rule="generic.security.audit.unknown-rule", severity="WARNING"):
    return {
        "path": path,
        "start": {"line": line},
        "end": {"line": line},
        "check_id": rule,
        "extra": {
            "severity": severity,
            "message": f"{rule} matched example code",
            "lines": "echo example",
        },
    }


def _results_payload():
    hits = []
    # 8a3a713f 形态：docs/xml/nginx/changes.xml 文档示例 10 条（同一 generic 规则）
    for i in range(10):
        hits.append(_raw_hit("docs/xml/nginx/changes.xml", 100 + i))
    # markdown / rst / CHANGELOG 噪声
    hits.append(_raw_hit("CHANGELOG.md", 5))
    hits.append(_raw_hit("docs/help.rst", 12))
    hits.append(_raw_hit("docs/guide/index.md", 30))
    # 真实代码命中必须保留（tests 不排除——裁决⑦：测试代码可能是靶场）
    hits.append(_raw_hit(
        "tests/test_nginx_config.py", 44,
        rule="p.python.sqlinjection", severity="ERROR",
    ))
    hits.append(_raw_hit(
        "src/ngx_http_core_module.c", 1820,
        rule="p.c.sql-injection", severity="ERROR",
    ))
    return json.dumps({"results": hits, "errors": []})


def _make_agent():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._runtime_context = {"project_root": "."}
    agent.config = SimpleNamespace(name="Orchestrator")
    agent._gate_observations = []
    agent._coverage_bypassed = False
    agent._coverage_bypass_info = {}
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    return agent


def _install_fake_subprocess(monkeypatch, payload: str):
    current = {"cmd": None}

    def _fake_proc():
        proc = MagicMock()
        proc.returncode = 0
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        async def _communicate():
            if current["cmd"] and "--version" in current["cmd"]:
                return b"1.77.0\n", b""
            return payload.encode("utf-8"), b""

        proc.communicate = _communicate
        return proc

    async def _create_subprocess_exec(*args, **kwargs):
        current["cmd"] = list(args)
        return _fake_proc()

    monkeypatch.setattr(
        "app.services.agent.agents.orchestrator.asyncio.create_subprocess_exec",
        _create_subprocess_exec,
    )


@pytest.mark.asyncio
async def test_f7_docs_changes_xml_md_rst_excluded_from_hot_files(monkeypatch):
    """docs/changes.xml + CHANGELOG.md + docs/*.rst/*.md → 0 噪声 hot files；
    tests/ 与 src/ 真实命中保留。"""
    agent = _make_agent()
    _install_fake_subprocess(monkeypatch, _results_payload())

    result = await agent._run_semgrep_prescan()

    assert result["scan_success"] is True
    hot_files = set(result["hot_files"])
    assert hot_files == {"tests/test_nginx_config.py", "src/ngx_http_core_module.c"}, (
        f"docs/changes.xml/md/rst 不得进入 hot_files，实际: {sorted(hot_files)}"
    )
    findings_paths = {f.get("file_path") for f in result["findings"]}
    assert not any("docs/" in p or p.endswith((".md", ".rst")) for p in findings_paths), (
        f"文档路径不得进入预扫 findings: {findings_paths}"
    )
    assert findings_paths == hot_files
    assert len(result["findings"]) == 2


@pytest.mark.asyncio
async def test_f7_only_docs_noise_yields_zero_hot_files(monkeypatch):
    """8a3a713f 原形：命中全部来自 docs/changes.xml → hot_files 与 findings 全空。"""
    agent = _make_agent()
    payload = json.dumps({
        "results": [_raw_hit("docs/xml/nginx/changes.xml", 100 + i) for i in range(10)],
        "errors": [],
    })
    _install_fake_subprocess(monkeypatch, payload)

    result = await agent._run_semgrep_prescan()

    assert result["hot_files"] == [], (
        f"纯文档噪声必须 0 hot files（当前 10 条占据唯一 hot file），实际: {result['hot_files']}"
    )
    assert result["findings"] == []
    assert result["scan_success"] is True


@pytest.mark.asyncio
async def test_f7_tests_and_examples_paths_are_kept(monkeypatch):
    """裁决⑦回归：tests/ 与 examples/ 下的命中不排除（靶场线索）。"""
    agent = _make_agent()
    payload = json.dumps({"results": [
        _raw_hit("examples/vuln_demo.py", 8, rule="p.python.eval", severity="WARNING"),
        _raw_hit("tests/fixtures/ssrf_case.py", 20, rule="p.python.requests-ssrf", severity="WARNING"),
    ], "errors": []})
    _install_fake_subprocess(monkeypatch, payload)

    result = await agent._run_semgrep_prescan()

    kept = {f["file_path"] for f in result["findings"]}
    assert kept == {"examples/vuln_demo.py", "tests/fixtures/ssrf_case.py"}
