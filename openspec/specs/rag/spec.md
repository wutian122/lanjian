# rag Specification

## Purpose
TBD - created by archiving change fix-audit-data-rag-cost. Update Purpose after archive.
## Requirements
### Requirement: RAG 工具参数健壮性

`RAGQueryTool._execute` 的 `query` 参数 SHALL 有默认值（None）并在函数内判空，缺失时返回 `ToolResult(success=False, error=...)`，不得设为必填位置参数导致抛出 `TypeError: missing 1 required positional argument`。`AgentTool.execute` 入口 SHALL 对 `args_schema` 必填字段做校验，缺失时返回结构化错误而非抛出未捕获异常。

#### Scenario: 缺 query 返回结构化错误
- **WHEN** LLM 调用 `rag_query` 但未传入 `query` 参数
- **THEN** 工具返回 `ToolResult(success=False, error="query 参数必填")`，不抛出 `TypeError`

#### Scenario: 任意工具缺必填参数不抛 TypeError
- **WHEN** 任意继承 `AgentTool` 的工具被调用时缺失 `args_schema` 声明的必填字段
- **THEN** `base.py execute` 返回结构化错误 ToolResult，不抛出未捕获的 `TypeError`

### Requirement: collection 元数据更新不触发 distance 告警

`ChromaVectorStore.update_collection_metadata` 调用 `collection.modify(metadata=...)` 前 SHALL 从 metadata 字典中剥离 `hnsw:space` 键（该键 collection 创建后不可变），不得将其传入 modify 触发 `Changing the distance function of a collection once it is created is not supported currently` 告警。

#### Scenario: 元数据更新无 distance 告警
- **WHEN** 索引完成后调用 update_collection_metadata 更新 project_hash/file_count
- **THEN** 不出现 "Changing the distance function" 告警，metadata 其余字段正常更新

### Requirement: RAG 检索事件入审计链

`rag_query` 工具 SHALL 通过标准 `tool_call`/`tool_result` 事件流落库（与 `read_file` 等工具一致），`tool_input` 含 `query`/`top_k`/`file_path`/`language`，`tool_result` 含召回结果摘要，不得仅内嵌在 `llm_observation` 文本中导致 `tool_name` 为空、`tool_input` 为 null。

#### Scenario: 检索事件入审计链
- **WHEN** agent 调用 rag_query 执行检索
- **THEN** agent_events 表存在 event_type=tool_call 且 tool_name=rag_query 的记录，tool_input JSON 含 query 字段

### Requirement: embedding 限流与缓存

`embeddings.py` 的 `embed_batch` SHALL 通过 `asyncio.Semaphore` 限制并发（默认 4）并使用令牌桶控速（默认 5 req/s，可配置），避免 siliconflow API 频繁 429。SHALL 实现持久化 embedding 缓存（key = `sha256(text)` + embedding_model 前缀），缓存命中时跳过 API 调用；429 退避重试不得耗尽后静默丢文件。

#### Scenario: 缓存命中跳过 API
- **WHEN** 同一 text + 同一 embedding_model 二次索引
- **THEN** 命中缓存跳过 embedding API 调用，返回缓存向量

#### Scenario: 429 限流退避不耗尽
- **WHEN** embedding API 持续返回 429
- **THEN** 按指数退避重试，重试次数内成功则继续，失败文件记录到索引失败表供下次重试，不静默丢文件

### Requirement: 生产容器无热重载

生产环境 backend 容器 SHALL 不以 `--reload` 模式启动 uvicorn，避免任务运行中代码被热重载导致行为不可复现。`docker-entrypoint.sh` SHALL 根据 `ENVIRONMENT` 环境变量决定是否加 `--reload`（仅开发环境加）。

#### Scenario: 生产无 --reload
- **WHEN** ENVIRONMENT=prod 启动 backend 容器
- **THEN** uvicorn 启动命令不含 `--reload`，运行日志无 "WatchFiles detected changes ... Reloading"

### Requirement: orchestrator 代码单一真相源

`backend/app/services/agent/agents/` 下 SHALL 仅保留当前生效的 orchestrator 主文件，`orchestrator.py.bak`/`orchestrator_server.py`/`orchestrator_utf8.py` 等副本 SHALL 归档到 `archive/` 目录，不得并存导致维护时改错文件。

#### Scenario: 仅保留生效 orchestrator 文件
- **WHEN** 检查 agents 目录
- **THEN** 仅 orchestrator.py 在主目录，其余副本在 archive/ 下，且无任何代码 import 归档副本

