"""
根因2 修复测试：prompt 含 verdict 判定示例

根因：LLM 对 SSTI {{7*7}}→49 这类已成功复现的仍标 not_reproducible，
      prompt 缺少明确的 confirmed 判定示例。
修复：prompt 增加 SSTI/XSS/命令注入/JWT confirmed 示例。
"""
import pytest
import os

PROMPT_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "app", "services", "agent", "agents", "verification.py"
)


def _read_prompt_source() -> str:
    with open(PROMPT_FILE, encoding="utf-8") as f:
        return f.read()


class TestPromptVerdictExamples:
    """验证 prompt 含 verdict 示例"""

    def test_prompt_verdict_examples(self):
        """根因2: prompt 含 SSTI/XSS/命令注入 confirmed 示例"""
        src = _read_prompt_source()
        assert "{{7*7}}" in src, "prompt 应含 SSTI {{7*7}}→49 confirmed 示例"
        assert "confirmed" in src
        assert "not_reproducible" in src

    def test_prompt_has_explicit_confirmed_examples(self):
        """根因2: prompt 含明确的 confirmed 判定场景"""
        src = _read_prompt_source()
        confirmed_mentions = sum(1 for kw in ["SSTI", "XSS", "命令注入", "JWT"] if kw in src)
        assert confirmed_mentions >= 3, "prompt 应含 SSTI/XSS/命令注入/JWT 至少 3 类 confirmed 示例"
