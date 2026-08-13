"""
扫描相关的公共常量（单一真相源）

统一管理仓库扫描 / ZIP 扫描 / RAG 索引使用的文本文件扩展名集合，
避免多份拷贝漂移。此前 scanner.py / scan.py / rag/indexer.py 各有一份：
- scanner.py 与 scan.py 为 23 种（缺 .html/.vue/.svelte/.xml/.css/.md）
- rag/indexer.py 为 29 种
导致同一项目"仓库/ZIP 扫描"与"RAG 检索"覆盖面不一致。
此处取两处并集 —— 补齐 web/标记类文件（.html/.vue/.svelte 是前端 XSS 高发区，
.xml 含 XXE / 配置泄露面，.md 常含敏感笔记），三方统一引用。
"""
from typing import FrozenSet

# 支持的文本文件扩展名（含点、小写）
TEXT_EXTENSIONS: FrozenSet[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".cc", ".hh", ".cs", ".php", ".rb",
    ".kt", ".swift", ".sql", ".sh", ".json", ".yml", ".yaml",
    ".xml", ".html", ".css", ".vue", ".svelte", ".md",
})
