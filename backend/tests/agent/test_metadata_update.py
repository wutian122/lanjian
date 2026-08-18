"""
R2 修复测试：collection 元数据更新不触发 distance 告警

根因：update_collection_metadata 把含 hnsw:space 的 metadata 字典传给
      collection.modify()，Chroma 拒绝（hnsw:space 创建后不可变）触发告警。
修复：modify 前剥离 hnsw:space 键。
"""
import pytest
from unittest.mock import MagicMock, patch
from app.services.rag.indexer import ChromaVectorStore


def _make_vector_store_with_collection(existing_metadata):
    vs = ChromaVectorStore.__new__(ChromaVectorStore)
    collection = MagicMock()
    collection.metadata = existing_metadata
    collection.modify = MagicMock()
    vs._collection = collection
    return vs, collection


class TestMetadataUpdateNoHnswWarning:
    """验证元数据更新剥离 hnsw:space"""

    @pytest.mark.asyncio
    async def test_metadata_update_no_warning(self):
        """R2: modify 时不传 hnsw:space"""
        existing = {"hnsw:space": "cosine", "embedding_model": "bge-m3", "created_at": 123.0}
        vs, collection = _make_vector_store_with_collection(existing)

        await vs.update_collection_metadata({"project_hash": "abc", "file_count": 100})

        collection.modify.assert_called_once()
        call_kwargs = collection.modify.call_args.kwargs
        passed_metadata = call_kwargs["metadata"]
        # hnsw:space 必须不在传给 modify 的 metadata 中
        assert "hnsw:space" not in passed_metadata, "modify 不应接收 hnsw:space（会触发 distance 告警）"
        # 其他字段正常更新
        assert passed_metadata["project_hash"] == "abc"
        assert passed_metadata["file_count"] == 100
        assert passed_metadata["embedding_model"] == "bge-m3"
        assert "updated_at" in passed_metadata

    @pytest.mark.asyncio
    async def test_metadata_update_without_existing_hnsw(self):
        """R2: 现有 metadata 无 hnsw:space 时正常更新"""
        vs, collection = _make_vector_store_with_collection({"embedding_model": "bge-m3"})

        await vs.update_collection_metadata({"project_hash": "xyz"})

        passed_metadata = collection.modify.call_args.kwargs["metadata"]
        assert "hnsw:space" not in passed_metadata
        assert passed_metadata["project_hash"] == "xyz"
