"""
Task 1 (B1): 构建产物目录与 minified 文件排除
Task 2 (B2): 单文件分块超时与 chunk 数量上限防护

覆盖 specs/rag-indexing-hardening/spec.md ADDED-R1 / ADDED-R2：
- 按路径段排除前端构建产物目录（static/next/assets、static/console-ui/assets、public 等）
- 单行超长 / 文件超大 → 判定为 minified 产物跳过
- 合法大文件（多行正常源码）不误伤
- 普通源码目录不受影响
- 自定义 exclude_patterns / include_patterns 仍生效
- 单文件分块设置时间上限（FILE_CHUNK_TIMEOUT=20s），超时跳过该文件并记 warning，
  仍计入已处理文件数（进度推进），不中断整体索引
- 每文件 chunk 数量上限（MAX_CHUNKS_PER_FILE=500），超过截断至 500 并记 warning
- 三处调用点（_full_index / _incremental_index / index_files）行为一致
- 正常文件不超时、不截断，行为与改动前一致
"""
import asyncio
import logging
import os

import pytest

from app.services.rag import indexer as indexer_module
from app.services.rag.indexer import (
    CodeIndexer,
    EmbeddingService,
    IndexingProgress,
    InMemoryVectorStore,
)
from app.services.rag.splitter import ChunkType, CodeChunk

# 与 indexer 模块内常量保持一致（见 test_minified_heuristic_constants_defined）
EXPECTED_MAX_SINGLE_LINE_LENGTH = 2000
EXPECTED_MAX_SOURCE_FILE_SIZE = 2 * 1024 * 1024
EXPECTED_FILE_CHUNK_TIMEOUT = 20
EXPECTED_MAX_CHUNKS_PER_FILE = 500


@pytest.fixture
def indexer():
    """构造轻量 CodeIndexer（内存向量存储，禁用 embedding 缓存，不联网）"""
    return CodeIndexer(
        collection_name="test-indexing-hardening",
        embedding_service=EmbeddingService(cache_enabled=False),
        vector_store=InMemoryVectorStore(collection_name="test-indexing-hardening"),
    )


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _rel(result_files, root):
    # 统一为 "/" 分隔，避免 Windows 下 relpath 返回 "\\" 导致断言噪音
    return sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in result_files)


def test_static_next_assets_giant_js_pruned(tmp_path, indexer):
    """含 static/next/assets/ 的巨型压缩 JS → 整棵子树被剪枝，不进入分块循环"""
    root = tmp_path / "project"
    _write(root / "static" / "next" / "assets" / "a.js", "var x=1;" * 50000)
    _write(root / "src" / "main.py", "print('hello')\n")
    result = _rel(indexer._collect_files(str(root), [], None), root)
    assert result == ["src/main.py"]


def test_console_ui_and_public_pruned(tmp_path, indexer):
    """Nacos 风格 static/console-ui/assets 与 public 目录段 → 剪枝"""
    root = tmp_path / "project"
    _write(root / "static" / "console-ui" / "assets" / "app.js", "var x=1;" * 100)
    _write(root / "static" / "js" / "lib.js", "var y=2;\n" * 50)
    _write(root / "public" / "index.js", "var z=3;\n" * 50)
    _write(root / "app" / "main.py", "print('ok')\n")
    result = _rel(indexer._collect_files(str(root), [], None), root)
    assert result == ["app/main.py"]


def test_single_line_too_long_skipped(tmp_path, indexer):
    """单行超长（minified 特征）→ 跳过，不做解析"""
    root = tmp_path / "project"
    long_line = "x" * (EXPECTED_MAX_SINGLE_LINE_LENGTH + 1)
    _write(root / "src" / "min.js", long_line + "\n")
    _write(root / "src" / "normal.py", "print('hello')\n")
    result = _rel(indexer._collect_files(str(root), [], None), root)
    assert result == ["src/normal.py"]


def test_oversized_file_skipped(tmp_path, indexer):
    """文件超过 2MB → 按文件超大启发式跳过（即使多行正常）"""
    root = tmp_path / "project"
    huge = root / "src" / "huge.js"
    huge.parent.mkdir(parents=True, exist_ok=True)
    with open(huge, "w", encoding="utf-8") as f:
        for i in range(EXPECTED_MAX_SOURCE_FILE_SIZE // 60 + 2000):
            f.write("// line %d with some padding text to grow size 0123456789\n" % i)
    assert os.path.getsize(huge) > EXPECTED_MAX_SOURCE_FILE_SIZE
    _write(root / "src" / "normal.py", "print('hi')\n")
    result = _rel(indexer._collect_files(str(root), [], None), root)
    assert result == ["src/normal.py"]


def test_large_multiline_source_retained(tmp_path, indexer):
    """大但正常的源码（多行、每行都很短）→ 不误伤，正常进入分块"""
    root = tmp_path / "project"
    lines = ["def fn%d(): return %d" % (i, i) for i in range(40000)]
    _write(root / "src" / "big.py", "\n".join(lines) + "\n")
    result = _rel(indexer._collect_files(str(root), [], None), root)
    assert result == ["src/big.py"]


def test_normal_source_untouched(tmp_path, indexer):
    """普通源码目录不受影响，正常收集"""
    root = tmp_path / "project"
    _write(root / "app" / "main.py", "def main():\n    pass\n")
    _write(root / "app" / "utils" / "helper.ts", "export const x = 1;\n")
    _write(root / "README.md", "# Title\n")
    result = _rel(indexer._collect_files(str(root), [], None), root)
    assert result == ["README.md", "app/main.py", "app/utils/helper.ts"]


def test_custom_exclude_patterns_still_honored(tmp_path, indexer):
    """自定义 exclude_patterns（任务级排除）仍生效"""
    root = tmp_path / "project"
    _write(root / "src" / "keep.py", "print('keep')\n")
    _write(root / "src" / "generated" / "skip.py", "print('skip')\n")
    _write(root / "src" / "skip_me.py", "print('skip2')\n")
    result = _rel(
        indexer._collect_files(str(root), ["src/generated/*", "skip_me.py"], None), root
    )
    assert result == ["src/keep.py"]


def test_include_patterns_still_honored(tmp_path, indexer):
    """include_patterns（include 过滤）仍生效"""
    root = tmp_path / "project"
    _write(root / "src" / "a.py", "print('a')\n")
    _write(root / "src" / "b.js", "console.log('b');\n")
    result = _rel(indexer._collect_files(str(root), [], ["src/*.py"]), root)
    assert result == ["src/a.py"]


def test_minified_heuristic_constants_defined():
    """模块常量契约：阈值与构建产物目录段集合"""
    assert indexer_module.MAX_SINGLE_LINE_LENGTH == EXPECTED_MAX_SINGLE_LINE_LENGTH
    assert indexer_module.MAX_SOURCE_FILE_SIZE == EXPECTED_MAX_SOURCE_FILE_SIZE
    required_segments = {"static", "assets", "console-ui", "public", "js", "css", "map"}
    assert required_segments.issubset(indexer_module.BUILD_ARTIFACT_DIR_SEGMENTS)


# ---------------------------------------------------------------------------
# Task 2 (B2): 单文件分块超时与 chunk 数量上限防护
# ---------------------------------------------------------------------------


class FakeEmbedding:
    """轻量假嵌入服务：不联网，返回固定维度零向量"""

    provider = "fake"
    model = "fake-model"
    dimension = 8
    base_url = None

    def __init__(self):
        self.cache_enabled = False

    async def embed_batch(
        self,
        texts,
        batch_size=200,
        progress_callback=None,
        cancel_check=None,
        **kwargs,
    ):
        return [[0.0] * self.dimension for _ in texts]


class FakeSplitter:
    """可控假分块器：可指定返回 chunk 数，或对指定文件挂起（模拟超时）"""

    def __init__(self, chunk_count=0, hang_paths=None):
        self.chunk_count = chunk_count
        self.hang_paths = hang_paths or set()
        self.calls = 0

    def _make_chunks(self, content, file_path):
        self.calls += 1
        return [
            CodeChunk(
                id=f"chunk-{file_path}-{i}",
                content=f"content {i}",
                file_path=file_path,
                language="python",
                chunk_type=ChunkType.UNKNOWN,
            )
            for i in range(self.chunk_count)
        ]

    def split_file(self, content, file_path, language=None):
        return self._make_chunks(content, file_path)

    async def split_file_async(self, content, file_path, language=None):
        if any(h in file_path for h in self.hang_paths):
            # 挂起超过被 monkeypatch 的超时值（0.1s），模拟 Tree-sitter 卡死
            await asyncio.sleep(2)
        return self._make_chunks(content, file_path)


def _make_guarded_indexer(splitter):
    return CodeIndexer(
        collection_name="test-file-guard",
        embedding_service=FakeEmbedding(),
        vector_store=InMemoryVectorStore(collection_name="test-file-guard"),
        splitter=splitter,
    )


async def _drive_full(indexer, root, progress=None):
    progress = progress or IndexingProgress()
    async for _p in indexer._full_index(str(root), [], None, progress, None):
        pass
    return progress


async def _drive_incremental(indexer, root, progress=None):
    progress = progress or IndexingProgress()
    async for _p in indexer._incremental_index(str(root), [], None, progress, None):
        pass
    return progress


async def _drive_index_files(indexer, files):
    # index_files 不接受外部 progress（内部自建），需捕获最后一次 yield 的进度对象
    progress = None
    async for _p in indexer.index_files(files):
        progress = _p
    assert progress is not None
    return progress


def test_file_guard_constants_defined():
    """模块常量契约：单文件分块超时上限与每文件 chunk 数量上限"""
    assert indexer_module.FILE_CHUNK_TIMEOUT == EXPECTED_FILE_CHUNK_TIMEOUT
    assert indexer_module.MAX_CHUNKS_PER_FILE == EXPECTED_MAX_CHUNKS_PER_FILE


async def test_full_index_split_timeout_skips_file(monkeypatch, caplog, tmp_path):
    """分块挂起超时 → 跳过该文件、processed_files 仍推进、warning 含文件名"""
    monkeypatch.setattr(indexer_module, "FILE_CHUNK_TIMEOUT", 0.1)
    root = tmp_path / "project"
    _write(root / "src" / "hang.py", "def hang():\n    pass\n")
    _write(root / "src" / "ok.py", "def ok():\n    return 1\n")

    indexer = _make_guarded_indexer(FakeSplitter(chunk_count=1, hang_paths={"hang.py"}))
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        await _drive_full(indexer, root, progress)

    # 两个文件都计入已处理（进度推进），超时文件被跳过、正常文件照常索引
    assert progress.processed_files == 2
    assert progress.added_files == 1
    assert await indexer.vector_store.get_count() == 1
    assert "分块超时" in caplog.text
    assert "hang.py" in caplog.text


async def test_incremental_index_split_timeout_skips_file(monkeypatch, caplog, tmp_path):
    """增量路径分块挂起超时 → 同样跳过并推进进度"""
    monkeypatch.setattr(indexer_module, "FILE_CHUNK_TIMEOUT", 0.1)
    root = tmp_path / "project"
    _write(root / "src" / "hang.py", "def hang():\n    pass\n")
    _write(root / "src" / "ok.py", "def ok():\n    return 1\n")

    indexer = _make_guarded_indexer(FakeSplitter(chunk_count=1, hang_paths={"hang.py"}))
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        await _drive_incremental(indexer, root, progress)

    assert progress.processed_files == 2
    assert progress.added_files == 1
    assert await indexer.vector_store.get_count() == 1
    assert "分块超时" in caplog.text
    assert "hang.py" in caplog.text


async def test_full_index_chunk_cap_truncates(monkeypatch, caplog, tmp_path):
    """单文件分块超过 500 → 截断至 500 并记 warning"""
    monkeypatch.setattr(indexer_module, "MAX_CHUNKS_PER_FILE", 500)
    root = tmp_path / "project"
    _write(root / "src" / "big.py", "def big():\n    pass\n")

    indexer = _make_guarded_indexer(FakeSplitter(chunk_count=600))
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        await _drive_full(indexer, root, progress)

    assert progress.processed_files == 1
    assert progress.added_files == 1
    assert progress.total_chunks == 500
    assert await indexer.vector_store.get_count() == 500
    assert "截断" in caplog.text
    assert "big.py" in caplog.text


async def test_incremental_index_chunk_cap_truncates(monkeypatch, caplog, tmp_path):
    """增量路径单文件分块超过 500 → 截断至 500 并记 warning"""
    monkeypatch.setattr(indexer_module, "MAX_CHUNKS_PER_FILE", 500)
    root = tmp_path / "project"
    _write(root / "src" / "big.py", "def big():\n    pass\n")

    indexer = _make_guarded_indexer(FakeSplitter(chunk_count=600))
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        await _drive_incremental(indexer, root, progress)

    assert progress.processed_files == 1
    assert progress.added_files == 1
    assert progress.total_chunks == 500
    assert await indexer.vector_store.get_count() == 500
    assert "截断" in caplog.text
    assert "big.py" in caplog.text


async def test_index_files_chunk_cap_truncates(monkeypatch, caplog):
    """index_files（同步 split_file 站点）分块超过 500 → 截断至 500 并记 warning"""
    monkeypatch.setattr(indexer_module, "MAX_CHUNKS_PER_FILE", 500)
    indexer = _make_guarded_indexer(FakeSplitter(chunk_count=600))
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        progress = await _drive_index_files(
            indexer, [{"path": "src/big.py", "content": "def big():\n    pass\n"}]
        )

    assert progress.processed_files == 1
    assert progress.added_files == 1
    assert progress.total_chunks == 500
    assert await indexer.vector_store.get_count() == 500
    assert "截断" in caplog.text
    assert "big.py" in caplog.text


async def test_normal_path_no_timeout_no_cap(monkeypatch, caplog, tmp_path):
    """正常文件：不超时、不截断，行为与改动前一致"""
    monkeypatch.setattr(indexer_module, "FILE_CHUNK_TIMEOUT", 0.1)
    root = tmp_path / "project"
    _write(root / "src" / "normal.py", "def normal():\n    pass\n")

    indexer = _make_guarded_indexer(FakeSplitter(chunk_count=3))
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        await _drive_full(indexer, root, progress)

    assert progress.processed_files == 1
    assert progress.added_files == 1
    assert progress.total_chunks == 3
    assert await indexer.vector_store.get_count() == 3
    assert "分块超时" not in caplog.text
    assert "截断" not in caplog.text
