"""
敏感信息加密服务
使用 Fernet 对称加密算法加密 API Key 等敏感信息
"""

import base64
import hashlib
import logging
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings

logger = logging.getLogger(__name__)


# P2-3: 版本化前缀
#
# 旧实现：encrypt 直接返回 Fernet.encrypt() 结果；decrypt 遇错静默返回原文。
# 问题：
#   1) 密钥轮换 / SECRET_KEY 被误改 → 所有旧密文默默变成"看起来正常的字符串"，
#      直接被拼进 LLM 请求 / 数据库；
#   2) is_encrypted 用异常路径判定，性能差且不精确。
# 现在给密文加固定前缀 ``enc:v1:``：
#   - 加密：`enc:v1:<Fernet token>`；
#   - 解密：有前缀 → 严格解密，失败抛 :class:`DecryptionError`；
#          无前缀 → 视为迁移期遗留明文，原样返回。这允许平滑迁移旧数据，
#          但同时确保**密文损坏不会再静默降级成明文**。
_ENC_PREFIX = "enc:v1:"


class DecryptionError(RuntimeError):
    """
    解密带 ``enc:v1:`` 前缀的密文失败。

    调用方需要显式处理（通常是把配置标为损坏、要求重新填入）；**绝对不要**
    捕获后返回原始密文当明文，那会把随机 base64 塞进对外请求。
    """


class EncryptionService:
    """加密服务 - 用于加密和解密敏感信息"""

    _instance: Optional['EncryptionService'] = None
    _fernet: Optional[Fernet] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_fernet()
        return cls._instance

    def _init_fernet(self):
        """初始化 Fernet 加密器，使用 SECRET_KEY 派生密钥"""
        # 使用 SHA256 哈希 SECRET_KEY 生成 32 字节密钥
        key_bytes = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        # Fernet 需要 base64 编码的 32 字节密钥
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        self._fernet = Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        """
        加密明文字符串。

        Returns:
            带 ``enc:v1:`` 前缀的密文；空明文返回空串。
        """
        if not plaintext:
            return ""

        encrypted = self._fernet.encrypt(plaintext.encode('utf-8'))
        return _ENC_PREFIX + encrypted.decode('utf-8')

    def decrypt(self, ciphertext: str) -> str:
        """
        解密密文字符串。

        - 空串 → 空串；
        - 带 ``enc:v1:`` 前缀 → 严格解密。失败抛 :class:`DecryptionError`；
        - 无前缀 → 视为迁移期遗留明文，原样返回（仅日志 debug 级提示）。

        Raises:
            DecryptionError: 前缀有但 Fernet token 损坏 / SECRET_KEY 不匹配。
        """
        if not ciphertext:
            return ""

        if ciphertext.startswith(_ENC_PREFIX):
            token = ciphertext[len(_ENC_PREFIX):]
            try:
                decrypted = self._fernet.decrypt(token.encode('utf-8'))
                return decrypted.decode('utf-8')
            except InvalidToken as e:
                logger.error(
                    "encryption.decrypt: Fernet InvalidToken —— 密文与当前 SECRET_KEY 不匹配 "
                    "或密文已被篡改。上层必须显式处理，不能返回原文。"
                )
                raise DecryptionError(
                    "无法解密敏感信息（密钥不匹配或密文损坏）。请检查 SECRET_KEY 是否被修改。"
                ) from e

        # 无前缀：迁移期兼容旧的明文/未加密数据
        logger.debug("encryption.decrypt: 无 %s 前缀，按明文返回（迁移期兼容）", _ENC_PREFIX)
        return ciphertext

    def is_encrypted(self, value: str) -> bool:
        """
        检查值是否已加密（带 ``enc:v1:`` 前缀）。

        P2-3 前用 try/except 判定，性能差且对损坏的旧密文误判为"未加密"。
        现在只看前缀，语义清晰、快。
        """
        return bool(value) and value.startswith(_ENC_PREFIX)


# 全局加密服务实例
encryption_service = EncryptionService()


def encrypt_sensitive_data(data: str) -> str:
    """加密敏感数据的便捷函数"""
    return encryption_service.encrypt(data)


def decrypt_sensitive_data(data: str, *, strict: bool = False) -> str:
    """
    解密敏感数据的便捷函数。

    L4: 默认 ``strict=False`` —— ``DecryptionError`` 会被吞掉并返回空串 + warning 日志。
        空串是明确的"配置损坏"信号，调用点会自然跳过（如"未配置 API Key"），不会把
        损坏密文当明文送进 LLM 请求（这才是 P2-3 真正想防的）。

    调用点如需严格模式（如密钥轮换脚本、密文校验工具），显式传 ``strict=True``。

    Raises:
        DecryptionError: 仅当 strict=True 且密文损坏时。
    """
    try:
        return encryption_service.decrypt(data)
    except DecryptionError:
        if strict:
            raise
        # 非严格模式：日志已在 EncryptionService.decrypt 里打过 error 级；
        # 这里再打一条 warning 提示上层收到了空串。
        logger.warning(
            "decrypt_sensitive_data: 密文损坏，返回空串。检查 SECRET_KEY 是否被轮换。"
        )
        return ""


# ============ P2-4: 敏感字段单一真相源 ============
#
# 现状：SENSITIVE_OTHER_FIELDS 在 5 个 endpoint 各写各的，字段不一致，导致：
#   - giteaToken 只在 projects.py:754 加密，其他 endpoint 读取时不解密 → 存明文
#   - sshPrivateKey 只在 projects.py:459 解密，其他不加密 → 也可能存明文
# 现在把列表提到这里，让所有 endpoint 都 import 同一份，永远同步。
# 未来新增敏感字段，只需要动这里。
#
# SENSITIVE_LLM_FIELDS 已在 endpoints/config.py 定义了 11 个 provider 完整列表，
# 本次不动它，只统一 OTHER 字段。

SENSITIVE_OTHER_FIELDS = (
    "githubToken",
    "gitlabToken",
    "giteaToken",
    "sshPrivateKey",
)
