"""P10-T4 口径统一：verified_count 合计语义测试。

旧口径（P9 及以前）：verified_count 仅统计 verification_status=confirmed
且 is_verified=True，static_confirmed（代码推理确认）被排除——生产实证
（B·tomcat 84d97d33）UI「已验证 1」与后端报告 sum(is_verified)=3 出现
口径裂缝。

新口径（2026-10-09 老板批准）：verified_count = verification_status in
(confirmed, static_confirmed) 且 is_verified=True。P9-8 证据前置（软证据
升级须有真实非 infra attempt + 四件套 + confidence≥0.75）已防洗白，
static_confirmed 采信与 confirmed 同口径安全。
"""
from app.models.agent_task import (
    VERIFIED_COUNT_STATUSES,
    finding_counts_as_verified,
)


def test_verified_count_statuses_are_confirmed_and_static_confirmed():
    """口径单一真相：计入 verified_count 的状态集合"""
    assert set(VERIFIED_COUNT_STATUSES) == {"confirmed", "static_confirmed"}


def test_confirmed_with_is_verified_counts():
    assert finding_counts_as_verified(
        {"verification_status": "confirmed", "is_verified": True}
    ) is True


def test_static_confirmed_with_is_verified_counts():
    """新口径核心：static_confirmed + is_verified=True 计入合计"""
    assert finding_counts_as_verified(
        {"verification_status": "static_confirmed", "is_verified": True}
    ) is True


def test_static_confirmed_without_is_verified_not_counted():
    """is_verified 仍是硬前提：static_confirmed 非 is_verified 不计入"""
    assert finding_counts_as_verified(
        {"verification_status": "static_confirmed", "is_verified": False}
    ) is False


def test_other_statuses_not_counted_even_if_is_verified_true():
    """脏数据防御：其他状态即使 is_verified=True 也不得计入"""
    for status in ("not_reproducible", "needs_context", "false_positive"):
        assert finding_counts_as_verified(
            {"verification_status": status, "is_verified": True}
        ) is False


def test_confirmed_without_is_verified_not_counted():
    assert finding_counts_as_verified(
        {"verification_status": "confirmed", "is_verified": False}
    ) is False
