"""P9 Fix-2: MD 报告漏洞详情段渲染 verification_note（第七章「三格式可见」缺口）。

背景：第七章要求 verification_note 在 MD / JSON / 前端三格式可见。JSON 报告
导出整个 verification_result（含 verification_note）、前端 FindingDetailPanel
已支持，但最常分享的 Markdown 交付物漏洞详情段不渲染该字段——诸如
「verdict=confirmed 无成功沙箱 attempt 未采信」「顺序归并证据请人工复核」
「PoC 崩溃/infra 故障诊断」在 MD 中不可审计。

数据通路（读码确认）：
- AgentFinding.verification_result 为 nullable JSON 列（dict / None），
  verification_note 无独立列，仅存于该 JSON 的 "verification_note" 键；
- 故 MD 取值链 = f.verification_result（isinstance dict 防护）.get("verification_note")。

契约：
- note 非空（strip 后）→ 在漏洞描述块之后输出「**验证说明:** <note>」+ 空行；
- note 为 None / 空串 / 纯空白 / verification_result 缺失 → 不输出该块（无空标题残留）；
- 多 finding 混合各自正确；多行/特殊字符 note 不破坏报告既有结构。
"""
from types import SimpleNamespace

from app.api.v1.endpoints import agent_tasks
from app.api.v1.endpoints.agent_tasks import generate_audit_report
from app.models.agent_task import AgentTask


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items):
        self._scalars = _FakeScalars(items)

    def scalars(self):
        return self._scalars


class _FakeDB:
    """按端点内调用顺序提供 task / project / findings，纯内存无真实 DB。"""

    def __init__(self, task, project, findings):
        self._task = task
        self._project = project
        self._findings = findings

    async def get(self, model, pk):
        return self._task if model is AgentTask else self._project

    async def execute(self, stmt):
        return _FakeResult(self._findings)


def _make_task():
    return SimpleNamespace(
        id="task12345678abcd",
        project_id="proj-1",
        status="completed",
        security_score=70,
        analyzed_files=3,
        total_files=3,
        total_iterations=4,
        tool_calls_count=5,
        tokens_used=1234,
        started_at=None,
        completed_at=None,
        observations=[],
    )


def _make_project():
    return SimpleNamespace(name="演示项目")


def _make_finding(
    title="SQL 注入",
    severity="high",
    verification_result=None,
    description="用户输入未经参数化直接拼接进 SQL 语句。",
    **overrides,
):
    base = dict(
        id="finding-1",
        title=title,
        severity=severity,
        vulnerability_type="sql_injection",
        file_path="app/dao/user.py",
        line_start=42,
        line_end=42,
        ai_confidence=0.9,
        description=description,
        code_snippet=None,
        suggestion="使用参数化查询。",
        fix_code=None,
        is_verified=False,
        has_poc=False,
        poc_code=None,
        poc_description=None,
        poc_steps=None,
        verification_result=verification_result,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _render_markdown(findings, monkeypatch):
    monkeypatch.setattr(agent_tasks, "assert_can_access_project", lambda *a, **k: None)
    db = _FakeDB(_make_task(), _make_project(), findings)
    user = SimpleNamespace(role="admin")
    resp = await generate_audit_report("task12345678abcd", "markdown", db, user)
    return resp.body.decode("utf-8")


_FOOTER = "*本报告由 蓝鉴 - AI 驱动的安全分析系统生成*"


class TestMDVerificationNote:
    async def test_note_rendered_verbatim(self, monkeypatch):
        note = "verdict=confirmed 但无成功沙箱 attempt，验证结论未采信，请人工复核。"
        md = await _render_markdown(
            [_make_finding(verification_result={"verification_note": note})],
            monkeypatch,
        )
        assert "**验证说明:**" in md
        assert note in md

    async def test_missing_or_blank_note_renders_nothing(self, monkeypatch):
        cases = (
            None,
            {},
            {"verification_note": None},
            {"verification_note": ""},
            {"verification_note": "   "},
        )
        for vr in cases:
            md = await _render_markdown(
                [_make_finding(verification_result=vr)], monkeypatch
            )
            assert "验证说明" not in md

    async def test_mixed_findings_each_correct(self, monkeypatch):
        note = "顺序归并证据，请人工复核 finding 与证据的绑定关系。"
        md = await _render_markdown(
            [
                _make_finding(
                    title="有验证说明的漏洞",
                    verification_result={"verification_note": note},
                ),
                _make_finding(title="无验证说明的漏洞", verification_result=None),
            ],
            monkeypatch,
        )
        assert md.count("**验证说明:**") == 1
        assert note in md
        assert "### HIGH-1: 有验证说明的漏洞" in md
        assert "### HIGH-2: 无验证说明的漏洞" in md

    async def test_multiline_and_special_chars_safe(self, monkeypatch):
        note = (
            "PoC 崩溃诊断：\n"
            "- 第 1 行：`raise RuntimeError`\n"
            "- 第 2 行：| 竖线 | # 井号 | *星号强调*"
        )
        md = await _render_markdown(
            [_make_finding(verification_result={"verification_note": note})],
            monkeypatch,
        )
        assert md.count("**验证说明:**") == 1
        for line in note.splitlines():
            assert line in md
        # 结构守卫：finding 标题与 footer 未被多行内容破坏
        assert "### HIGH-1: SQL 注入" in md
        assert md.rstrip().endswith(_FOOTER)

    async def test_existing_report_structure_preserved(self, monkeypatch):
        md = await _render_markdown(
            [_make_finding(verification_result={"verification_note": "NOTE"})],
            monkeypatch,
        )
        assert "# 蓝鉴 安全审计报告" in md
        assert "## 报告信息" in md
        assert "## 执行摘要" in md
        assert "### 漏洞发现概览" in md
        assert "## 高危 (High) 漏洞" in md
        assert "**位置:**" in md
        assert "**漏洞描述:**" in md
        assert "**修复建议:**" in md
        assert md.rstrip().endswith(_FOOTER)
