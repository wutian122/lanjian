"""fix-rag-indexing-2026-08 wave6 冒烟：巨型 minified JS 项目 + 嵌入不可用场景。
验证：构建产物排除、分块快速完成（<60s）、嵌入失败快速失败（无零向量、异常传播）。
路径安全：所有写入经 pathlib resolve() 规范化并用 is_relative_to 校验位于项目根内。
"""
import asyncio
import tempfile
import time
from pathlib import Path

from app.services.rag.embeddings import EmbeddingUnavailableError
from app.services.rag.indexer import CodeIndexer, InMemoryVectorStore
from app.services.rag.splitter import ChunkType, CodeChunk


class UnavailableEmbedding:
    """模拟嵌入端点不可用（重试耗尽后 EmbeddingService 抛 EmbeddingUnavailableError）。"""

    async def embed_batch(self, texts, batch_size=200, progress_callback=None, cancel_check=None, **kw):
        raise EmbeddingUnavailableError("模拟: 嵌入端点返回 400 (模型不存在)")


class FakeSplitter:
    """冒烟用假分块器：返回固定 1 个 chunk（环境无法从 github 下载 tree-sitter parser，
    真实分块在此不可行；排除逻辑已用真实 _collect_files 单独验证）。"""

    async def split_file_async(self, content, file_path, language=None):
        return [CodeChunk(
            id=f"smoke-{file_path}",
            content=content[:2000],
            file_path=file_path,
            language="python",
            chunk_type=ChunkType.FILE,
        )]

    def split_file(self, content, file_path, language=None):
        return [  # 同步兜底：index_files 路径不参与冒烟，返回空
        ]


def _write_safe(root: Path, rel: str, content: str):
    """在 root 内安全写文件：resolve 规范化 + is_relative_to 包含校验，禁止目录逃逸。"""
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"路径逃逸拒绝: {rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _build_project(root: Path):
    """构造含巨型 minified JS 的目录（模拟 Nacos static/next/assets）。"""
    _write_safe(root, "src/main.py", 'def main():\n    return "hello"\n')
    _write_safe(root, "static/next/assets/ts.worker-BH9nVgjN.js", "var a=1;" * 2_000_000)


async def main():
    root = Path(tempfile.mkdtemp(prefix="lanjian_smoke_"))
    _build_project(root)

    indexer = CodeIndexer(
        collection_name="smoke",
        embedding_service=UnavailableEmbedding(),
        vector_store=InMemoryVectorStore(collection_name="smoke"),
        splitter=FakeSplitter(),
    )

    # 1) 收集阶段排除验证
    files = indexer._collect_files(str(root), [], None)
    rel = sorted(Path(p).relative_to(root).as_posix() for p in files)
    assert "src/main.py" in rel, f"正常源码应保留, got {rel}"
    assert not any("static" in p or "js" in p for p in rel), f"构建产物应被排除, got {rel}"
    print(f"[OK] 收集排除: 保留 {len(files)} 个文件: {rel}")

    # 2) 全量索引冒烟：应快速完成（分块阶段不卡死在巨型 JS 上），嵌入不可用快速失败
    t0 = time.time()
    timed_out = False
    fast_failed = False
    progress_seen = 0
    try:
        async for p in indexer.smart_index_directory(str(root), [], None):
            progress_seen = p.processed_files
    except EmbeddingUnavailableError as e:
        fast_failed = True
        print(f"[OK] 嵌入不可用快速失败（{time.time()-t0:.1f}s）: {str(e)[:80]}...")
    except TimeoutError:
        timed_out = True

    elapsed = time.time() - t0
    assert not timed_out, "索引不应超时"
    assert fast_failed, "嵌入端点不可用应快速失败而非写零向量"
    assert elapsed < 60, f"应在时限内完成, took {elapsed:.1f}s"
    assert await indexer.vector_store.get_count() == 0, "不得写入零向量"
    print(f"[OK] 冒烟通过: 进度推进到 {progress_seen} 文件, 耗时 {elapsed:.1f}s, 零向量入库=0")


if __name__ == "__main__":
    asyncio.run(main())
    print("SMOKE PASS")
