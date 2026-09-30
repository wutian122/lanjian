"""未验证静态线索报告段（2026-09-29 层 5c）。

背景：Semgrep 兜底候选被过滤（无确定性 PoC 模板 / 低严重度 / EL-SSTI 类）后
现在只记一行统计（"类型分布: other×1"），报告读者看不到任何具体线索——
nginx 双机任务的唯一静态信号就这样消失了。修复：明细随 observations 持久化，
报告渲染为"未验证静态线索（报告仅呈现，未执行验证）"段落。

契约：
- `_build_unverified_static_leads_section`：遍历 observations 里
  semgrep_fallback_filtered / semgrep_fallback_unverifiable 两条 gate 的
  candidates 明细，渲染每条的 title + file:line + 类型；无明细返回 []；
- 段落标题注明"报告仅呈现，未执行验证"（与其他兜底候选区分，防止误当已验证）。
"""
import pytest

from app.api.v1.endpoints.agent_tasks import _build_unverified_static_leads_section


OBS = [
    {
        "gate": "semgrep_fallback_filtered",
        "reason": "1 条 ...（类型分布: other×1）",
        "time": "2026-09-27T13:02:16Z",
        "candidates": [
            {"file_path": "docs/xml/nginx/changes.xml", "line": 42,
             "title": "embedded-url-ssrf", "type": "other"},
        ],
    },
]


class TestUnverifiedStaticLeadsSection:
    def test_renders_candidates_with_location(self):
        lines = _build_unverified_static_leads_section(OBS)
        text = "\n".join(lines)
        assert "未验证静态线索" in text
        assert "embedded-url-ssrf" in text
        assert "docs/xml/nginx/changes.xml:42" in text
        assert "报告仅呈现" in text

    def test_empty_without_candidates(self):
        assert _build_unverified_static_leads_section([]) == []
        assert _build_unverified_static_leads_section(
            [{"gate": "other", "reason": "x"}]
        ) == []

    def test_unverifiable_gate_also_rendered(self):
        obs = [
            {"gate": "semgrep_fallback_unverifiable", "reason": "el×1",
             "candidates": [{"file_path": "a.jsp", "line": 5,
                             "title": "EL 表达式注入", "type": "el"}]},
        ]
        lines = _build_unverified_static_leads_section(obs)
        assert "a.jsp:5" in "\n".join(lines)
