"""
嵌入模型服务
支持多种嵌入模型提供商: OpenAI, Azure, Ollama, Cohere, HuggingFace, Jina
"""

import asyncio
import hashlib
import logging
import os
import json
import time
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    """嵌入结果"""
    embedding: List[float]
    tokens_used: int
    model: str


class EmbeddingProvider(ABC):
    """嵌入提供商基类"""
    
    @abstractmethod
    async def embed_text(self, text: str) -> EmbeddingResult:
        """嵌入单个文本"""
        pass
    
    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        """批量嵌入文本"""
        pass
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """嵌入向量维度"""
        pass


class OpenAIEmbedding(EmbeddingProvider):
    """OpenAI 嵌入服务"""
    
    MODELS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        "BAAI/bge-m3": 1024,
        "bge-m3": 1024,
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "text-embedding-3-small",
        dimension: Optional[int] = None,
    ):
        self.api_key = api_key or settings.LLM_API_KEY
        self.base_url = base_url or "https://api.openai.com/v1"
        self.model = model
        self._dimension = dimension or self.MODELS.get(model, 1536)
    
    @property
    def dimension(self) -> int:
        return self._dimension
    
    async def embed_text(self, text: str) -> EmbeddingResult:
        results = await self.embed_texts([text])
        return results[0]
    
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        
        max_length = 8191
        truncated_texts = [text[:max_length] for text in texts]
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "input": truncated_texts,
        }
        
        url = f"{self.base_url.rstrip(chr(47))}/embeddings"
        
        # 指数退避重试（最多 5 次）
        max_retries = 5
        base_delay = 1.0
        
        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code == 429:
                    retry_after_header = response.headers.get("Retry-After")
                    if retry_after_header:
                        try:
                            retry_after = float(retry_after_header)
                        except ValueError:
                            retry_after = base_delay * (2 ** attempt)
                    else:
                        retry_after = base_delay * (2 ** attempt)
                    
                    retry_after = min(retry_after, 30.0)
                    logger.warning(
                        f"Embedding API rate limited (429), retrying in {retry_after:.1f}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(retry_after)
                    continue
                
                response.raise_for_status()
                data = response.json()
                
                results = []
                for item in data.get("data", []):
                    results.append(EmbeddingResult(
                        embedding=item["embedding"],
                        tokens_used=data.get("usage", {}).get("total_tokens", 0) // len(texts),
                        model=self.model,
                    ))
                
                return results
        
        raise Exception(f"Embedding API rate limited after {max_retries} retries for model {self.model}")


class AzureOpenAIEmbedding(EmbeddingProvider):
    """
    Azure OpenAI 嵌入服务
    
    使用最新 API 版本 2024-10-21 (GA)
    端点格式: https://<resource>.openai.azure.com/openai/deployments/<deployment>/embeddings?api-version=2024-10-21
    """
    
    MODELS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        "BAAI/bge-m3": 1024,
        "bge-m3": 1024,
    }
    
    # 最新的 GA API 版本
    API_VERSION = "2024-10-21"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "text-embedding-3-small",
    ):
        self.api_key = api_key
        self.base_url = base_url or "https://your-resource.openai.azure.com"
        self.model = model
        self._dimension = self.MODELS.get(model, 1536)
    
    @property
    def dimension(self) -> int:
        return self._dimension
    
    async def embed_text(self, text: str) -> EmbeddingResult:
        results = await self.embed_texts([text])
        return results[0]
    
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        
        max_length = 8191
        truncated_texts = [text[:max_length] for text in texts]
        
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        
        payload = {
            "input": truncated_texts,
        }
        
        # Azure URL 格式 - 使用最新 API 版本
        url = f"{self.base_url.rstrip('/')}/openai/deployments/{self.model}/embeddings?api-version={self.API_VERSION}"
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get("data", []):
                results.append(EmbeddingResult(
                    embedding=item["embedding"],
                    tokens_used=data.get("usage", {}).get("total_tokens", 0) // len(texts),
                    model=self.model,
                ))
            
            return results


class OllamaEmbedding(EmbeddingProvider):
    """
    Ollama 本地嵌入服务

    使用新的 /api/embed 端点 (2024年起):
    - 支持批量嵌入
    - 使用 'input' 参数（支持字符串或字符串数组）
    """

    # 默认维度映射（基础模型版本）
    # 注意：同一模型不同参数规模可能有不同维度
    # 例如 qwen3-embedding:0.6b=1024, qwen3-embedding:8b=4096
    # 用户可通过 dimension 参数覆盖
    MODELS = {
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
        "all-minilm": 384,
        "snowflake-arctic-embed": 1024,
        "bge-m3": 1024,
        "qwen3-embedding": 1024,  # 默认值，8b版本为4096
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: str = "nomic-embed-text",
        dimension: Optional[int] = None,
    ):
        self.base_url = base_url or "http://localhost:11434"
        self.model = model
        # 用户指定的维度优先，否则使用默认映射
        self._dimension = dimension if dimension else self.MODELS.get(model, 768)
    
    @property
    def dimension(self) -> int:
        return self._dimension
    
    async def embed_text(self, text: str) -> EmbeddingResult:
        results = await self.embed_texts([text])
        return results[0]
    
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        
        # 新的 Ollama /api/embed 端点
        url = f"{self.base_url.rstrip('/')}/api/embed"
        
        payload = {
            "model": self.model,
            "input": texts,  # 新 API 使用 'input' 参数，支持批量
        }
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # 新 API 返回格式: {"embeddings": [[...], [...], ...]}
            embeddings = data.get("embeddings", [])
            
            results = []
            for i, embedding in enumerate(embeddings):
                results.append(EmbeddingResult(
                    embedding=embedding,
                    tokens_used=len(texts[i]) // 4,
                    model=self.model,
                ))
            
            return results


class CohereEmbedding(EmbeddingProvider):
    """
    Cohere 嵌入服务
    
    使用新的 v2 API (2024年起):
    - 端点: https://api.cohere.com/v2/embed
    - 使用 'inputs' 参数替代 'texts'
    - 需要指定 'embedding_types'
    """
    
    MODELS = {
        "embed-english-v3.0": 1024,
        "embed-multilingual-v3.0": 1024,
        "embed-english-light-v3.0": 384,
        "embed-multilingual-light-v3.0": 384,
        "embed-v4.0": 1024,  # 最新模型
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "embed-multilingual-v3.0",
    ):
        self.api_key = api_key
        # 新的 v2 API 端点
        self.base_url = base_url or "https://api.cohere.com/v2"
        self.model = model
        self._dimension = self.MODELS.get(model, 1024)
    
    @property
    def dimension(self) -> int:
        return self._dimension
    
    async def embed_text(self, text: str) -> EmbeddingResult:
        results = await self.embed_texts([text])
        return results[0]
    
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # v2 API 参数格式
        payload = {
            "model": self.model,
            "inputs": texts,  # v2 使用 'inputs' 而非 'texts'
            "input_type": "search_document",
            "embedding_types": ["float"],  # v2 需要指定嵌入类型
        }
        
        url = f"{self.base_url.rstrip('/')}/embed"
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            results = []
            # v2 API 返回格式: {"embeddings": {"float": [[...], [...]]}, ...}
            embeddings_data = data.get("embeddings", {})
            embeddings = embeddings_data.get("float", []) if isinstance(embeddings_data, dict) else embeddings_data
            
            for embedding in embeddings:
                results.append(EmbeddingResult(
                    embedding=embedding,
                    tokens_used=data.get("meta", {}).get("billed_units", {}).get("input_tokens", 0) // max(len(texts), 1),
                    model=self.model,
                ))
            
            return results


class HuggingFaceEmbedding(EmbeddingProvider):
    """
    HuggingFace Inference Providers 嵌入服务
    
    使用新的 Router 端点 (2025年起):
    https://router.huggingface.co/hf-inference/models/{model}/pipeline/feature-extraction
    """
    
    MODELS = {
        "sentence-transformers/all-MiniLM-L6-v2": 384,
        "sentence-transformers/all-mpnet-base-v2": 768,
        "BAAI/bge-large-zh-v1.5": 1024,
        "BAAI/bge-m3": 1024,
        "BAAI/bge-small-en-v1.5": 384,
        "BAAI/bge-base-en-v1.5": 768,
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "BAAI/bge-m3",
    ):
        self.api_key = api_key
        # 新的 Router 端点
        self.base_url = base_url or "https://router.huggingface.co"
        self.model = model
        self._dimension = self.MODELS.get(model, 1024)
    
    @property
    def dimension(self) -> int:
        return self._dimension
    
    async def embed_text(self, text: str) -> EmbeddingResult:
        results = await self.embed_texts([text])
        return results[0]
    
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # 新的 HuggingFace Router URL 格式
        # https://router.huggingface.co/hf-inference/models/{model}/pipeline/feature-extraction
        url = f"{self.base_url.rstrip('/')}/hf-inference/models/{self.model}/pipeline/feature-extraction"
        
        payload = {
            "inputs": texts,
            "options": {
                "wait_for_model": True,
            }
        }
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            results = []
            # HuggingFace 返回格式: [[embedding1], [embedding2], ...]
            for embedding in data:
                # 有时候返回的是嵌套的列表
                if isinstance(embedding, list) and len(embedding) > 0:
                    if isinstance(embedding[0], list):
                        # 取平均或第一个
                        embedding = embedding[0]
                
                results.append(EmbeddingResult(
                    embedding=embedding,
                    tokens_used=len(texts[len(results)]) // 4,
                    model=self.model,
                ))
            
            return results


class JinaEmbedding(EmbeddingProvider):
    """Jina AI 嵌入服务"""
    
    MODELS = {
        "jina-embeddings-v2-base-code": 768,
        "jina-embeddings-v2-base-en": 768,
        "jina-embeddings-v2-base-zh": 768,
        "jina-embeddings-v2-small-en": 512,
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "jina-embeddings-v2-base-code",
    ):
        self.api_key = api_key
        self.base_url = base_url or "https://api.jina.ai/v1"
        self.model = model
        self._dimension = self.MODELS.get(model, 768)
    
    @property
    def dimension(self) -> int:
        return self._dimension
    
    async def embed_text(self, text: str) -> EmbeddingResult:
        results = await self.embed_texts([text])
        return results[0]
    
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "input": texts,
        }
        
        url = f"{self.base_url.rstrip('/')}/embeddings"
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get("data", []):
                results.append(EmbeddingResult(
                    embedding=item["embedding"],
                    tokens_used=data.get("usage", {}).get("total_tokens", 0) // len(texts),
                    model=self.model,
                ))
            
            return results


class QwenEmbedding(EmbeddingProvider):
    """Qwen 嵌入服务（基于阿里云 DashScope embeddings API）"""
    
    MODELS = {
        # DashScope Qwen 嵌入模型及其默认维度
        "text-embedding-v4": 1024,  # 支持维度: 2048, 1536, 1024(默认), 768, 512, 256, 128, 64
        "text-embedding-v3": 1024,  # 支持维度: 1024(默认), 768, 512, 256, 128, 64
        "text-embedding-v2": 1536,  # 支持维度: 1536
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "text-embedding-v4",
    ):
        # 优先使用显式传入的 api_key，其次使用 EMBEDDING_API_KEY/QWEN_API_KEY/LLM_API_KEY
        self.api_key = (
            api_key
            or getattr(settings, "EMBEDDING_API_KEY", None)
            or getattr(settings, "QWEN_API_KEY", None)
            or settings.LLM_API_KEY
        )
        # 🔥 API 密钥验证
        if not self.api_key:
            raise ValueError(
                "Qwen embedding requires API key. "
                "Set EMBEDDING_API_KEY, QWEN_API_KEY or LLM_API_KEY environment variable."
            )
        # DashScope 兼容 OpenAI 的 embeddings 端点
        self.base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model = model
        self._dimension = self.MODELS.get(model, 1024)
    
    @property
    def dimension(self) -> int:
        return self._dimension
    
    async def embed_text(self, text: str) -> EmbeddingResult:
        results = await self.embed_texts([text])
        return results[0]
    
    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []

        # 与 OpenAI 接口保持一致的截断策略
        max_length = 8191
        truncated_texts = [text[:max_length] for text in texts]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "input": truncated_texts,
            "encoding_format": "float",
        }

        url = f"{self.base_url.rstrip('/')}/embeddings"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                usage = data.get("usage", {}) or {}
                total_tokens = usage.get("total_tokens") or usage.get("prompt_tokens") or 0

                results: List[EmbeddingResult] = []
                for item in data.get("data", []):
                    results.append(EmbeddingResult(
                        embedding=item["embedding"],
                        tokens_used=total_tokens // max(len(texts), 1),
                        model=self.model,
                    ))

                return results
        except httpx.HTTPStatusError as e:
            logger.error(f"Qwen embedding API error: {e.response.status_code} - {e.response.text}")
            raise RuntimeError(f"Qwen embedding API failed: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Qwen embedding network error: {e}")
            raise RuntimeError(f"Qwen embedding network error: {e}") from e
        except Exception as e:
            logger.error(f"Qwen embedding unexpected error: {e}")
            raise RuntimeError(f"Qwen embedding failed: {e}") from e


class EmbeddingService:
    """
    嵌入服务
    统一管理嵌入模型和缓存

    支持的提供商:
    - openai: OpenAI 官方
    - azure: Azure OpenAI
    - ollama: Ollama 本地
    - cohere: Cohere
    - huggingface: HuggingFace Inference API
    - jina: Jina AI
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        dimension: Optional[int] = None,
        cache_enabled: bool = True,
    ):
        """
        初始化嵌入服务

        Args:
            provider: 提供商 (openai, azure, ollama, cohere, huggingface, jina)
            model: 模型名称
            api_key: API Key
            base_url: API Base URL
            dimension: 向量维度（可选，用于覆盖默认值）
            cache_enabled: 是否启用缓存
        """
        self.cache_enabled = cache_enabled
        self._cache: Dict[str, List[float]] = {}

        # 确定提供商（保存原始值用于属性访问）
        self.provider = provider or getattr(settings, 'EMBEDDING_PROVIDER', 'openai')
        self.model = model or getattr(settings, 'EMBEDDING_MODEL', 'text-embedding-3-small')
        self.api_key = api_key
        self.base_url = base_url
        self.custom_dimension = dimension

        # R3: 持久化磁盘缓存 + 限流
        self._cache_dir = getattr(settings, 'EMBEDDING_CACHE_DIR', None) or os.path.join(
            getattr(settings, 'VECTOR_DB_PATH', './data/vector_db'), '..', 'embedding_cache'
        )
        self._rate_limit = getattr(settings, 'EMBEDDING_RATE_LIMIT', 5)  # req/s
        self._semaphore = asyncio.Semaphore(4)
        self._last_request_time = 0.0
        self._disk_cache_loaded = False
        if self.cache_enabled and self._cache_dir:
            try:
                os.makedirs(self._cache_dir, exist_ok=True)
            except Exception as e:
                logger.warning(f"创建 embedding 缓存目录失败: {e}")
                self._cache_dir = None

        # 创建提供商实例
        self._provider = self._create_provider(
            provider=self.provider,
            model=self.model,
            api_key=api_key,
            base_url=base_url,
            dimension=dimension,
        )

        logger.info(f"Embedding service initialized with {self.provider}/{self.model}, dimension={self._provider.dimension}")
    
    def _create_provider(
        self,
        provider: str,
        model: str,
        api_key: Optional[str],
        base_url: Optional[str],
        dimension: Optional[int] = None,
    ) -> EmbeddingProvider:
        """创建嵌入提供商实例"""
        provider = provider.lower()

        if provider == "ollama":
            return OllamaEmbedding(base_url=base_url, model=model, dimension=dimension)

        elif provider == "azure":
            return AzureOpenAIEmbedding(api_key=api_key, base_url=base_url, model=model)

        elif provider == "cohere":
            return CohereEmbedding(api_key=api_key, base_url=base_url, model=model)

        elif provider == "huggingface":
            return HuggingFaceEmbedding(api_key=api_key, base_url=base_url, model=model)

        elif provider == "jina":
            return JinaEmbedding(api_key=api_key, base_url=base_url, model=model)

        elif provider == "qwen":
            return QwenEmbedding(api_key=api_key, base_url=base_url, model=model)

        else:
            # 默认使用 OpenAI (兼容 SiliconFlow / 自定义 API 端点)
            return OpenAIEmbedding(api_key=api_key, base_url=base_url, model=model, dimension=dimension)
    
    @property
    def dimension(self) -> int:
        """嵌入向量维度"""
        return self._provider.dimension
    
    def _cache_key(self, text: str) -> str:
        """生成缓存键（R3: 含 model 前缀，换模型自动失效）"""
        h = hashlib.sha256(text.encode()).hexdigest()
        return f"{self.model}:{h}"

    def _disk_cache_path(self, cache_key: str) -> Optional[str]:
        """R3: 磁盘缓存文件路径"""
        if not self._cache_dir:
            return None
        # cache_key 含 ':'，转成安全文件名
        safe = cache_key.replace(":", "_").replace("/", "_")
        return os.path.join(self._cache_dir, f"{safe}.json")

    def _disk_cache_get(self, cache_key: str) -> Optional[List[float]]:
        """R3: 从磁盘读缓存"""
        path = self._disk_cache_path(cache_key)
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _disk_cache_set(self, cache_key: str, embedding: List[float]) -> None:
        """R3: 写磁盘缓存"""
        path = self._disk_cache_path(cache_key)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(embedding, f)
        except Exception as e:
            logger.warning(f"写入 embedding 磁盘缓存失败: {e}")

    def _cache_get(self, cache_key: str) -> Optional[List[float]]:
        """R3: 内存 -> 磁盘 二级缓存读"""
        if cache_key in self._cache:
            return self._cache[cache_key]
        disk = self._disk_cache_get(cache_key)
        if disk is not None:
            self._cache[cache_key] = disk
            return disk
        return None

    def _cache_set(self, cache_key: str, embedding: List[float]) -> None:
        """R3: 内存 + 磁盘二级缓存写"""
        self._cache[cache_key] = embedding
        self._disk_cache_set(cache_key, embedding)

    async def _rate_limit_acquire(self) -> None:
        """R3: 令牌桶控速 —— 限制请求频率不超过 _rate_limit req/s"""
        if not self._rate_limit or self._rate_limit <= 0:
            return
        min_interval = 1.0 / self._rate_limit
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_request_time = time.time()
    
    async def embed(self, text: str) -> List[float]:
        """
        嵌入单个文本
        
        Args:
            text: 文本内容
            
        Returns:
            嵌入向量
        """
        if not text or not text.strip():
            return [0.0] * self.dimension
        
        # 检查缓存
        if self.cache_enabled:
            cache_key = self._cache_key(text)
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        # R3: 限流
        await self._rate_limit_acquire()
        result = await self._provider.embed_text(text)

        # 存入缓存
        if self.cache_enabled:
            self._cache_set(cache_key, result.embedding)

        return result.embedding
    
    async def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
        show_progress: bool = False,
        progress_callback: Optional[callable] = None,
        cancel_check: Optional[callable] = None,
    ) -> List[List[float]]:
        """
        批量嵌入文本

        Args:
            texts: 文本列表
            batch_size: 批次大小
            show_progress: 是否显示进度
            progress_callback: 进度回调函数，接收 (processed, total) 参数
            cancel_check: 取消检查函数，返回 True 表示应该取消

        Returns:
            嵌入向量列表

        Raises:
            asyncio.CancelledError: 当 cancel_check 返回 True 时
        """
        if not texts:
            return []

        embeddings = []
        uncached_indices = []
        uncached_texts = []

        # 检查缓存
        for i, text in enumerate(texts):
            if not text or not text.strip():
                embeddings.append([0.0] * self.dimension)
                continue

            if self.cache_enabled:
                cache_key = self._cache_key(text)
                cached = self._cache_get(cache_key)
                if cached is not None:
                    embeddings.append(cached)
                    continue

            embeddings.append(None)  # 占位
            uncached_indices.append(i)
            uncached_texts.append(text)

        # 批量处理未缓存的文本
        if uncached_texts:
            total_batches = (len(uncached_texts) + batch_size - 1) // batch_size
            processed_batches = 0

            for i in range(0, len(uncached_texts), batch_size):
                # 🔥 检查是否应该取消
                if cancel_check and cancel_check():
                    logger.info(f"[Embedding] Cancelled at batch {processed_batches + 1}/{total_batches}")
                    raise asyncio.CancelledError("嵌入操作已取消")

                batch = uncached_texts[i:i + batch_size]
                batch_indices = uncached_indices[i:i + batch_size]

                # R3: 并发限制 + 令牌桶控速
                async with self._semaphore:
                    await self._rate_limit_acquire()
                    try:
                        results = await self._provider.embed_texts(batch)

                        for idx, result in zip(batch_indices, results):
                            embeddings[idx] = result.embedding

                            # 存入缓存（内存 + 磁盘）
                            if self.cache_enabled:
                                cache_key = self._cache_key(texts[idx])
                                self._cache_set(cache_key, result.embedding)

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Batch embedding error (batch {processed_batches + 1}/{total_batches}): {e}")
                        # Retry failed batch with exponential backoff
                        batch_retry_max = 3
                        for retry_attempt in range(batch_retry_max):
                            retry_delay = 2.0 * (2 ** retry_attempt)
                            logger.info(f"Batch {processed_batches + 1} retrying in {retry_delay:.0f}s (attempt {retry_attempt + 1}/{batch_retry_max})")
                            await asyncio.sleep(retry_delay)
                            try:
                                await self._rate_limit_acquire()
                                retry_results = await self._provider.embed_texts(batch)
                                for idx, result in zip(batch_indices, retry_results):
                                    embeddings[idx] = result.embedding
                                    if self.cache_enabled:
                                        cache_key = self._cache_key(texts[idx])
                                        self._cache_set(cache_key, result.embedding)
                                logger.info(f"Batch {processed_batches + 1} retry succeeded on attempt {retry_attempt + 1}")
                                break
                            except Exception as retry_e:
                                logger.warning(f"Batch {processed_batches + 1} retry {retry_attempt + 1} failed: {retry_e}")
                                if retry_attempt == batch_retry_max - 1:
                                    logger.error(f"Batch {processed_batches + 1} failed after {batch_retry_max} retries, will use zero vectors")

                processed_batches += 1

                # 🔥 调用进度回调
                if progress_callback:
                    processed_count = min(i + batch_size, len(uncached_texts))
                    try:
                        progress_callback(processed_count, len(uncached_texts))
                    except Exception as e:
                        logger.warning(f"Progress callback error: {e}")

        # 统计失败批次
        failed_count = sum(1 for e in embeddings if e is None)
        if failed_count > 0:
            logger.warning(
                f"⚠️ {failed_count}/{len(embeddings)} embeddings failed, "
                f"replacing with empty vectors"
            )

        # 确保没有 None（失败的用空向量填充，但已有警告日志）
        return [e if e is not None else [0.0] * self.dimension for e in embeddings]
    
    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()
    
    @property
    def cache_size(self) -> int:
        """缓存大小"""
        return len(self._cache)


