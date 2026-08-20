"""
Task 1 (B1): 构建产物目录与 minified 文件排除

覆盖 specs/rag-indexing-hardening/spec.md ADDED-R1：
- 按路径段排除前端构建产物目录（static/next/assets、static/console-ui/assets、public 等）
- 单行超长 / 文件超大 → 判定为 minified 产物跳过
- 合法大文件（多行正常源码）不误伤
- 普通源码目录不受影响
- 自定义 exclude_patterns / include_patterns 仍生效
"""
import os

import pytest

from app.services.rag import indexer as indexer_module
from app.services.rag.indexer import CodeIndexer, EmbeddingService, InMemoryVectorStore

# 与 indexer 模块内常量保持一致（见 test_minified_heuristic_constants_defined）
EXPECTED_MAX_SINGLE_LINE_LENGTH = 2000
EXPECTED_MAX_SOURCE_FILE_SIZE = 2 * 1024 * 1024


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
