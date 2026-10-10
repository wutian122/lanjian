"""P10 A' 步骤 1（T2）：良性语料误杀防护库。

权威方案：/root/coder/docs/p10-root-cause-plan-2026-10-09.html 第三章步骤 1
+ 第二章「8 类合法输出实测反例」。方案实测（HEAD=939f3b7 真实检测器）：
8 类完全合法的审计/代码输出全部被 ``_detect_output_degeneracy`` 判 True，
一次误杀即丢弃整轮（约 1 万 token），模型稳定产出同类内容会级联误杀。

8 类红方反例（修正前全部命中，修正后必须全部 None）：
  1. 40 连字符 markdown 分隔线            → 修正前 F1
  2. 35 个等号分隔线/标题下划线            → 修正前 F1
  3. 字符串字面量内含 30+ 个 'A'          → 修正前 F1
  4. 代码 40 空格缩进                     → 修正前 F1
  5. 重复 Java import 行（约 208 字符）    → 修正前 F2
  6. 编号枚举公共前缀（约 300 字符）       → 修正前 F2
  7. 合法引用的扩展 Latin-1 字节           → 修正前 F3
  8. 约 902 字符随机 hex（数据转储）       → 修正前 F4

修正原则（方案硬性要求）：精确形态区分，不是放松阈值——
- F1：行级分隔线/标题模式整行白名单；行首纯空白缩进豁免；同行成对引号
  包裹的字面量内容豁免（数据不是字符墙）；
- F2：行内（同一物理行内）重复仍判循环；跨行重复仅在「同列偏移 + 行后缀
  分歧（编号/列表枚举）」或「同列偏移 + 整行相同且为 import/include 样板」
  时豁免；
- F3：扩展 Latin-1 连片仅在被同行成对引号包裹时豁免（源码字节数据）；
  U+FFFD/U+FFFE/U+FFFF 损坏符任何位置都命中（不豁免）；
- F4：尾窗口主体（≥60%）位于成对引号内（hex/base64 等数据转储）时豁免。
"""

import hashlib
from typing import List, Tuple

import pytest

from app.services.agent.agents.base import BaseAgent
from app.services.agent.agents.orchestrator import OrchestratorAgent


def _detect(text: str):
    # BaseAgent 为抽象类（run 未实现），用具体子类 __new__ 绕过构造
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    return agent._detect_output_degeneracy(text)


def _hex_stream(n: int) -> str:
    """确定性伪随机 hex（sha256 链式摘要），无标点、无长 run。"""
    out = ""
    i = 0
    while len(out) < n:
        out += hashlib.sha256(f"p10-benign-seed-{i}".encode()).hexdigest()
        i += 1
    return out[:n]


def _benign_corpus() -> List[Tuple[str, str]]:
    """8 类红方实测反例（修正前命中，修正后全部 None）。"""
    # 1. 40 连字符 markdown 分隔线
    case1 = "## 审计结论\n" + "-" * 40 + "\n说明结束。"
    # 2. 35 个等号（setext 标题下划线 / 分隔线）
    case2 = "标题文本\n" + "=" * 35 + "\n"
    # 3. 字符串字面量内含 32 个 A
    case3 = 'marker = "' + "A" * 32 + '"\n'
    # 4. 代码 40 空格缩进（后接常规代码行把长度撑过 200 窗口仍须豁免）
    case4 = (
        "def handle_request(req):\n"
        + " " * 40
        + "return None\n"
        + "# check auth first\n# then validate input\n"
        + "# finally log result\n# end of function here\n"
    )
    # 5. 重复 Java import 行（样板代码，246 字符 ≥ 任务要求的 ~208）
    import_line = (
        "import org.springframework.web.servlet.config.annotation."
        "WebMvcConfigurerAdapter;"
    )
    case5 = (import_line + "\n") * 3
    # 6. 编号枚举公共前缀（12 行，336 字符 ≈ 任务要求的 ~300）
    case6 = "".join(
        f"Observation block number {i:02d}\n" for i in range(1, 13)
    )
    # 7. 合法引用的扩展 Latin-1 字节（成对引号内的源码字节数据）
    latin = ("\xff\xfe\xfd\xe9\xfa\xc7" * 7)[:40]
    case7 = 'raw = "' + latin + '"\n'
    # 8. 约 902 字符随机 hex（dump 数据字面量）
    case8 = "dump = '" + _hex_stream(893) + "'"
    return [
        ("markdown-40-hyphens", case1),
        ("35-equals-line", case2),
        ("32-A-in-string-literal", case3),
        ("40-space-indentation", case4),
        ("repeated-java-import", case5),
        ("enumeration-common-prefix", case6),
        ("quoted-latin1-bytes", case7),
        ("random-hex-dump", case8),
    ]


BENIGN = _benign_corpus()


class TestBenignCorpusNotFlagged:
    """8 类合法输出：修正后必须全部判 None（修正前全部命中 = RED）。"""

    @pytest.mark.parametrize(
        "text",
        [text for _, text in BENIGN],
        ids=[name for name, _ in BENIGN],
    )
    def test_benign_returns_none(self, text):
        assert _detect(text) is None

    def test_hex_corpus_length_is_902(self):
        # 固定方案实测口径：hex 反例总长 902
        assert len(BENIGN[7][1]) == 902


class TestTrueDegeneracyStillCaught:
    """对照例：F1-F4 真崩坏构造必须全部仍命中（防豁免过宽）。"""

    def test_real_f1_charwall(self):
        text = "正常审计分析文本。" + "A" * 35 + "继续无引号包裹"
        assert _detect(text) == "F1"

    def test_real_f2_inline_loop(self):
        frag = "international law human rights rule of lawdemocracy"[:30]
        text = "分析开始。" + (frag * 4)[:300] + "结尾"
        assert _detect(text) == "F2"

    def test_real_f2_identical_line_loop(self):
        # 整行重复的非样板行（同列偏移、整行相同、不匹配 import 白名单）
        # 仍是循环，必须命中——证明行结构本身不构成豁免。
        line = "this sentence repeats every single line.\n"
        text = line * 20
        assert len(text) >= 120
        assert _detect(text) == "F2"

    def test_real_f3_garbled_wall(self):
        # 与 T1 同形态：长度过 F1 门禁、无引号包裹的扩展 Latin-1 墙
        text = "normal prefix n\xffd\xbd\xf4\xff\xe9\xfa\xc7 nginx release"
        assert _detect(text) == "F3"

    def test_real_f3_replacement_char_always_hits(self):
        # U+FFFD 损坏符：即使被引号「包围」也不豁免（解码失败标记）
        text = 'data = "prefix � suffix end marker here"'
        assert len(text) >= 30
        assert BaseAgent._has_garbled_wall(text) == "F3"
        assert _detect(text) == "F3"

    def test_real_f4_punctuation_drift(self):
        text = "".join(
            f"编号{i:04d}无标点漂移检测样本" for i in range(60)
        )
        assert len(text) >= 800
        assert _detect(text) == "F4"

    def test_28_hyphens_regression_guard(self):
        # 既有行为守卫：28 连字符（run <30）始终不命中
        text = "3. " + "-" * 28 + " 分隔线\n"
        assert _detect(text) is None


class TestF1BoundaryMutation:
    """F1 变异检查：阈值边界与豁免边界。"""

    def test_30_hyphens_standalone_line_exempt(self):
        # 阈值边界：30 连字符整行（修正前 F1，修正后分隔线豁免）
        assert _detect("-" * 30 + "\n") is None

    def test_29_hyphens_below_threshold(self):
        assert _detect("前文。\n" + "-" * 29 + "\n") is None

    def test_30_A_unquoted_mid_prose_hits(self):
        # 豁免边界：30 A 不在引号内、不独占整行 → 仍命中
        text = "结论：" + "A" * 30 + "后续分析文本"
        assert _detect(text) == "F1"

    def test_35_equals_inside_literal_exempt(self):
        text = "sep = '" + "=" * 35 + "'\n"
        assert _detect(text) is None

    def test_charwall_breaks_out_of_literal_hits(self):
        # 只有开引号没有闭引号：崩坏「冲出」字面量，不豁免
        text = 'data = "' + "B" * 40 + "后续崩坏文本" * 5
        assert _detect(text) == "F1"


class TestF3BoundaryMutation:
    def test_six_latin1_unquoted_hits(self):
        # _has_garbled_wall 本身无长度门禁；前缀补足仅为整体调用过 F1 门禁
        text = "前缀文字说明：" + "X" * 14 + "\x80" * 6 + " 后缀文字"
        assert len(text) >= 30
        assert BaseAgent._has_garbled_wall(text) == "F3"
        assert _detect(text) == "F3"

    def test_six_latin1_quoted_exempt(self):
        text = "raw = '" + "\x80" * 6 + "'\n"
        assert BaseAgent._has_garbled_wall(text) is None

    def test_quoted_run_then_unquoted_run_hits(self):
        # 引号内豁免后，引号外再出现乱码 run 仍须命中
        text = (
            "a = '" + "\x80" * 6 + "'\n"
            "b = " + "\xe9" * 6 + "\n"
        )
        assert BaseAgent._has_garbled_wall(text) == "F3"


class TestF4BoundaryMutation:
    @staticmethod
    def _drift_text(length: int) -> str:
        # 内嵌递增编号杜绝 F2，切出指定长度的无句读正文
        base = "".join(
            f"编号{i:04d}无标点漂移检测样本" for i in range(80)
        )
        return base[:length]

    def test_length_799_not_flagged(self):
        text = self._drift_text(799)
        assert len(text) == 799
        assert _detect(text) is None

    def test_length_800_flagged(self):
        text = self._drift_text(800)
        assert len(text) == 800
        assert _detect(text) == "F4"

    def test_short_data_literal_in_prose_not_exempt(self):
        # 窗口主体仍是无句读正文，仅一个短字面量不构成豁免（<60%）
        text = (
            "id='ab12' "
            + "".join(
                f"编号{i:04d}无标点漂移检测样本" for i in range(60)
            )
        )
        assert len(text) >= 800
        assert _detect(text) == "F4"


class TestF1ExemptionLengthCap:
    """P10 终审 Fix-2：F1 豁免长度上界。

    行级分隔线豁免 ≤100 字符（现实 markdown HR / setext 标题上界）；
    行首空白 run（缩进）豁免 ≤120（现实代码缩进上界）；超长同符
    整行恢复判 F1。
    """

    def test_40_hyphens_corpus_still_exempt(self):
        # T2 既有语料不回潮：40 连字符在界内仍豁免
        assert _detect("## 审计结论\n" + "-" * 40 + "\n说明结束。") is None

    def test_35_equals_corpus_still_exempt(self):
        assert _detect("标题文本\n" + "=" * 35 + "\n") is None

    def test_40_spaces_corpus_still_exempt(self):
        case4 = (
            "def handle_request(req):\n"
            + " " * 40
            + "return None\n"
            + "# check auth first\n# then validate input\n"
            + "# finally log result\n# end of function here\n"
        )
        assert _detect(case4) is None

    # ---- 99/101 分隔线边界（端到端） ----
    def test_99_hyphens_line_exempt(self):
        assert len("-" * 99) < 120
        assert _detect("-" * 99) is None

    def test_100_hyphens_line_exempt_boundary(self):
        assert _detect("-" * 100) is None

    def test_101_hyphens_line_hits_f1(self):
        assert _detect("-" * 101) == "F1"

    def test_101_equals_line_hits_f1(self):
        assert _detect("=" * 101) == "F1"

    def test_is_benign_char_run_99_vs_101_direct(self):
        # 挂点级变异：99 字符分隔整行豁免 / 101 恢复判 F1
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        text99 = "\n" + "-" * 99 + "\n"
        assert agent._is_benign_char_run(text99, 1, 100) is True
        text101 = "\n" + "-" * 101 + "\n"
        assert agent._is_benign_char_run(text101, 1, 102) is False

    # ---- 119/121 缩进边界 ----
    def test_119_spaces_indent_exempt(self):
        # len=119 <120：F2 不跑，F1 缩进豁免
        assert _detect(" " * 119) is None

    def test_120_spaces_exact_boundary_f2(self):
        # len=120 精确边界：F2 闸门（len≥120）先于缩进豁免，按同行重复判 F2
        # （复审实测锁定：119→None / 120→F2 / 121→F1）
        assert _detect(" " * 120) == "F2"

    def test_121_spaces_indent_hits_f1(self):
        # F1 扫描先于 F2：run=121 超上界 → F1
        assert _detect(" " * 121) == "F1"

    def test_121_spaces_then_code_hits_f1(self):
        # 超长缩进后接同行代码：仍按 F1（不是合法缩进）
        text = " " * 121 + "x = 1\n"
        assert _detect(text) == "F1"

    def test_is_benign_char_run_119_vs_121_direct(self):
        # 挂点级变异：119 run 豁免 / 121 恢复判 F1
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        assert agent._is_benign_char_run(" " * 119, 0, 119) is True
        assert agent._is_benign_char_run(" " * 121, 0, 121) is False

    def test_unquoted_char_wall_still_hits(self):
        # 真崩坏对照：无包裹 A 墙保持命中
        text = "正常审计分析文本。" + "A" * 35 + "继续无引号包裹"
        assert _detect(text) == "F1"


class TestF2BoilerplateRepetitionCap:
    """P10 终审 Fix-3：import/include 样板配对豁免重复上界。

    该整行在尾窗口（tail 500）出现 ≥8 行时不再豁免，仍判 F2。
    """

    # 17 字符短样板行（匹配 _BOILERPLATE_LINE_RE 的 import 声明），
    # 使 8 行可完整落入 tail500（长 Java import 行物理上不可能在 500
    # 窗口内出现 8 次，短行同样是现实合法形态）。
    BOILERPLATE = "import io; // x"
    PERIOD = len(BOILERPLATE) + 1  # 含换行：16（复审修正：原注释误写 18）

    def test_3_repeated_imports_exempt_direct(self):
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        text3 = (self.BOILERPLATE + "\n") * 3
        assert agent._is_benign_repetition(text3, 0, self.PERIOD) is True

    def test_3_repeated_imports_exempt_e2e(self):
        # 总长补足 ≥120：3 行样板在界内豁免，其余散文唯一不触发 F2
        text = (
            (self.BOILERPLATE + "\n") * 3
            + "".join(
                f"第{i:03d}项配置检查正常无异常说明。\n" for i in range(5)
            )
        )
        assert len(text) >= 120
        assert _detect(text) is None

    def test_10_repeated_imports_hit_f2_direct(self):
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        text10 = (self.BOILERPLATE + "\n") * 10
        assert agent._is_benign_repetition(text10, 0, self.PERIOD) is False

    def test_10_repeated_imports_hit_f2_e2e(self):
        text = (self.BOILERPLATE + "\n") * 10
        assert len(text) >= 120
        assert _detect(text) == "F2"

    def test_7_repeated_imports_boundary_exempt(self):
        # 边界：7 行 <8 仍豁免（直接挂点）
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        text7 = (self.BOILERPLATE + "\n") * 7
        assert agent._is_benign_repetition(text7, 0, self.PERIOD) is True

    def test_8_repeated_imports_exact_boundary_f2(self):
        # 精确边界 8 行：≥8 不豁免（复审手工实测锁定，补用例防回潮）
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        text8 = (self.BOILERPLATE + "\n") * 8
        assert agent._is_benign_repetition(text8, 0, self.PERIOD) is False
        assert len(text8) >= 120
        assert _detect(text8) == "F2"

    def test_real_non_boilerplate_loop_still_hits(self):
        # 对照：整行重复的非样板真循环始终命中
        line = "this sentence repeats every single line.\n"
        assert _detect(line * 20) == "F2"
