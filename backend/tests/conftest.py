"""pytest 根 conftest：统一测试进程环境默认值。

仅对进程内环境变量做 setdefault——显式传入的环境变量（CI/本地 shell）优先；
不触碰任何外部网络/代理配置（socks 代理等问题由各测试自行清理）。
"""
import os

# app.core.config.Settings 强制要求 SECRET_KEY（≥32 位）与 POSTGRES_PASSWORD；
# 未显式提供时注入测试默认值，使 `uv run pytest` 无需 env 前缀即可运行。
os.environ.setdefault("SECRET_KEY", "lanjian-baseline-test-secret-key-for-pytest-20260902")
os.environ.setdefault("POSTGRES_PASSWORD", "baseline-test-password-123")
