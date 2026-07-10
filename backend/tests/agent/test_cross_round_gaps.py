"""
B3 修复测试：CrossRoundContext 构建不得抛出 'list' object is not callable

根因：orchestrator.py:1300 `coverage_report.gaps()` 误调用 @property
"""
import pytest

from app.services.agent.coverage import evaluate_coverage, CoverageReport


class TestCoverageReportGapsProperty:
    """验证 CoverageReport.gaps 是 @property，属性访问不抛异常"""

    def test_coverage_report_gaps_is_property(self):
        """gaps 应为属性访问，返回 list，不抛 'list' object is not callable"""
        report = evaluate_coverage([], [])
        # 属性访问必须成功
        gaps = report.gaps
        assert isinstance(gaps, list)
        # 无 findings 时所有维度未覆盖，gaps 非空
        assert len(gaps) > 0

    def test_coverage_report_gaps_callable_raises(self):
        """以方法形式调用 gaps() 必须抛 TypeError（回归保护：提醒调用方用属性）"""
        report = evaluate_coverage([], [])
        with pytest.raises(TypeError):
            report.gaps()

    def test_cross_round_context_builds_from_coverage_without_error(self):
        """模拟 Orchestrator 构建 CrossRoundContext 的关键路径，不得抛异常"""
        from app.services.agent.core.cross_round import CrossRoundContext

        report = evaluate_coverage([], [])
        cross_round = CrossRoundContext()
        # 这正是 orchestrator.py:1300 的模式 —— 必须用属性访问
        for gap in report.gaps:
            cross_round.gaps.append(gap)
        prompt = cross_round.to_prompt()
        assert "未覆盖维度" in prompt

    def test_orchestrator_source_uses_gaps_as_property(self):
        """回归保护：orchestrator.py 中 CoverageReport.gaps 必须属性访问，不得 gaps()"""
        import inspect
        from app.services.agent.agents import orchestrator as orch_module

        source = inspect.getsource(orch_module)
        # CrossRoundContext 构建路径使用 coverage_report.gaps（属性）
        # 不得出现 coverage_report.gaps()（方法调用）
        assert "coverage_report.gaps()" not in source, (
            "orchestrator.py 不得使用 coverage_report.gaps() —— gaps 是 @property"
        )

    def test_cross_round_covered_filled_for_covered_dimension(self):
        """B3b 回归保护：COVERED 维度必须填充到 cross_round.covered

        根因: status_info 是 CoverageStatus 枚举（str, Enum），原 isinstance(status_info, dict)
        恒 False，导致 covered 永不填充。修复后应比较 CoverageStatus.COVERED。
        """
        from app.services.agent.core.cross_round import CrossRoundContext
        from app.services.agent.coverage import CoverageStatus, evaluate_coverage

        # 构造一个 D1 注入维度的 finding，使 D1 标记为 COVERED
        findings = [{"vulnerability_type": "sql_injection", "title": "SQL注入"}]
        report = evaluate_coverage(findings, [])
        cross_round = CrossRoundContext()
        # 模拟 orchestrator.py:1295-1303 的构建逻辑
        for dim, status_info in report.statuses.items():
            if status_info == CoverageStatus.COVERED:
                cross_round.covered[dim] = "✅ 已覆盖"
            elif status_info == CoverageStatus.SHALLOW:
                cross_round.covered[dim] = "⚠️ 浅覆盖"
        for gap in report.gaps:
            cross_round.gaps.append(gap)

        # D1 必须被标记为已覆盖
        assert "D1" in cross_round.covered, (
            f"D1 应被标记已覆盖，实际 covered={cross_round.covered}"
        )
        prompt = cross_round.to_prompt()
        assert "已覆盖维度" in prompt
        assert "D1" in prompt

    def test_orchestrator_source_compares_coverage_status_enum(self):
        """B3b 源码回归保护：覆盖状态判断必须用 CoverageStatus 枚举比较，不得用 isinstance(dict)"""
        import inspect
        import re as _re
        from app.services.agent.agents import orchestrator as orch_module

        source = inspect.getsource(orch_module)
        # 去除注释行后再检查（注释中引用旧代码是允许的）
        code_lines = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)
        # 代码中不得用 isinstance(status_info, dict) 判断覆盖状态
        assert "isinstance(status_info, dict)" not in code_only, (
            "orchestrator.py 代码不得用 isinstance(status_info, dict) 判断覆盖状态 —— "
            "status_info 是 CoverageStatus 枚举，应直接比较 CoverageStatus.COVERED"
        )
        # 代码中必须用 CoverageStatus.COVERED 比较
        assert "CoverageStatus.COVERED" in code_only, (
            "orchestrator.py 必须用 CoverageStatus.COVERED 比较覆盖状态"
        )
