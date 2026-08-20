"""
Task 1 (B1): 构建产物目录与 minified 文件排除
Task 2 (B2): 单文件分块超时与 chunk 数量上限防护
Task 3 (B3): 分块循环有界并发（CHUNK_CONCURRENCY=4）

覆盖 specs/rag-indexing-hardening/spec.md ADDED-R1 / ADDED-R2 / ADDED-R3：
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
- 分块循环有界并发（CHUNK_CONCURRENCY=4）：多文件并行分块，整体耗时明显小于串行
- 单个慢文件不阻塞整批：超时被跳过，其余文件正常入库
- 进度计数单调递增，且与串行语义一致
- 单文件分块异常不中断整批，错误被记录
- 增量路径（files_to_add / files_to_update，含 is_update 先删后加）同样受益
"""
import asyncio
import hashlib
import logging
import os
import time

import pytest

from app.services.rag import indexer as indexer_module
from app.services.rag.embeddings import (
    EmbeddingResult,
    EmbeddingService as EmbeddingServiceImpl,
    EmbeddingUnavailableError,
)
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
EXPECTED_CHUNK_CONCURRENCY = 4


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
    """可控假分块器：可指定返回 chunk 数、对指定文件挂起（模拟超时）、
    对指定文件抛异常，或对每个文件固定 sleep（模拟真实分块耗时，用于并发验证）"""

    def __init__(self, chunk_count=0, hang_paths=None, sleep_per_file=0.0, raise_paths=None):
        self.chunk_count = chunk_count
        self.hang_paths = hang_paths or set()
        self.sleep_per_file = sleep_per_file
        self.raise_paths = raise_paths or set()
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
        if any(h in file_path for h in self.raise_paths):
            # 模拟分块阶段抛异常
            raise RuntimeError(f"boom-{file_path}")
        if any(h in file_path for h in self.hang_paths):
            # 挂起超过被 monkeypatch 的超时值（0.1s），模拟 Tree-sitter 卡死
            await asyncio.sleep(2)
        if self.sleep_per_file:
            # 模拟真实分块耗时（不挂起，超时阈值内正常完成）
            await asyncio.sleep(self.sleep_per_file)
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


# ---------------------------------------------------------------------------
# Task 3 (B3): 分块循环有界并发（CHUNK_CONCURRENCY=4）
# ---------------------------------------------------------------------------


async def _drive_full_seen(indexer, root, progress=None):
    """驱动 _full_index 并记录每次 yield 的 processed_files，用于单调性断言"""
    progress = progress or IndexingProgress()
    seen = []
    async for p in indexer._full_index(str(root), [], None, progress, None):
        seen.append(p.processed_files)
    return progress, seen


async def _seed_store(indexer, entries):
    """向 InMemoryVectorStore 预置旧索引状态：[(relative_path, file_hash, chunk_count), ...]"""
    emb = [0.0] * FakeEmbedding.dimension
    ids, embs, docs, metas = [], [], [], []
    for rel_path, file_hash, n in entries:
        for i in range(n):
            ids.append(f"seed-{rel_path}-{i}")
            embs.append(emb)
            docs.append(f"seed {i}")
            metas.append({"file_path": rel_path, "file_hash": file_hash})
    await indexer.vector_store.add_documents(ids, embs, docs, metas)


def test_chunk_concurrency_constant_defined():
    """模块常量契约：分块有界并发数"""
    assert indexer_module.CHUNK_CONCURRENCY == EXPECTED_CHUNK_CONCURRENCY


async def test_full_index_batched_concurrency_speeds_up(monkeypatch, tmp_path):
    """真实并发验证：4 个文件各 sleep 0.3s，串行约需 1.2s，有界并发下总耗时明显小于 1.0s"""
    monkeypatch.setattr(indexer_module, "FILE_CHUNK_TIMEOUT", 10)  # 放宽超时，仅测并发
    root = tmp_path / "project"
    for i in range(4):
        _write(root / "src" / f"f{i}.py", f"def f{i}():\n    return {i}\n")

    splitter = FakeSplitter(chunk_count=2, sleep_per_file=0.3)
    indexer = _make_guarded_indexer(splitter)

    start = time.monotonic()
    progress, _ = await _drive_full_seen(indexer, root)
    elapsed = time.monotonic() - start

    # 并发为 4：一轮 gather 内 4 个文件并行分块（理论 ~0.3s），串行则需 ~1.2s
    assert elapsed < 1.0, f"elapsed={elapsed:.2f}s 应明显小于串行 ~1.2s（并发未生效则退回串行）"
    assert progress.processed_files == 4
    assert progress.added_files == 4
    assert progress.total_chunks == 8
    assert await indexer.vector_store.get_count() == 8


async def test_full_index_slow_file_bounded_by_timeout(monkeypatch, caplog, tmp_path):
    """慢文件不堵死整批：一个文件挂起超过 FILE_CHUNK_TIMEOUT，其余文件仍正常入库"""
    monkeypatch.setattr(indexer_module, "FILE_CHUNK_TIMEOUT", 0.1)
    root = tmp_path / "project"
    _write(root / "src" / "hang.py", "def hang():\n    pass\n")
    _write(root / "src" / "ok1.py", "def ok1():\n    return 1\n")
    _write(root / "src" / "ok2.py", "def ok2():\n    return 2\n")

    splitter = FakeSplitter(chunk_count=2, hang_paths={"hang.py"})
    indexer = _make_guarded_indexer(splitter)
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        await _drive_full(indexer, root, progress)

    # 慢文件超时被跳过但仍计入已处理，快文件 chunk 全部入库
    assert progress.processed_files == 3
    assert progress.added_files == 2
    assert progress.total_chunks == 4
    assert await indexer.vector_store.get_count() == 4
    assert "分块超时" in caplog.text
    assert "hang.py" in caplog.text


async def test_full_index_progress_monotonic_and_consistent(monkeypatch, tmp_path):
    """进度单调 + 与串行语义一致：混合空/慢/正常文件，processed_files 单调不减，总数与串行一致"""
    monkeypatch.setattr(indexer_module, "FILE_CHUNK_TIMEOUT", 0.1)
    root = tmp_path / "project"
    _write(root / "src" / "empty.py", "   \n  \n")  # 空内容 → 跳过
    _write(root / "src" / "hang.py", "def hang():\n    pass\n")  # 超时 → 跳过但计入已处理
    _write(root / "src" / "ok1.py", "def ok1():\n    return 1\n")
    _write(root / "src" / "ok2.py", "def ok2():\n    return 2\n")

    splitter = FakeSplitter(chunk_count=2, hang_paths={"hang.py"})
    indexer = _make_guarded_indexer(splitter)

    progress, seen = await _drive_full_seen(indexer, root)

    # processed_files 单调不减
    assert seen == sorted(seen), f"processed_files 应单调不减，实际 {seen}"
    # 与串行语义一致：4 文件全部计入已处理，1 空跳过，1 超时跳过，2 正常
    assert progress.processed_files == 4
    assert progress.skipped_files == 1
    assert progress.added_files == 2
    assert progress.total_chunks == 4
    assert await indexer.vector_store.get_count() == 4


async def test_full_index_error_isolation(monkeypatch, caplog, tmp_path):
    """错误隔离：一个文件分块抛异常，整批不中断，其余文件正常入库，errors 记录"""
    root = tmp_path / "project"
    _write(root / "src" / "bad.py", "def bad():\n    pass\n")
    _write(root / "src" / "ok1.py", "def ok1():\n    return 1\n")
    _write(root / "src" / "ok2.py", "def ok2():\n    return 2\n")

    splitter = FakeSplitter(chunk_count=2, raise_paths={"bad.py"})
    indexer = _make_guarded_indexer(splitter)
    progress = IndexingProgress()

    with caplog.at_level(logging.WARNING, logger="app.services.rag.indexer"):
        await _drive_full(indexer, root, progress)

    assert progress.processed_files == 3
    assert progress.added_files == 2
    assert progress.total_chunks == 4
    assert await indexer.vector_store.get_count() == 4
    assert len(progress.errors) == 1
    assert "bad.py" in progress.errors[0]


async def test_incremental_index_concurrency_add_and_update(monkeypatch, tmp_path):
    """增量路径有界并发：新增 + 更新（is_update 先删后加）混合，全部正常入库、进度单调"""
    root = tmp_path / "project"
    _write(root / "src" / "upd.py", "def upd():\n    return 1\n")  # 更新（hash 已变）
    _write(root / "src" / "new.py", "def new():\n    return 2\n")  # 新增
    _write(root / "src" / "stable.py", "def stable():\n    return 3\n")  # 未变，不处理
    # 与 _collect_files/_incremental_index 内部 os.path.relpath 的本地分隔符保持一致
    upd_rel = os.path.relpath(root / "src" / "upd.py", root)
    new_rel = os.path.relpath(root / "src" / "new.py", root)
    stable_rel = os.path.relpath(root / "src" / "stable.py", root)

    splitter = FakeSplitter(chunk_count=2)
    indexer = _make_guarded_indexer(splitter)
    # 预置旧索引：upd.py 旧 hash 与当前内容不一致 → 触发更新；stable.py 旧 hash 一致 → 不处理
    current_upd = hashlib.md5("def upd():\n    return 1\n".encode()).hexdigest()
    current_stable = hashlib.md5("def stable():\n    return 3\n".encode()).hexdigest()
    await _seed_store(
        indexer,
        [
            (upd_rel, "old-hash-differs", 2),
            (stable_rel, current_stable, 2),
        ],
    )

    progress = IndexingProgress()
    seen = []
    async for p in indexer._incremental_index(str(root), [], None, progress, None):
        seen.append(p.processed_files)

    # 增量差异：新增 1 + 更新 1（stable 不变不处理）
    assert progress.total_files == 2
    assert progress.processed_files == 2
    assert progress.added_files == 1  # new.py
    assert progress.updated_files == 1  # upd.py
    assert progress.deleted_files == 0
    assert seen == sorted(seen), f"processed_files 应单调不减，实际 {seen}"
    # upd.py 旧块被删（delete-then-add），最终仅剩：new.py 2 + upd.py 2 + stable.py 2 = 6
    assert await indexer.vector_store.get_count() == 6
    hashes = await indexer.vector_store.get_file_hashes()
    assert hashes[upd_rel] != "old-hash-differs"
    assert hashes[new_rel] == hashlib.md5("def new():\n    return 2\n".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Task 4 (B4): 嵌入失败快速失败，杜绝零向量静默入库
# ---------------------------------------------------------------------------


class _AlwaysFailProvider:
    """永远失败的嵌入提供商：embed_texts 稳定抛异常（模拟嵌入端点持续 400）"""

    dimension = 8

    async def embed_text(self, text):
        raise RuntimeError("embedding endpoint 400 always")

    async def embed_texts(self, texts):
        raise RuntimeError("embedding endpoint 400 always")


class _TransientFailProvider:
    """瞬时抖动提供商：首次调用抛异常，之后成功（模拟 429 抖动后恢复）"""

    dimension = 8

    def __init__(self):
        self.calls = 0

    async def embed_text(self, text):
        results = await self.embed_texts([text])
        return results[0]

    async def embed_texts(self, texts):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient 429 boom")
        return [
            EmbeddingResult(embedding=[0.5] * self.dimension, tokens_used=0, model="fake")
            for _ in texts
        ]


def _make_embedding_service(provider):
    """构造带指定 provider 的 EmbeddingService（无缓存、无限流控速）"""
    svc = EmbeddingServiceImpl(cache_enabled=False)
    svc._provider = provider
    svc._rate_limit = 0
    return svc


async def _no_sleep(_):
    """把 embed_batch 的重试退避 sleep 加速为 no-op，避免测试等待 2+4+8 秒"""
    return None


async def test_embed_batch_raises_embedding_unavailable_no_zero_vectors(monkeypatch):
    """嵌入批次重试耗尽 → 抛 EmbeddingUnavailableError，绝不返回零向量"""
    monkeypatch.setattr("app.services.rag.embeddings.asyncio.sleep", _no_sleep)

    svc = _make_embedding_service(_AlwaysFailProvider())
    with pytest.raises(EmbeddingUnavailableError) as exc_info:
        await svc.embed_batch(["hello world", "def f():\n    pass"])
    # 异常信息应携带批次序号、重试次数与最后一次失败原因
    msg = str(exc_info.value)
    assert "嵌入批次" in msg
    assert "重试" in msg
    assert "400" in msg


async def test_embed_batch_transient_retry_succeeds(monkeypatch):
    """瞬时抖动：首次失败、重试成功 → 正常返回向量，不抛异常"""
    monkeypatch.setattr("app.services.rag.embeddings.asyncio.sleep", _no_sleep)

    provider = _TransientFailProvider()
    svc = _make_embedding_service(provider)

    result = await svc.embed_batch(["a", "b"])
    assert provider.calls == 2  # 首次失败 + 一次重试成功
    assert len(result) == 2
    assert all(len(v) == 8 and v[0] == 0.5 for v in result)


class _FailingFakeEmbedding:
    """wave4: 嵌入不可用的假嵌入服务（embed_batch 直接抛 EmbeddingUnavailableError）"""

    provider = "fake"
    model = "fake-model"
    dimension = 8
    base_url = None
    cache_enabled = False

    async def embed_batch(
        self,
        texts,
        batch_size=200,
        progress_callback=None,
        cancel_check=None,
        **kwargs,
    ):
        raise EmbeddingUnavailableError("嵌入端点 400，RAG 不可用")


def _make_failing_embedding_indexer(splitter=None):
    return CodeIndexer(
        collection_name="test-embed-fail",
        embedding_service=_FailingFakeEmbedding(),
        vector_store=InMemoryVectorStore(collection_name="test-embed-fail"),
        splitter=splitter or FakeSplitter(chunk_count=2),
    )


async def test_full_index_propagates_embedding_unavailable_no_write(tmp_path):
    """_full_index：嵌入不可用 → 异常向上传播，vector_store 计数保持 0（无零向量写入）"""
    root = tmp_path / "project"
    _write(root / "src" / "a.py", "def a():\n    return 1\n")

    indexer = _make_failing_embedding_indexer()
    with pytest.raises(EmbeddingUnavailableError):
        await _drive_full(indexer, root)
    assert await indexer.vector_store.get_count() == 0


async def test_smart_index_directory_propagates_embedding_unavailable_no_write(tmp_path):
    """smart_index_directory（agent_tasks 入口）：嵌入不可用 → 异常向上传播、无写入"""
    root = tmp_path / "project"
    _write(root / "src" / "a.py", "def a():\n    return 1\n")

    indexer = _make_failing_embedding_indexer()
    with pytest.raises(EmbeddingUnavailableError):
        async for _p in indexer.smart_index_directory(str(root), [], None):
            pass
    assert await indexer.vector_store.get_count() == 0


# ===========================================================================
# Task 5 (B5): 进度消息分阶段标识（分块 vs 嵌入）
# 覆盖 specs/rag-indexing-hardening/spec.md MODIFIED-R5：
# - 分块阶段进度消息明确标识"分块"，嵌入阶段使用独立的"嵌入"标识，两阶段可区分。
# - 文档契约型断言：模板常量同时是 SSE 消息的实际来源（agent_tasks.py 内 emit 使用），
#   因此直接断言模板内容即锁定了用户可见消息的阶段标识。
# ===========================================================================


def _progress_templates():
    """延迟导入 agent_tasks 常量，避免拖慢本模块收集阶段（模块导入较重）。"""
    from app.api.v1.endpoints.agent_tasks import (
        CHUNK_PROGRESS_MSG_TEMPLATE,
        EMBED_PROGRESS_MSG_TEMPLATE,
    )

    return CHUNK_PROGRESS_MSG_TEMPLATE, EMBED_PROGRESS_MSG_TEMPLATE


def test_chunk_progress_template_marks_chunk_phase():
    """文档契约：分块模板含"分块"与"文件"标识，且与嵌入模板内容不同。"""
    chunk_tpl, embed_tpl = _progress_templates()
    assert "分块" in chunk_tpl
    assert "文件" in chunk_tpl
    assert chunk_tpl != embed_tpl


def test_embed_progress_template_marks_embed_phase():
    """文档契约：嵌入模板含"嵌入"标识，且不含"分块"（两阶段标识互斥）。"""
    chunk_tpl, embed_tpl = _progress_templates()
    assert "嵌入" in embed_tpl
    assert "分块" not in embed_tpl


def test_progress_templates_render_phase_labels():
    """文档契约：模板 .format 渲染后，样本消息含对应阶段标识（用户实际看到的文案）。"""
    chunk_tpl, embed_tpl = _progress_templates()
    chunk_msg = chunk_tpl.format(processed=5, total=10, pct=50.0)
    embed_msg = embed_tpl.format(processed=5, total=10, pct=50.0)
    assert "分块" in chunk_msg
    assert "嵌入" in embed_msg
    assert "分块" not in embed_msg
