"""Task-scoped LLM rate limiter isolation tests.

对应 spec delta llm-adapter:
- Scenario: 不同任务获得独立的 LLM 限流器
- Scenario: 同一任务复用同一限流器实例
- Scenario: limiter 按 RPM 计算 rate
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.core import rate_limiter as module


def test_get_task_llm_rate_limiter_returns_independent_instances():
    """不同 task_id 取到不同 limiter 实例，互不影响。"""
    module._global_registry = None

    limiter_a = module.get_task_llm_rate_limiter("task-A", rpm=5)
    limiter_b = module.get_task_llm_rate_limiter("task-B", rpm=60)

    assert limiter_a is not limiter_b
    assert limiter_a.name == "llm_task-A"
    assert limiter_b.name == "llm_task-B"


def test_get_task_llm_rate_limiter_same_task_returns_same_instance():
    """同 task_id 取到同一实例，状态延续。"""
    module._global_registry = None

    limiter_first = module.get_task_llm_rate_limiter("task-X", rpm=10)
    limiter_first.tokens = 2.5  # 改一下状态

    limiter_again = module.get_task_llm_rate_limiter("task-X", rpm=10)

    assert limiter_again is limiter_first
    assert limiter_again.tokens == 2.5


def test_get_task_llm_rate_limiter_uses_rpm():
    """rpm=5 → rate≈5/60，rpm=60 → rate=1.0。"""
    module._global_registry = None

    limiter_5 = module.get_task_llm_rate_limiter("task-rpm-5", rpm=5)
    limiter_60 = module.get_task_llm_rate_limiter("task-rpm-60", rpm=60)

    assert abs(limiter_5.rate - 5 / 60.0) < 1e-9
    assert limiter_60.rate == 1.0


def test_get_task_llm_rate_limiter_burst_is_5():
    """burst 固定 5。"""
    module._global_registry = None

    limiter = module.get_task_llm_rate_limiter("task-burst", rpm=5)

    assert limiter.burst == 5


def test_get_task_llm_rate_limiter_defaults_rpm_when_none():
    """rpm=None 或 0 时 fallback 到默认 60。"""
    module._global_registry = None

    limiter_none = module.get_task_llm_rate_limiter("task-none", rpm=None)
    limiter_zero = module.get_task_llm_rate_limiter("task-zero", rpm=0)

    assert limiter_none.rate == 1.0  # 60/60
    assert limiter_zero.rate == 1.0  # 60/60
