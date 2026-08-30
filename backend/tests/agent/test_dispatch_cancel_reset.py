"""w1 (T1): BaseAgent 调度取消复位与软停止基础设施。

背景：调度超时清理调用 agent.cancel() 后 _cancelled 单向锁死（cancel 定义处，
base.py ~537），子 Agent 实例跨 dispatch 复用导致一次超时永久废掉该类型 Agent
（生产 2026-08-30 诊断：nacos/tomact 补发调度全部瞬间"任务已取消"）。

本测试规定：
1. reset_dispatch_cancel() 清除调度超时锁存，调度可恢复；
2. 用户取消（外部回调为真）不得被复位洗掉；
3. 软停止（request_soft_stop/consume_soft_stop）与取消语义完全分离。
"""

from unittest.mock import MagicMock

import pytest

from app.services.agent.agents.recon import ReconAgent


@pytest.fixture()
def agent():
    return ReconAgent(
        llm_service=MagicMock(),
        tools={},
        event_emitter=MagicMock(),
    )


def test_reset_dispatch_cancel_clears_timeout_latch(agent):
    """调度超时造成的取消锁存可被复位，同类型 Agent 补发调度可用。"""
    agent.cancel()  # 模拟调度超时清理路径（orchestrator.py:1957-1958）
    assert agent.is_cancelled is True

    agent.reset_dispatch_cancel()
    assert agent.is_cancelled is False


def test_reset_dispatch_cancel_keeps_user_cancel(agent):
    """用户取消（外部回调为真）不得被复位洗掉。"""
    agent.set_cancel_callback(lambda: True)
    assert agent.is_cancelled is True  # 回调路径触发（并锁存）

    agent.reset_dispatch_cancel()
    assert agent.is_cancelled is True, "用户取消生效期间，复位不得清除取消状态"


def test_reset_after_callback_cleared_keeps_user_cancel(agent):
    """区分性用例：用户取消锁存后即使回调被清空，复位仍不得解除取消。

    既有 cancel() 会清空 _cancel_callback，若实现退化为无条件复位
    （仅复判当前回调），本用例将失败——守护 reset 的回调复判守卫。
    """
    agent.set_cancel_callback(lambda: True)
    assert agent.is_cancelled is True  # 用户取消锁存（_user_cancelled）
    agent.set_cancel_callback(None)  # 模拟回调事后失效/被清空

    agent.reset_dispatch_cancel()
    assert agent.is_cancelled is True, "用户取消锁存不得因回调失效 + 复位而解除"


def test_reset_dispatch_cancel_noop_without_latch(agent):
    """无锁存时复位是幂等空操作。"""
    assert agent.is_cancelled is False
    agent.reset_dispatch_cancel()
    assert agent.is_cancelled is False


def test_request_soft_stop_does_not_cancel(agent):
    """软停止与取消语义分离：置位软停止不影响 is_cancelled。"""
    agent.request_soft_stop()
    assert agent.is_soft_stopped is True
    assert agent.is_cancelled is False


def test_consume_soft_stop_one_shot(agent):
    """软停止信号一次性消费：首次消费返回 True，之后返回 False。"""
    agent.request_soft_stop()
    assert agent.consume_soft_stop() is True
    assert agent.is_soft_stopped is False
    assert agent.consume_soft_stop() is False


def test_soft_stop_survives_reset_dispatch_cancel(agent):
    """复位调度取消不得波及软停止状态（两者独立）。"""
    agent.cancel()
    agent.request_soft_stop()

    agent.reset_dispatch_cancel()
    assert agent.is_soft_stopped is True


def test_soft_stop_and_user_cancel_coexist(agent):
    """软停止与用户取消并存时，取消语义优先判定不受软停止干扰。"""
    agent.request_soft_stop()
    agent.set_cancel_callback(lambda: True)

    assert agent.is_cancelled is True
    assert agent.is_soft_stopped is True
