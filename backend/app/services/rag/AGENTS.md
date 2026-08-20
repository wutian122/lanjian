# RAG 管道 — 代码索引与语义检索

5 文件、~3,775 行。tree-sitter AST 分块 → 7 个 Embedding 提供商 → ChromaDB 向量存储 → 语义检索。
支持智能/增量/全量三种索引模式。

## 目录结构

```
rag/
├── __init__.py       # 模块边界（35 行），导出 9 个公共符号
├── splitter.py       # 代码拆分（850 行）：tree-sitter AST + 正则回退 + 行级回退
├── embeddings.py     # 嵌入服务：7 个提供商 + 工厂 + 缓存 + 重试（耗尽后抛 EmbeddingUnavailableError 快速失败，杜绝零向量静默入库）
├── indexer.py        # 代码索引（1453 行）：智能/增量/全量 + ChromaVectorStore + InMemoryVectorStore
└── retriever.py      # 代码检索（588 行）：语义/混合/函数上下文/相似代码
```

## 数据流

```
项目代码文件
    │
    ▼
CodeIndexer.smart_index_directory(mode=SMART)
    │  检测是否需要重建（provider/model/dimension/version 变更 → 自动全量）
    ├── [FULL] _full_index
    │     ├── _collect_files（EXCLUDE_DIRS 精确匹配 + 构建产物目录段剪枝（static/assets/console-ui 等）+ minified 启发式（单行>2000/文件>2MB）+ 29 种文本扩展名白名单）
    │     ├── 每文件 → CodeSplitter.split_file_async（有界并发 4 分块；单文件 20s 超时跳过 / chunk 超 500 截断）
    │     │     ├── tree-sitter AST 解析（21 语言）
    │     │     ├── 正则回退（6 语言模式）
    │     │     └── 行级回退
    │     │     → List[CodeChunk]（含 security_indicators/imports/calls）
    │     └── _index_chunks → EmbeddingService.embed_batch(batch=200)
    │           → ChromaVectorStore.add_documents(batch=500)
    │
    ├── [INCREMENTAL] _incremental_index
    │     └── MD5 file_hash diff → add/update/delete
    │
    └── 更新 collection 元数据

查询时：
CodeRetriever.retrieve(query, top_k=10)
    → EmbeddingService.embed(query)
    → ChromaVectorStore.query(n_results=top_k*2)
    → 距离→相似度（1 - distance）
    → List[RetrievalResult]
```

## 关键类

| 类 | 文件 | 行 | 职责 |
|----|------|:--:|------|
| `EmbeddingProvider` (ABC) | embeddings.py | 28 | 抽象基类 |
| `EmbeddingService` | embeddings.py | 595 | 统一入口，SHA256 缓存，批量嵌入 |
| `CodeSplitter` | splitter.py | 329 | 三级分块（AST→正则→行级） |
| `TreeSitterParser` | splitter.py | 138 | 21 语言 tree-sitter 解析 |
| `CodeIndexer` | indexer.py | 679 | 核心索引器（3 种模式） |
| `ChromaVectorStore` | indexer.py | 197 | ChromaDB 持久化（cosine 距离） |
| `InMemoryVectorStore` | indexer.py | 525 | 测试回退 |
| `CodeRetriever` | retriever.py | 75 | 5 种检索方法 |
| `RetrievalResult` | retriever.py | 20 | 含 score + security_indicators |

## 7 个 Embedding 提供商

| 提供商 | 类名 | 默认模型 | 429 重试 |
|--------|------|---------|:--------:|
| OpenAI | `OpenAIEmbedding` | text-embedding-3-small | 5 次（base 1s, max 30s） |
| Azure OpenAI | `AzureOpenAIEmbedding` | text-embedding-3-small | ✅ |
| Ollama | `OllamaEmbedding` | — | ✅ |
| Cohere | `CohereEmbedding` | embed-english-v3.0 | ✅ |
| HuggingFace | `HuggingFaceEmbedding` | — | ✅ |
| Jina | `JinaEmbedding` | jina-embeddings-v2-base-code | ✅ |
| Qwen (DashScope) | `QwenEmbedding` | text-embedding-v3 | ✅（含 Ollama） |

## 索引模式

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| `SMART`（默认） | 每次 | 检测 embedding 配置变更 → 自动全量；否则 → 增量 |
| `FULL` | 显式调用 / 配置变更 | 清空 collection → 全量重建 |
| `INCREMENTAL` | 显式调用 | MD5 hash diff → 增删改 |

## 关键配置

| 配置项 | 值 | 位置 |
|--------|-----|------|
| embed batch_size | **200** | indexer.py |
| Chroma add/upsert batch | **500** | indexer.py |
| 429 重试次数 | **5**（base 1s, max 30s） | embeddings.py |
| 批次失败重试 | **3** 次（2×2^n s） | embeddings.py |
| 文本截断 | **8191** 字符 | embeddings.py |
| 文件大小上限 | **500,000** 字符 | indexer.py |
| 索引版本 | **2.0** | indexer.py |
| tree-sitter 语言 | **21** 种 | splitter.py |
| 安全模式语言 | **5**（py/js/java/go/php） | splitter.py |
| HTTP 超时 | **120s** | embeddings.py |
| 距离度量 | **cosine**（hnsw:space: cosine） | indexer.py |
| 批次间隔 | **0.3s**（Ollama 豁免） | embeddings.py |
| Token 估算 | tiktoken cl100k_base / len÷4 | splitter.py |

## 消费者

- `agent_tasks.py:_initialize_tools()` — 创建 EmbeddingService + CodeIndexer + CodeRetriever
- Agent 工具：`RAGQueryTool`、`SecurityCodeSearchTool`、`FunctionContextTool`
- embedding_config API（`/api/v1/embedding`）— 管理 Embedding 配置

## 反模式

- 禁止直接操作 ChromaDB collection（必须通过 ChromaVectorStore 接口）
- 禁止跳过智能模式决策（配置变更时会导致 embedding 维度不匹配）
- 新语言支持 → tree-sitter parser 添加（splitter.py）+ 安全模式添加（splitter.py）
- indexer 的 `INCREMENTAL` 模式下 `_index_chunks` 可能存在逻辑问题（all_chunks 非空时未被调用）— 已知代码异味

## 已知问题

1. **indexer.py 潜在的增量模式 Bug**（line ~1138）：`_index_chunks` 在增量模式下的调用条件可能不会触发，需要审查。
2. **CodeSplitter 的 tree-sitter 依赖 tree_sitter_language_pack** — 确保 uv sync 安装该依赖（非 tree-sitter-languages 旧包）。
3. **embedding 配置变更检测**依赖 collection 元数据中的 provider/model/dimension — 如果旧 collection 无元数据，会 fallback 到从向量维度推断。
