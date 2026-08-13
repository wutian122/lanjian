"""Wave 2 §3.2 OrchestratorRegistry 测试

覆盖 Scenario:
- 存活写入与读取
- TTL 过期
- 跨 registry 实例可见（多 worker 场景）
- Redis 不可用时降级到进程内 dict
"""
import asyncio
import json
import time

import pytest

from app.services.agent.core.orchestrator_registry import OrchestratorRegistry


class _FakeRedis:
    """一个极简 in-memory fake Redis，用于测试 registry 与真实 aioredis 契约"""

    def __init__(self):
        self._data = {}  # key -> (value, expires_at)

    async def set(self, key, value, ex=None):
        expires = time.time() + ex if ex else None
        self._data[key] = (value, expires)

    async def get(self, key):
        entry = self._data.get(key)
        if not entry:
            return None
        value, expires = entry
        if expires and time.time() > expires:
            self._data.pop(key, None)
            return None
        return value

    async def delete(self, key):
        self._data.pop(key, None)


class _BrokenRedis:
    """所有操作都抛异常的 Redis，用于测试 fallback"""

    async def set(self, *a, **kw):
        raise ConnectionError("Redis unavailable")

    async def get(self, *a, **kw):
        raise ConnectionError("Redis unavailable")

    async def delete(self, *a, **kw):
        raise ConnectionError("Redis unavailable")


class TestRegistryRedisBackend:
    """使用 fake Redis 的正常路径测试"""

    @pytest.mark.asyncio
    async def test_set_and_get_alive(self):
        registry = OrchestratorRegistry(redis_client=_FakeRedis())
        await registry.set_alive("task_a", worker_id="worker_1")

        data = await registry.get("task_a")
        assert data is not None
        assert data["worker_id"] == "worker_1"
        assert isinstance(data["alive_at"], int)

        assert await registry.is_alive("task_a") is True

    @pytest.mark.asyncio
    async def test_ttl_expires_and_is_alive_returns_false(self):
        registry = OrchestratorRegistry(redis_client=_FakeRedis())
        await registry.set_alive("task_ttl", ttl_seconds=1)

        assert await registry.is_alive("task_ttl") is True
        await asyncio.sleep(1.2)
        assert await registry.is_alive("task_ttl") is False

    @pytest.mark.asyncio
    async def test_stale_alive_at_marks_not_alive(self):
        """alive_at 老于 stale_threshold_seconds 时 is_alive=False，即使 key 还没过期"""
        redis = _FakeRedis()
        registry = OrchestratorRegistry(redis_client=redis)
        # 直接注入一个 40 秒前的 alive_at
        stale = {"alive_at": int(time.time()) - 40, "worker_id": "w", "event_manager_local": True}
        await redis.set("lanjian:orch:stale_task", json.dumps(stale), ex=60)

        assert await registry.is_alive("stale_task", stale_threshold_seconds=30) is False

    @pytest.mark.asyncio
    async def test_clear_removes_entry(self):
        registry = OrchestratorRegistry(redis_client=_FakeRedis())
        await registry.set_alive("task_clear")
        assert await registry.is_alive("task_clear") is True

        await registry.clear("task_clear")
        assert await registry.is_alive("task_clear") is False

    @pytest.mark.asyncio
    async def test_get_worker(self):
        registry = OrchestratorRegistry(redis_client=_FakeRedis())
        await registry.set_alive("task_w", worker_id="host_x:42")
        assert await registry.get_worker("task_w") == "host_x:42"


class TestRegistryCrossInstance:
    """§3.2 Scenario: 多 worker 下另一个 worker 也能看到 (共享同一 Redis)"""

    @pytest.mark.asyncio
    async def test_two_registries_share_state_via_redis(self):
        shared_redis = _FakeRedis()
        registry_a = OrchestratorRegistry(redis_client=shared_redis)
        registry_b = OrchestratorRegistry(redis_client=shared_redis)  # 新实例，同一 redis

        await registry_a.set_alive("shared_task", worker_id="worker_A")

        # registry_b 通过共享 Redis 也能查到
        assert await registry_b.is_alive("shared_task") is True
        assert await registry_b.get_worker("shared_task") == "worker_A"


class TestRegistryFallback:
    """§3.2 Scenario: Redis 不可用时降级到进程内 dict"""

    @pytest.mark.asyncio
    async def test_fallback_when_redis_broken(self):
        """Redis 抛异常时，registry 应 fallback 到进程内 dict，功能不受影响"""
        registry = OrchestratorRegistry(redis_client=_BrokenRedis())

        # set 应该不抛错（内部 catch 后走 fallback dict）
        await registry.set_alive("fb_task", worker_id="fb_worker")
        # is_alive 仍能读回（从 fallback dict）
        assert await registry.is_alive("fb_task") is True
        # get_worker 也能读
        assert await registry.get_worker("fb_task") == "fb_worker"
        # clear 也不抛错
        await registry.clear("fb_task")
        assert await registry.is_alive("fb_task") is False

    @pytest.mark.asyncio
    async def test_fallback_with_no_redis_client(self):
        """redis_client=None 也应能作为纯进程内 registry 工作"""
        registry = OrchestratorRegistry(redis_client=None)
        await registry.set_alive("np_task", worker_id="np_worker", ttl_seconds=60)

        assert await registry.is_alive("np_task") is True
        assert await registry.get_worker("np_task") == "np_worker"

        await registry.clear("np_task")
        assert await registry.is_alive("np_task") is False

    @pytest.mark.asyncio
    async def test_fallback_dict_ttl_expires(self):
        registry = OrchestratorRegistry(redis_client=None)
        await registry.set_alive("ttl_task", ttl_seconds=1)
        assert await registry.is_alive("ttl_task") is True
        await asyncio.sleep(1.2)
        assert await registry.is_alive("ttl_task") is False
