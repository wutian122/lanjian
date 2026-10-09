"""AgentTaskStatus 形态契约（2026-10-02 生产回归）。

任务 42af226f（v6.6.0 部署对照）实证：reporting 阶段收口抛
`'str' object has no attribute 'value'` → 任务 failed——根因是新代码误把
AgentTaskStatus 当 Enum 用了 .value。本项目 AgentTaskStatus 是类常量容器
（成员本身即字符串），加此契约测试防未来误用。
"""
from app.models.agent_task import AgentTaskStatus


def test_status_members_are_plain_strings():
    assert AgentTaskStatus.COMPLETED == "completed"
    assert AgentTaskStatus.COMPLETED_WITH_GAPS == "completed_with_gaps"
    assert AgentTaskStatus.FAILED == "failed"
    assert AgentTaskStatus.PENDING == "pending"


def test_status_is_not_enum_no_value_attribute():
    # 类常量容器（非 Enum）：任何 AgentTaskStatus.X.value 都是 AttributeError
    member = AgentTaskStatus.COMPLETED_WITH_GAPS
    assert isinstance(member, str)
    assert not hasattr(member, "value")
