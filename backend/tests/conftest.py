"""pytest 根 conftest：统一测试进程环境默认值。

仅对进程内环境变量做 setdefault——显式传入的环境变量（CI/本地 shell）优先；
不触碰任何外部网络/代理配置（socks 代理等问题由各测试自行清理）。
"""
import os

# app.core.config.Settings 强制要求 SECRET_KEY（≥32 位）与 POSTGRES_PASSWORD；
# 未显式提供时注入测试默认值，使 `uv run pytest` 无需 env 前缀即可运行。
os.environ.setdefault("SECRET_KEY", "lanjian-baseline-test-secret-key-for-pytest-20260902")
os.environ.setdefault("POSTGRES_PASSWORD", "baseline-test-password-123")


import pytest


@pytest.fixture(autouse=True)
def _reset_process_wide_llm_state():
    """阻断级防护（方案第七章）：重置进程级单例，杜绝跨文件状态污染。

    - endpoint_health：模块级单例若不重置，前序测试累积 degraded 会让
      后续用例真实 sleep 5s×N 并产生顺序依赖失败；
    - llm_global_gate：既有 reset_gate 一并重建（配置变更口径）；
    - circuit/rate：无同步模块级 reset（circuit.reset_all 为异步），
      按「若已有 reset 则调用」原则不强行处理。
    """
    from app.core.llm_endpoint_health import reset_endpoint_health

    reset_endpoint_health()
    try:
        from app.core.llm_global_gate import reset_gate

        reset_gate()
    except Exception:
        pass
    yield
