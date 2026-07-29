"""
问题二 & 问题一 修复测试：
- mask_config：密钥永不下发前端（F12 不可见），仅返回 {field}Set 布尔
- strip_empty_sensitive：空敏感字段不覆盖已存密钥
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _set_secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    import importlib
    import app.core.config as cfg_mod
    importlib.reload(cfg_mod)


def _import_config():
    import importlib
    import app.api.v1.endpoints.config as cfg
    importlib.reload(cfg)
    return cfg


# ============ 问题二：mask_config ============

def test_mask_config_hides_plaintext_key():
    cfg = _import_config()
    src = {"llmApiKey": "sk-secret-123456", "llmModel": "gpt-4o"}
    masked = cfg.mask_config(src, cfg.SENSITIVE_LLM_FIELDS)
    # 明文密钥不得出现在返回中
    assert masked["llmApiKey"] == ""
    assert "sk-secret-123456" not in str(masked)
    # 但要标记"已配置"
    assert masked["llmApiKeySet"] is True
    # 非敏感字段保留
    assert masked["llmModel"] == "gpt-4o"


def test_mask_config_marks_empty_key_as_not_set():
    cfg = _import_config()
    src = {"llmApiKey": "", "llmModel": "gpt-4o"}
    masked = cfg.mask_config(src, cfg.SENSITIVE_LLM_FIELDS)
    assert masked["llmApiKey"] == ""
    assert masked["llmApiKeySet"] is False


def test_mask_config_all_provider_keys():
    cfg = _import_config()
    src = {f: "secret" for f in cfg.SENSITIVE_LLM_FIELDS}
    masked = cfg.mask_config(src, cfg.SENSITIVE_LLM_FIELDS)
    for f in cfg.SENSITIVE_LLM_FIELDS:
        assert masked[f] == ""
        assert masked[f"{f}Set"] is True


# ============ 问题二：strip_empty_sensitive ============

def test_strip_empty_sensitive_removes_empty_key():
    cfg = _import_config()
    data = {"llmApiKey": "", "llmModel": "gpt-4o"}
    cleaned = cfg.strip_empty_sensitive(data, cfg.SENSITIVE_LLM_FIELDS)
    # 空密钥被移除，避免覆盖已存值
    assert "llmApiKey" not in cleaned
    assert cleaned["llmModel"] == "gpt-4o"


def test_strip_empty_sensitive_keeps_nonempty_key():
    cfg = _import_config()
    data = {"llmApiKey": "sk-new-key", "llmModel": "gpt-4o"}
    cleaned = cfg.strip_empty_sensitive(data, cfg.SENSITIVE_LLM_FIELDS)
    # 用户重新输入的密钥保留
    assert cleaned["llmApiKey"] == "sk-new-key"


def test_mask_config_does_not_mutate_input():
    cfg = _import_config()
    src = {"llmApiKey": "sk-secret"}
    cfg.mask_config(src, cfg.SENSITIVE_LLM_FIELDS)
    # 原 dict 不被修改
    assert src["llmApiKey"] == "sk-secret"
