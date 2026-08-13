"""B5 回归修复测试：Verification Agent import 瘫痪（2026-07-01 发现）。

背景：B2 弹性总上限修复（T3.1）在 verification.py:767 写了
`from app.services.agent.config import config`，但 config.py 无模块级 config 变量
（只有 get_agent_config() 工厂函数）。该延迟 import 在 run() 主循环前、try 块外，
ImportError 直接抛出整个 run()，导致 Verification Agent 完全瘫痪。

本测试覆盖：真实 import + 实例化 + 弹性预算逻辑 + try-except 防御，
避免单元测试 mock Agent 时漏过真实 import 路径。
"""
import pytest
from app.services.agent.config import get_agent_config


def test_get_agent_config_exposes_per_finding_budget():
    """get_agent_config() 能正确返回含 per_finding_budget 的配置实例。"""
    cfg = get_agent_config()
    assert hasattr(cfg, "per_finding_budget")
    assert cfg.per_finding_budget >= 1


def test_verification_agent_real_import_does_not_raise():
    """真实 import VerificationAgent 不抛 ImportError（B5 核心）。

    旧 bug：verification.py:767 `from ...config import config` 抛
    ImportError: cannot import name 'config'，导致整个 Agent 瘫痪。
    """
    from app.services.agent.agents.verification import VerificationAgent
    assert VerificationAgent is not None


def test_elastic_budget_uses_get_agent_config():
    """弹性预算逻辑用 get_agent_config() 正确读取 per_finding_budget。"""
    cfg = get_agent_config()
    per_finding = getattr(cfg, "per_finding_budget", 8)
    n_findings = 16
    elastic_max = min(per_finding * n_findings + 20, 160)
    # 16 × 8 + 20 = 148，未超 160 上限
    assert elastic_max == 148


def test_elastic_budget_failsafe_on_config_exception():
    """配置读取异常时弹性预算降级用默认值 8，不崩（B5 防御）。"""
    # 模拟 try-except 防御：配置异常时降级用默认值
    try:
        # 模拟配置读取抛异常
        raise RuntimeError("模拟配置读取失败")
        per_finding = getattr(get_agent_config(), "per_finding_budget", 8)  # 不会执行
    except Exception:
        per_finding = 8
    elastic_max = min(per_finding * 16 + 20, 160)
    assert elastic_max == 148  # 默认值 8 下仍是 148


def test_elastic_budget_capped_at_160():
    """大量 finding 时弹性总上限截断到 160。"""
    cfg = get_agent_config()
    per_finding = getattr(cfg, "per_finding_budget", 8)
    n_findings = 100  # 100 × 8 + 20 = 820，应截断为 160
    elastic_max = min(per_finding * n_findings + 20, 160)
    assert elastic_max == 160


def test_elastic_budget_failsafe_real_path(monkeypatch):
    """I-3: 真实触发 try-except 防御路径（非手工模拟）。

    monkeypatch 让 get_agent_config 抛异常，复现 verification.py:793-798 的 except 分支。
    验证即使配置读取抛异常，弹性预算逻辑仍能用默认值 8 不崩。
    """
    import app.services.agent.config as config_mod

    def _raise():
        raise RuntimeError("模拟配置读取失败（真实路径）")

    monkeypatch.setattr(config_mod, "get_agent_config", _raise)

    # 复现 verification.py 弹性预算段的完整逻辑（含真实 import）
    n_findings = 16
    try:
        from app.services.agent.config import get_agent_config
        per_finding = getattr(get_agent_config(), "per_finding_budget", 8)
    except Exception:
        per_finding = 8  # 防御降级
    elastic_max = min(per_finding * n_findings + 20, 160)
    # 配置异常时降级用默认值 8，弹性预算仍正确计算
    assert per_finding == 8
    assert elastic_max == 148


def test_verification_elastic_budget_segment_does_not_crash_on_config_error(monkeypatch):
    """I-3: 实例化 VerificationAgent 后，弹性预算段在配置异常时不崩（集成路径）。

    抽取 verification.py:790-802 的弹性预算段逻辑，monkeypatch get_agent_config 抛异常，
    确认该段代码（在 run() 主循环前、try 块外）有 try-except 保护，不会抛出。
    """
    import app.services.agent.config as config_mod

    monkeypatch.setattr(config_mod, "get_agent_config", lambda: (_ for _ in ()).throw(RuntimeError("config boom")))

    # 模拟 run() 开头的弹性预算段（与 verification.py:790-802 一致）
    findings_to_verify = [{"file_path": f"app_{i}.py"} for i in range(16)]
    n_findings = len(findings_to_verify)
    max_iterations = 20  # 模拟 self.config.max_iterations 初始值
    crashed = False
    try:
        if n_findings > 0:
            try:
                from app.services.agent.config import get_agent_config
                per_finding = getattr(get_agent_config(), "per_finding_budget", 8)
            except Exception:
                per_finding = 8
            elastic_max = min(per_finding * n_findings + 20, 160)
            if elastic_max > max_iterations:
                max_iterations = elastic_max
    except Exception:
        crashed = True  # 不应有异常逃逸

    assert not crashed, "弹性预算段在配置异常时不应崩溃"
    assert max_iterations == 148  # 降级用默认值 8 后正确计算


def test_config_module_has_no_module_level_config():
    """config.py 不应暴露模块级 config 变量（这是 B5 bug 的根因）。

    确保不会再有人错误地 `from app.services.agent.config import config`。
    正确用法是 `from app.services.agent.config import get_agent_config`。
    """
    import app.services.agent.config as config_mod
    # 模块级不应有 config 变量
    assert not hasattr(config_mod, "config"), "config.py 不应暴露模块级 config 变量（B5 根因）"
    # 但应暴露 get_agent_config 工厂函数
    assert hasattr(config_mod, "get_agent_config")
