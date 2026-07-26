"""
Redis 连接管理 — 用于 token 黑名单、登录限流等
"""
import os
from typing import Optional

import redis.asyncio as aioredis

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
