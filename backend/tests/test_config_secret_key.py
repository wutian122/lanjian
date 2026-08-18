"""
P0-1: SECRET_KEY 强制注入 & 校验测试

保护点：
- 未设置 / 空字符串 / <32 位 / 命中弱值黑名单 —— Settings() 必须直接抛错拒绝启动
- 强值（长度 >= 32 且不在黑名单）—— 正常构造成功
"""
import pytest
from pydantic import ValidationError

# 注意：不要 `from app.core.config import settings`，那会在 import 阶段用当前 .env
# 里的 SECRET_KEY 构造单例；我们要独立测试 Settings 类本身。
from app.core.config import Settings, _INSECURE_SECRET_KEYS


# 用来避免其他必填字段（如 POSTGRES_*）继承宿主环境干扰测试的最小合法基底。
# 目前 Settings 中只有 SECRET_KEY 是强制且无默认；POSTGRES_* 都有默认值，
# 所以这里只需要控制 SECRET_KEY 与关掉 .env 加载即可。
_ENV_FILE_DISABLED = {"_env_file": None}


def _build(secret_key, monkeypatch):
    """Construct Settings with a specific SECRET_KEY, ignoring host .env."""
    # 清掉宿主/CI 环境里可能已有的 SECRET_KEY，避免污染
    monkeypatch.delenv("SECRET_KEY", raising=False)
    if secret_key is None:
        # 完全不提供 —— 触发 "field required"
        return Settings(**_ENV_FILE_DISABLED)
    return Settings(SECRET_KEY=secret_key, **_ENV_FILE_DISABLED)


class TestConfigSecretKey:
    def test_config_secret_key_rejects_missing(self, monkeypatch):
        """未提供 SECRET_KEY 时应直接失败（field required）。"""
        with pytest.raises(ValidationError) as exc_info:
            _build(None, monkeypatch)
        # Pydantic v1 报 "field required"，v2 报 "Field required"，兼容大小写
        assert "secret_key" in str(exc_info.value).lower()

    def test_config_secret_key_rejects_empty(self, monkeypatch):
        """空字符串应被 validator 拒绝。"""
        with pytest.raises(ValidationError):
            _build("", monkeypatch)

    def test_config_secret_key_rejects_short(self, monkeypatch):
        """长度 < 32 应被拒绝。"""
        short = "a" * 31
        with pytest.raises(ValidationError) as exc_info:
            _build(short, monkeypatch)
        assert "too short" in str(exc_info.value).lower() or "31" in str(exc_info.value)

    @pytest.mark.parametrize("weak", sorted(_INSECURE_SECRET_KEYS))
    def test_config_secret_key_rejects_known_defaults(self, weak, monkeypatch):
        """
        黑名单里的每一个弱值都应被拒绝。
        注意：黑名单里的短值（如 "secret"）会先被长度检查挡下，也算拒绝成功；
        只要 Settings() 抛 ValidationError 即认为覆盖到位。
        """
        with pytest.raises(ValidationError):
            _build(weak, monkeypatch)

    def test_config_secret_key_rejects_weak_value_with_correct_length(self, monkeypatch):
        """
        长度足够但命中黑名单 —— 单独构造一个 >=32 位的已知弱值验证：
        黑名单里 "changethis_in_production_to_a_long_random_string"
        长度 48，足以通过长度检查，只能被黑名单挡下。
        """
        weak_long = "changethis_in_production_to_a_long_random_string"
        assert len(weak_long) >= 32  # 前置条件
        assert weak_long in _INSECURE_SECRET_KEYS
        with pytest.raises(ValidationError) as exc_info:
            _build(weak_long, monkeypatch)
        assert "well-known default" in str(exc_info.value) or "MUST be changed" in str(exc_info.value)

    def test_config_secret_key_accepts_strong_value(self, monkeypatch):
        """强值应被接受。"""
        # secrets.token_urlsafe(48) 生成的样式：64 字符 base64url，不会撞黑名单
        strong = "s3cure-Rand0m-Token-9ZQ_kY7uPvL2mNw6xJ4tE8bV1aH-c" * 2  # 100 字符
        assert len(strong) >= 32
        assert strong.strip().lower() not in _INSECURE_SECRET_KEYS
        settings = _build(strong, monkeypatch)
        assert settings.SECRET_KEY == strong
