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
