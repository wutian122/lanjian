"""Orchestrator 跨进程存活状态注册表 (Wave 2 §3.2).

将进程内 dict (_running_event_managers / _running_orchestrators / _running_asyncio_tasks)
的存活语义抽象成 Redis 存储，使得：
- --workers >1 部署或 uvicorn --reload 重启后，前端能通过 orchestrator_alive 字段
  感知 stale running 任务
- 多 worker 环境下不同 worker 都能查到任务是否活跃

Redis 键空间: lanjian:orch:{task_id}
字段:
- alive_at (unix ts, seconds): Orchestrator 主循环每 5 秒刷新
- worker_id (str): "{hostname}:{pid}"
- event_manager_local (bool): 本进程 event_manager 是否可用

Redis 不可用时降级到进程内 dict + WARNING 日志。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OrchestratorRegistry:
    """Orchestrator 存活状态注册表，Redis-backed，fallback 到进程内 dict"""

    KEY_PREFIX = "lanjian:orch:"
    DEFAULT_TTL_SECONDS = 60
    DEFAULT_STALE_THRESHOLD = 30  # alive_at > now-30s 视为存活

    def __init__(self, redis_client=None) -> None:
        """
        Args:
            redis_client: aioredis 异步客户端，None 时走进程内 fallback dict
        """
        self._redis = redis_client
        self._fallback: Dict[str, Dict[str, Any]] = {}
        self._worker_id = f"{socket.gethostname()}:{os.getpid()}"

    def _key(self, task_id: str) -> str:
        return f"{self.KEY_PREFIX}{task_id}"

    async def set_alive(
        self,
        task_id: str,
        worker_id: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        event_manager_local: bool = True,
    ) -> None:
        """标记任务活跃 (Orchestrator 主循环每 5s 调用)"""
        payload = {
            "alive_at": int(time.time()),
            "worker_id": worker_id or self._worker_id,
            "event_manager_local": event_manager_local,
        }
        if self._redis is not None:
            try:
                await self._redis.set(
                    self._key(task_id), json.dumps(payload), ex=ttl_seconds
                )
                return
            except Exception as e:
                logger.warning(
                    f"[OrchestratorRegistry] Redis set failed for {task_id}, falling back: {e}"
                )
        self._fallback[task_id] = {**payload, "_expires": time.time() + ttl_seconds}

    async def is_alive(
        self, task_id: str, stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD
    ) -> bool:
        """任务是否活跃 (alive_at > now - stale_threshold)"""
        data = await self.get(task_id)
        if not data:
            return False
        alive_at = data.get("alive_at", 0)
        return (time.time() - alive_at) < stale_threshold_seconds

    async def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取完整存活信息，若不存在或 Redis fallback 已过期返回 None"""
        if self._redis is not None:
            try:
                raw = await self._redis.get(self._key(task_id))
                if raw:
                    return json.loads(raw)
                return None
            except Exception as e:
                logger.warning(
                    f"[OrchestratorRegistry] Redis get failed for {task_id}, using fallback: {e}"
                )
        entry = self._fallback.get(task_id)
        if not entry:
            return None
        if entry.get("_expires", 0) < time.time():
            self._fallback.pop(task_id, None)
            return None
        return {k: v for k, v in entry.items() if not k.startswith("_")}

    async def get_worker(self, task_id: str) -> Optional[str]:
        """获取任务所在 worker 的 ID（供多 worker 场景调度参考）"""
        data = await self.get(task_id)
        return data.get("worker_id") if data else None

    async def clear(self, task_id: str) -> None:
        """任务完成/失败时清理 Redis 键（best-effort，正常也会 TTL 过期自动清）"""
        if self._redis is not None:
            try:
                await self._redis.delete(self._key(task_id))
            except Exception as e:
                logger.warning(
                    f"[OrchestratorRegistry] Redis delete failed for {task_id}: {e}"
                )
        self._fallback.pop(task_id, None)


# ============ 全局单例 ============

_registry: Optional[OrchestratorRegistry] = None
_registry_lock = asyncio.Lock()


async def get_registry() -> OrchestratorRegistry:
    """获取全局 registry 单例。首次调用时初始化 Redis client；Redis 不可用时 fallback。"""
    global _registry
    if _registry is not None:
        return _registry
    async with _registry_lock:
        if _registry is not None:
            return _registry
        try:
            from app.core.redis import get_redis  # 延迟导入避免循环

            redis_client = await get_redis()
            _registry = OrchestratorRegistry(redis_client=redis_client)
            logger.info("[OrchestratorRegistry] Initialized with Redis backend")
        except Exception as e:
            logger.warning(
                f"[OrchestratorRegistry] Redis init failed, using in-process fallback: {e}"
            )
            _registry = OrchestratorRegistry(redis_client=None)
    return _registry
