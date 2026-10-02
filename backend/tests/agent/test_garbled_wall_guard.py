"""乱码墙硬检测（2026-09-29 层 3b）+ 截断乱码轮丢弃（层 3a）。

背景：任务 c6d6cd09 实证——截断轮 parse 后半残 step 携带乱码任务文本
（"寻巬b / max ~ff files / ÿd½ôÿéúÇ"）传给子 Agent，子 Agent 在垃圾输入上
继续截断 → 恶性循环 0 finding。根治：乱码在源头上丢弃，绝不执行、绝不外传。

契约：
- `_has_garbled_wall`（base 纯函数）：
  * 无效替换符（U+FFFD-U+FFFF）任一 → True；
  * 扩展 Latin-1（U+0080-U+00FF，含样本 "ÿd½ôÿéúÇ"）无空格连片 run ≥6 → True；
  * 正常中文审计文本 / 代码 / 法语标注（含零星重音字符）→ False；
- 截断轮乱码决策丢弃：`_is_garbled_truncated_output` 且仅当 (step 非空 and
  本轮 length 截断 and 输出含乱码墙) 为 True——非截断轮乱码不拦截（留给
  degeneracy 检测），截断正常轮不误伤。
"""
import pytest

from app.services.agent.agents.base import BaseAgent


class TestGarbledWallDetection:
    @pytest.mark.parametrize("bad", [
        "ÿd½ôÿéúÇ",
        "n\xffd\xbd\xf4\xff\xe9\xfa\xc7 nginxx-release",
        "normal text � end",
        "A\x80\x81\x82\x83\x84\x85B",
    ])
    def test_garbled_wall_hit(self, bad):
        assert BaseAgent._has_garbled_wall(bad) is True

    @pytest.mark.parametrize("ok", [
        "",
        "好的，路径遍历风险已确认。",
        "## Files to read (max 8 files, stop after)",
        "src/http/modules/ngx_http_proxy_module.c",
        "Vulnérabilité confirmée dans le parseur HTTP",  # 法语零星重音字符
        "if (a == b) { return c; } // ok",
        "多字节中文——分隔线----正常----------",
    ])
    def test_normal_text_passes(self, ok):
        assert BaseAgent._has_garbled_wall(ok) is False


class TestGarbledTruncatedStepRejection:
    def _make(self, **kw):
        from app.services.agent.agents.recon import ReconAgent

        agent = ReconAgent.__new__(ReconAgent)
        agent._last_llm_truncated = kw.get("truncated", True)
        return agent

    def test_truncated_garbled_step_rejected(self):
        agent = self._make(truncated=True)
        assert agent._is_garbled_truncated_output(
            step=object(), llm_output="ÿd½ôÿéúÇ") is True

    def test_truncated_clean_step_not_rejected(self):
        agent = self._make(truncated=True)
        assert agent._is_garbled_truncated_output(
            step=object(), llm_output="Thought: ok") is False

    def test_non_truncated_garbled_not_rejected_here(self):
        # 非截断轮乱码留给 degeneracy 检测管线；本守门只管截断环节
        agent = self._make(truncated=False)
        assert agent._is_garbled_truncated_output(
            step=object(), llm_output="�") is False

    def test_no_step_not_rejected(self):
        agent = self._make(truncated=True)
        assert agent._is_garbled_truncated_output(
            step=None, llm_output="�") is False


class TestPunctuationDriftDetection:
    """F4 标点密度漂移（2026-10-02 部署对照实证）。

    任务 bba4d002：关思考后长正文出现"无标点面条"漂移——内容逻辑正确但
    整段无句读（repetition_penalty 惩罚高频标点 + fp8 魔改权重长输出漂移）。
    F1 字符墙/F2 循环/F3 乱码墙均抓不到。F4：尾 500 字符窗口句读
    （。！？；：，、.!?;:\\n）≤2 判漂移，流中掐断省 token。
    """

    DRIFT = ("semgrep预扫描唯一热点是changes.xml文档文件非代码攻击向量不构成真实风险源可排除误报干扰因素考虑后判定不存在需要verification沙箱验证的真实候选因为needs_verification等于真"
             "的发现数量为零此时继续重复调度同一Agent期望不同结果违反重要原则第五条避免重复且浪费token预算已达四十七万接近合理上限应基于已有充分证据做出终止决策输出结构化结论说明nginx作为成熟开源Web服务器经过多轮独立第三方安全审查其核心请求处理链路针对本次指定五类目标漏洞在当前版本快照下未发现新增或遗漏的可利用缺陷符合预期") * 3

    def test_punctuation_drift_long_run_detected(self):
        assert BaseAgent._has_punctuation_drift("Thought: " + self.DRIFT) is True

    def test_normal_chinese_prose_not_flagged(self):
        normal = ("已完成recon和analysis两个阶段，均返回0个漏洞发现。按照强制审计顺序规则，"
                  "D6 SSRF、path traversal已由Analysis Agent检查过；semgrep预扫描唯一热点是"
                  "changes.xml，非代码攻击向量，不构成真实风险源。考虑后判定：不存在需要"
                  "verification沙箱验证的真实候选。" * 5)
        assert BaseAgent._has_punctuation_drift(normal) is False

    def test_code_block_with_semicolons_not_flagged(self):
        code = "if (a == b) { return c; }\n" * 60
        assert BaseAgent._has_punctuation_drift(code) is False

    def test_short_run_not_flagged(self):
        assert BaseAgent._has_punctuation_drift("短文本无标点") is False
