"""P5-3 过载可观测（2026-10-04）。

生产实证（任务 1fe2d9ce）：过载空响应（usage 全 0）止损后 observations
为空——用户无法诊断"为什么没完成"；止损文案"思考流耗尽"与实际形态
（usage=0，根本没生成）不符。

契约：
- usage 全 0 的空响应 → 判定 overload_suspected；
- 止损（record_empty_round 返回 True）时 orchestrator 写 gate observation
  "llm_empty_response_streak"（进 task.observations 落库链路）；
- 止损事件文案按形态如实描述。
"""
import pytest

from app.services.agent.agents.base import BaseAgent


class TestOverloadDetection:
    def test_zero_usage_empty_response_is_overload(self):
        assert BaseAgent._is_overload_suspected(
            usage={"prompt_tokens": 0, "completion_tokens": 0}) is True

    def test_none_usage_is_overload(self):
        assert BaseAgent._is_overload_suspected(usage=None) is True

    def test_real_usage_not_overload(self):
        assert BaseAgent._is_overload_suspected(
            usage={"prompt_tokens": 4694, "completion_tokens": 99}) is False

    def test_zero_usage_with_zero_prompt_is_overload(self):
        # 1fe2d9ce 实证形态：prompt=0/compl=0——调用未达模型
        assert BaseAgent._is_overload_suspected(usage={}) is True
