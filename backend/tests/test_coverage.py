import pytest
from app.services.agent.core.coverage import CoverageMatrix, CoverageReport


class TestCoverageMatrix:
    def test_initial_state_all_unknown(self):
        matrix = CoverageMatrix()
        report = matrix.to_report()
        assert report.covered_count == 0
        assert report.uncovered_count == 10
        assert not report.is_sufficient

    def test_mark_covered_from_finding(self):
        matrix = CoverageMatrix()
        matrix.mark_covered("D1_injection", evidence="SQL injection in user.py:42")
        report = matrix.to_report()
        assert report.covered_count == 1
        assert report.matrix["D1_injection"]["status"] == "covered"

    def test_mark_shallow_from_grep(self):
        matrix = CoverageMatrix()
        matrix.mark_shallow("D2_auth", evidence="grep JWT found 3 hits")
        report = matrix.to_report()
        assert report.shallow_count == 1
        assert report.matrix["D2_auth"]["status"] == "shallow"

    def test_sufficient_coverage_requires_6_and_d1_d2_d3(self):
        # 修复后：阈值降为 6，核心三角 D1/D2/D3 必查
        matrix = CoverageMatrix()
        for dim in ["D1_injection", "D2_auth", "D3_authz",
                     "D4_deserialization", "D5_file", "D6_ssrf"]:
            matrix.mark_covered(dim, evidence="test")
        report = matrix.to_report()
        assert report.covered_count == 6
        assert report.is_sufficient

    def test_insufficient_when_d1_missing(self):
        matrix = CoverageMatrix()
        for dim in ["D2_auth", "D3_authz", "D4_deserialization",
                     "D5_file", "D6_ssrf", "D7_crypto", "D8_config", "D9_business_logic"]:
            matrix.mark_covered(dim, evidence="test")
        report = matrix.to_report()
        assert not report.is_sufficient  # D1 missing

    def test_shallow_counts_toward_coverage(self):
        # 修复核心：浅覆盖（grep 搜过）计入 covered_count
        matrix = CoverageMatrix()
        # D1/D2/D3 深度覆盖（满足核心三角）
        for dim in ["D1_injection", "D2_auth", "D3_authz"]:
            matrix.mark_covered(dim, evidence="deep")
        # 另外 3 个维度仅浅覆盖
        for dim in ["D4_deserialization", "D5_file", "D6_ssrf"]:
            matrix.mark_shallow(dim, evidence="grep")
        report = matrix.to_report()
        # covered(3) + shallow(3) = 6 >= 6 且核心三角覆盖 → 达标
        assert report.covered_count == 6
        assert report.is_sufficient

    def test_shallow_alone_insufficient_without_core_triangle(self):
        # 浅覆盖计数但核心三角未覆盖 → 仍不达标
        matrix = CoverageMatrix()
        for dim in ["D4_deserialization", "D5_file", "D6_ssrf",
                     "D7_crypto", "D8_config", "D9_business_logic"]:
            matrix.mark_shallow(dim, evidence="grep")
        report = matrix.to_report()
        assert report.covered_count == 6  # 浅覆盖计入
        assert not report.is_sufficient   # 但缺 D1/D2/D3

    def test_below_threshold_insufficient(self):
        # 覆盖数不足 6 → 不达标（即便核心三角齐全）
        matrix = CoverageMatrix()
        for dim in ["D1_injection", "D2_auth", "D3_authz", "D4_deserialization"]:
            matrix.mark_covered(dim, evidence="deep")
        report = matrix.to_report()
        assert report.covered_count == 4
        assert not report.is_sufficient

    def test_map_finding_to_dimension(self):
        matrix = CoverageMatrix()
        assert matrix.map_finding_to_dimension("sql_injection") == "D1_injection"
        assert matrix.map_finding_to_dimension("auth_bypass") == "D2_auth"
        assert matrix.map_finding_to_dimension("idor") == "D3_authz"
        assert matrix.map_finding_to_dimension("xss") == "D1_injection"
        assert matrix.map_finding_to_dimension("ssrf") == "D6_ssrf"
        assert matrix.map_finding_to_dimension("hardcoded_secret") == "D7_crypto"
        assert matrix.map_finding_to_dimension("unknown_type") is None

    def test_map_pattern_to_dimension(self):
        matrix = CoverageMatrix()
        assert matrix.map_pattern_to_dimension("JWT") == "D2_auth"
        assert matrix.map_pattern_to_dimension("execute") == "D1_injection"
        assert matrix.map_pattern_to_dimension("upload") == "D5_file"
        assert matrix.map_pattern_to_dimension("requests.get") == "D6_ssrf"
        assert matrix.map_pattern_to_dimension("unknown_pattern") is None

    def test_map_pattern_to_dimension_no_false_positive(self):
        """模糊匹配不应产生误匹配"""
        matrix = CoverageMatrix()
        assert matrix.map_pattern_to_dimension("keyboard_handler") != "D7_crypto"
        assert matrix.map_pattern_to_dimension("event_handler") != "D8_config"
        assert matrix.map_pattern_to_dimension("hashtable") != "D7_crypto"

    def test_map_pattern_to_dimension_open_paren_still_works(self):
        """open( 等以非字母结尾的关键词仍应匹配"""
        matrix = CoverageMatrix()
        assert matrix.map_pattern_to_dimension("open(file_path)") == "D5_file"
        assert matrix.map_pattern_to_dimension("read(fd)") == "D5_file"
        assert matrix.map_pattern_to_dimension("write(data)") == "D5_file"

    def test_map_finding_to_dimension_csrf(self):
        """csrf 应映射到 D3_authz"""
        matrix = CoverageMatrix()
        assert matrix.map_finding_to_dimension("csrf") == "D3_authz"

    def test_map_finding_to_dimension_open_redirect(self):
        """open_redirect 应映射到 D8_config"""
        matrix = CoverageMatrix()
        assert matrix.map_finding_to_dimension("open_redirect") == "D8_config"

    def test_map_finding_to_dimension_other_falls_back(self):
        """other 类型应回退到 D9_business_logic 而非 None"""
        matrix = CoverageMatrix()
        result = matrix.map_finding_to_dimension("other")
        assert result is not None
