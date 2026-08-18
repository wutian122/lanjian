"""
R3 修复测试：embedding 限流与持久化缓存

根因：embed_batch 仅内存缓存（进程重启丢失），无全局限流器，固定 sleep(0.3)
      无法适配 siliconflow 限流，频发 429。
修复：本地磁盘缓存（sha256(text)+model 前缀）+ asyncio.Semaphore(4) + 令牌桶控速。
"""
import pytest
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

from app.services.rag.embeddings import EmbeddingService


def _make_service_with_disk_cache(cache_dir, rate_limit=5):
    """构造带磁盘缓存的 EmbeddingService，provider mock。"""
    provider = MagicMock()
    provider.dimension = 8
    provider.embed_text = AsyncMock(return_value=MagicMock(embedding=[0.1] * 8))
    provider.embed_texts = AsyncMock(return_value=[MagicMock(embedding=[0.2] * 8)])

    service = EmbeddingService.__new__(EmbeddingService)
    service.cache_enabled = True
    service._cache = {}
    service._cache_dir = cache_dir
    service.provider = "openai"
    service.model = "bge-m3"
    service._provider = provider
    service._rate_limit = rate_limit
    import asyncio
    service._semaphore = asyncio.Semaphore(4)
    service._last_request_time = 0.0
    service._disk_cache_loaded = False
    return service, provider


class TestEmbeddingDiskCache:
    """验证持久化磁盘缓存"""

    @pytest.mark.asyncio
    async def test_embedding_cache_hit_skips_api(self):
        """R3: 缓存命中跳过 API"""
        with tempfile.TemporaryDirectory() as d:
            service, provider = _make_service_with_disk_cache(d)
            # 第一次 embed，调 API
            await service.embed("hello world")
            assert provider.embed_text.call_count == 1
            # 第二次同文本，应命中缓存
            await service.embed("hello world")
            assert provider.embed_text.call_count == 1, "缓存命中不应再调 API"

    @pytest.mark.asyncio
    async def test_embedding_cache_persists_to_disk(self):
        """R3: embed 后磁盘缓存文件存在"""
        with tempfile.TemporaryDirectory() as d:
            service, provider = _make_service_with_disk_cache(d)
            await service.embed("persist me")
            # 磁盘缓存文件应存在
            files = os.listdir(d)
            assert len(files) > 0, "缓存应持久化到磁盘"

    @pytest.mark.asyncio
    async def test_embedding_cache_loads_from_disk(self):
        """R3: 新 service 实例从磁盘加载缓存，不调 API"""
        with tempfile.TemporaryDirectory() as d:
            service1, _ = _make_service_with_disk_cache(d)
            await service1.embed("cross process")

            # 新实例同缓存目录
            service2, provider2 = _make_service_with_disk_cache(d)
            await service2.embed("cross process")
            assert provider2.embed_text.call_count == 0, "磁盘缓存加载后不应调 API"

    @pytest.mark.asyncio
    async def test_embedding_cache_key_includes_model(self):
        """R3: 缓存 key 含 model 前缀，换模型自动失效"""
        with tempfile.TemporaryDirectory() as d:
            service, provider = _make_service_with_disk_cache(d)
            await service.embed("same text")
            assert provider.embed_text.call_count == 1
            # 换 model
            service.model = "other-model"
            await service.embed("same text")
            assert provider.embed_text.call_count == 2, "换 model 后缓存应失效"
