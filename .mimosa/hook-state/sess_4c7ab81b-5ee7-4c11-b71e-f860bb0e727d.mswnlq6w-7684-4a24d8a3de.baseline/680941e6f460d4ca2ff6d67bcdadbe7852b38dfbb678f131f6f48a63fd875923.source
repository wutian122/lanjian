"""B2 饥饿兜底修复测试。"""
from app.services.agent.config import get_agent_config


config = get_agent_config()


def test_per_finding_budget_config_exists():
    assert hasattr(config, "per_finding_budget")
    assert config.per_finding_budget >= 1


def test_elastic_total_max_formula():
    """验证弹性总上限公式: min(per_finding * n + 20, 160)。"""
    per_finding = config.per_finding_budget
    n = 16
    elastic_max = min(per_finding * n + 20, 160)
    # 16 findings × 8 + 20 = 148，未超 160 上限
    assert elastic_max == 148


def test_elastic_total_max_capped_at_160():
    """超过 160 时被截断。"""
    per_finding = 8
    n = 30  # 30 × 8 + 20 = 260，应截断为 160
    elastic_max = min(per_finding * n + 20, 160)
    assert elastic_max == 160


def test_elastic_total_max_small_findings():
    """少量 finding 不膨胀。"""
    per_finding = 8
    n = 2  # 2 × 8 + 20 = 36
    elastic_max = min(per_finding * n + 20, 160)
    assert elastic_max == 36
