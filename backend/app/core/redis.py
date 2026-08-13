"""
Redis 连接管理 — 用于 token 黑名单、登录限流等
"""
import logging
import os
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """获取 Redis 连接（单例）"""
    global _redis_pool
    if _redis_pool is None:
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _redis_pool = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _redis_pool


async def close_redis() -> None:
    """关闭 Redis 连接"""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None


async def is_blacklisted(jti: str) -> bool:
    """检查 token jti 是否已被登出拉黑（#3-B）。

    注意：Redis 异常时返回 False（fail-open）并记 warning。
    黑名单是登出撤销的增量防护，主防线仍是 JWT 签名与过期 ——
    避免 Redis 抖动导致全站正常用户被 401 踢下线。
    """
    if not jti:
        return False
    try:
        redis = await get_redis()
        return bool(await redis.get(f"logout:blacklist:{jti}"))
    except Exception as exc:  # pragma: no cover
        logger.warning(f"查询 token 黑名单失败（按未拉黑处理）: {exc}")
        return False
