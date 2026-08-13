"""
P2-3: encryption 服务测试
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _set_secret_key(monkeypatch):
    # 保证 config 校验通过；EncryptionService 是单例，如果之前已被别的测试触发过初始化
    # 需要强制重置一次。
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    import importlib
    import app.core.config as cfg_mod
    importlib.reload(cfg_mod)
    import app.core.encryption as enc_mod
    importlib.reload(enc_mod)
    return enc_mod


def test_encrypt_has_prefix(_set_secret_key):
    enc = _set_secret_key
    ct = enc.encrypt_sensitive_data("hello-secret")
    assert ct.startswith("enc:v1:")


def test_encrypt_decrypt_roundtrip(_set_secret_key):
    enc = _set_secret_key
    ct = enc.encrypt_sensitive_data("hello-secret")
    assert enc.decrypt_sensitive_data(ct) == "hello-secret"


def test_is_encrypted_true_only_for_prefixed(_set_secret_key):
    enc = _set_secret_key
    ct = enc.encrypt_sensitive_data("x")
    assert enc.encryption_service.is_encrypted(ct) is True
    assert enc.encryption_service.is_encrypted("not-encrypted") is False
    assert enc.encryption_service.is_encrypted("") is False


def test_decrypt_no_prefix_returns_plaintext(_set_secret_key):
    """迁移期兼容：旧的明文/未加密数据原样返回。"""
    enc = _set_secret_key
    assert enc.decrypt_sensitive_data("plain-api-key") == "plain-api-key"


def test_decrypt_prefixed_but_corrupted_raises(_set_secret_key):
    """
    关键防护：带前缀但 token 损坏 —— **service 层**必须抛 DecryptionError，
    绝不能返回原文，否则会把随机 base64 塞进 LLM 请求。
    注意：L4 后，顶层 helper decrypt_sensitive_data 默认吞异常返空串；
    要走严格路径需直接调 encryption_service.decrypt 或用 strict=True。
    """
    enc = _set_secret_key
    with pytest.raises(enc.DecryptionError):
        enc.encryption_service.decrypt("enc:v1:garbage-token-not-valid-fernet")


def test_decrypt_empty(_set_secret_key):
    enc = _set_secret_key
    assert enc.decrypt_sensitive_data("") == ""


def test_encrypt_empty(_set_secret_key):
    enc = _set_secret_key
    assert enc.encrypt_sensitive_data("") == ""


def test_decrypt_corrupted_returns_empty_by_default(_set_secret_key):
    """
    L4: 默认非严格模式 —— 损坏密文返回空串（+ warning 日志），不抛异常。
    这样上游调用点无需 12 处 try/except，空串会自然被跳过（"未配置"），
    比"损坏密文当明文送 LLM"安全，也比 500 crash 友好。
    """
    enc = _set_secret_key
    result = enc.decrypt_sensitive_data("enc:v1:garbage-token")
    assert result == ""


def test_decrypt_corrupted_strict_raises(_set_secret_key):
    """L4: strict=True 时仍抛 DecryptionError（供密钥轮换脚本用）。"""
    enc = _set_secret_key
    with pytest.raises(enc.DecryptionError):
        enc.decrypt_sensitive_data("enc:v1:garbage-token", strict=True)
