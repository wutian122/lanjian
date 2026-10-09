"""B3 输出句读契约（2026-10-02 部署对照实证，任务 bba4d002）。

无标点面条漂移的应用侧防线（与 F4 流中掐断互补）：在系统提示词层
要求句读纪律 + 限制决策正文长度。契约：inject_agent_contract 注入的
提示词包含句读纪律条目。
"""
from app.services.agent.agent_contract import inject_agent_contract


def test_contract_contains_punctuation_discipline():
    prompt = inject_agent_contract("你是审计 Agent", max_iterations=30)
    assert "句读纪律" in prompt
    assert "无标点的长段文字" in prompt
    assert "质量退化" in prompt


def test_contract_not_duplicated():
    once = inject_agent_contract("你是审计 Agent", max_iterations=30)
    twice = inject_agent_contract(once, max_iterations=30)
    assert twice == once
