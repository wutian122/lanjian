# Backend — lanjian Python FastAPI 后端

Python 3.11+，FastAPI，SQLAlchemy 2.0+ 异步，PostgreSQL 15。

## 目录结构

```
backend/
├── app/
│   ├── main.py                       # FastAPI 入口，CORS，生命周期（检查 Docker/Redis/DB）
│   ├── api/
│   │   ├── deps.py                   # 依赖注入（get_db、get_current_user、JWT 解码）
│   │   ├── middleware.py             # 中间件（X-App-Name 头）
│   │   └── v1/
│   │       ├── api.py                # 路由聚合器（13 个端点模块）
│   │       └── endpoints/
│   │           ├── agent_tasks.py    # ⭐ Agent 审计任务（SSE 流式、RAG 索引、任务生命周期，4803 行）
│   │           ├── auth.py           # 登录、注册、验证码、改密、登出
│   │           ├── users.py          # 用户 CRUD + 状态切换 + 级联删除
│   │           ├── projects.py       # 项目管理 + ZIP + 分支 + 文件树（826 行）
│   │           ├── members.py        # 项目成员管理
│   │           ├── tasks.py          # 传统审计任务
│   │           ├── scan.py           # 即时代码扫描（仓库/ZIP/即时分析）
│   │           ├── config.py         # LLM 运行时配置（加密存储）
│   │           ├── database.py       # 数据库管理（export/import/clear/stats/health）
│   │           ├── prompts.py        # 提示词模板管理（中英文双语）
│   │           ├── rules.py          # 审计规则集（导入导出）
│   │           ├── embedding_config.py # Embedding 模型配置
│   │           └── ssh_keys.py       # SSH 密钥管理（Ed25519/RSA）
│   ├── core/
│   │   ├── config.py                 # Pydantic Settings（80+ 配置项：LLM/DB/JWT/Agent/沙箱/RAG/Feature Flags）
│   │   ├── security.py               # 密码策略（12位复杂度+历史检查+锁定+过期）、JWT 令牌
│   │   ├── encryption.py             # 字段级 Fernet 对称加密（API Key/SSH 私钥等）
│   │   ├── rbac.py                   # 三级 RBAC（super_admin/admin/user）+ 行级数据范围隔离
│   │   └── redis.py                  # Redis 异步连接池（单例，max_connections=20）
│   ├── db/
│   │   ├── session.py                # AsyncSessionLocal 工厂 + get_db 依赖注入
│   │   ├── base.py                   # SQLAlchemy 声明式基类（自动推导表名）
│   │   └── init_db.py                # 首次启动种子数据（超管 + 演示数据 + 模板初始化）
│   ├── models/                       # 9 个 ORM 模型（16 张表）
│   │   ├── user.py                   # 用户（UUID 主键、RBAC 角色、多租户、密码历史、账户锁定）
│   │   ├── project.py                # 项目（repository/zip 两种类型）+ 项目成员
│   │   ├── agent_task.py             # Agent 任务（11 种状态）+ 事件（27 种类型）+ 发现（7 种状态）+ 检查点 + 树节点
│   │   ├── audit.py                  # 传统审计任务 + 审计问题（5 级严重度）
│   │   ├── audit_rule.py             # 审计规则集 + 规则（支持导入导出）
│   │   ├── audit_log.py              # 操作审计日志
│   │   ├── prompt_template.py        # 提示词模板（中英文、系统/用户/分析三种类型）
│   │   ├── user_config.py            # 用户 LLM 配置（JSON 存储）
│   │   └── analysis.py               # 即时分析记录
│   ├── schemas/                      # Pydantic Schema（4 个模块）
│   │   ├── token.py                  # 登录响应 + JWT 载荷
│   │   ├── user.py                   # 用户 CRUD（含密码匹配验证）
│   │   ├── audit_rule.py             # 规则集/规则 CRUD + 导入导出
│   │   └── prompt_template.py        # 提示词模板 CRUD + 测试
│   ├── services/
│   │   ├── agent/                    # ⭐ Multi-Agent 核心引擎（详见 agent/AGENTS.md）
│   │   ├── llm/                      # LLM 适配器工厂
│   │   │   ├── factory.py            # 工厂模式（11 个提供商，LiteLLM 统一 + 3 个原生适配器）
│   │   │   ├── service.py            # LLM 服务层（配置回退链、JSON 修复、流式支持）
│   │   │   ├── base_adapter.py       # 适配器基类
│   │   │   ├── types.py              # 类型定义（LLMProvider/Config/Message/Response）
│   │   │   ├── memory_compressor.py  # 对话记忆压缩（token > 90% 时触发）
│   │   │   ├── prompt_cache.py       # 提示缓存（仅 Claude 支持）
│   │   │   └── adapters/             # 原生适配器
│   │   │       ├── litellm_adapter.py # LiteLLM 统一适配器（覆盖 8/11 提供商）
│   │   │       ├── baidu_adapter.py   # 百度文心（OAuth token 认证）
│   │   │       ├── minimax_adapter.py # MiniMax（特殊错误格式）
│   │   │       └── doubao_adapter.py  # 字节豆包
│   │   ├── rag/                      # ChromaDB RAG 管道
│   │   │   ├── embeddings.py         # Embedding 服务（8 个提供商，429 限流指数退避，批量重试）
│   │   │   ├── indexer.py            # 代码索引器（增量/全量/智能更新，索引版本控制）
│   │   │   ├── retriever.py          # 代码检索器（语义/混合/函数上下文/相似代码检索）
│   │   │   └── splitter.py           # 代码拆分器（tree-sitter AST，16+ 语言，安全模式识别）
│   │   ├── scanner.py                # 传统 SAST 扫描器（GitHub/GitLab/Gitea 仓库）
│   │   ├── report_generator.py       # WeasyPrint PDF 报告生成（A4 企业级审计报告）
│   │   ├── init_templates.py         # 提示词模板和审计规则初始化（4 个模板 + 5 个规则集）
│   │   ├── zip_storage.py            # ZIP 上传存储
│   │   └── git_ssh_service.py        # Git SSH 密钥管理（Ed25519/RSA）
│   └── utils/
│       └── repo_utils.py             # Git 仓库操作
├── alembic/                          # 数据库迁移（24 个迁移文件）
│   └── versions/                     # 001_initial → 023_drop_dead（含一次 merge heads 4c280754c680）
├── tests/                            # 测试（82 个测试文件，575 个用例）
│   ├── agent/                        # Agent 专项测试（62 个：验证/沙箱/覆盖率/严格发现等）
│   ├── rag/                          # RAG 测试（test_indexing_hardening.py：B1-B5 索引进度加固）
│   └── test_*.py                     # 认证/RBAC/攻击链/覆盖率/跨轮次/文件选择测试
├── uploads/                          # 上传文件存储
├── pyproject.toml                    # 依赖 + lint + 测试配置
├── alembic.ini                       # Alembic 配置
├── Dockerfile                        # 后端 Docker 镜像
├── docker-entrypoint.sh              # 容器入口脚本
├── start.sh                          # 启动脚本
├── env.example                       # 环境变量示例
└── AGENTS.md                         # 本文件
```

## API 端点映射

| 前缀 | 模块 | 功能 |
|------|------|------|
| `/api/v1/auth` | auth.py | 登录、注册、验证码、改密、登出 |
| `/api/v1/users` | users.py | 用户 CRUD + 状态切换 |
| `/api/v1/projects` | projects.py | 项目管理 + ZIP + 分支 + 文件树 |
| `/api/v1/projects` | members.py | 项目成员管理 |
| `/api/v1/tasks` | tasks.py | 传统审计任务 + 问题 + PDF 报告 |
| `/api/v1/agent-tasks` | agent_tasks.py | ⭐ Agent 审计（创建/SSE 流/取消/重启/报告） |
| `/api/v1/scan` | scan.py | 即时代码扫描（仓库/ZIP/即时分析） |
| `/api/v1/config` | config.py | LLM 运行时配置（加密存储） |
| `/api/v1/database` | database.py | 数据库连接测试 |
| `/api/v1/prompts` | prompts.py | 提示词模板管理 |
| `/api/v1/rules` | rules.py | 审计规则集管理 |
| `/api/v1/embedding` | embedding_config.py | Embedding 模型配置 |
| `/api/v1/ssh-keys` | ssh_keys.py | SSH 密钥管理 |

## 审计数据流

```
前端 POST /api/v1/agent-tasks/
    │
    ▼agent_tasks.py: _execute_agent_task(task_id)
    ├── 1. 获取项目、用户配置
    ├── 2. 克隆/准备项目代码
    ├── 3. _initialize_tools()
    │     ├── 创建 EmbeddingService
    │     ├── 创建 CodeIndexer → smart_index_directory()
    │     │     └── _index_chunks() → embed_batch(batch_size=200)
    │     ├── 创建 CodeRetriever
    │     └── 返回工具集（recon/analysis/verification/orchestrator）
    ├── 4. 创建子 Agent（Recon/Analysis/Verification）
    ├── 5. 创建 OrchestratorAgent
    ├── 6. orchestrator.run(input_data)
    │     ├── LLM 决策循环（max_iterations=20，orchestrator.py:220 硬编码）
    │     ├── dispatch_agent("recon") → ReconAgent.run()
    │     ├── dispatch_agent("analysis") → AnalysisAgent.run()
    │     ├── dispatch_agent("verification") → VerificationAgent.run()
    │     └── finish → 返回结果
    └── 7. 保存 Findings 到数据库
```

> **v5.3.0 起 RAG 索引经 B1-B5 加固**：构建产物路径段排除 + minified 启发式（`indexer.py` `BUILD_ARTIFACT_DIR_SEGMENTS` / `MAX_SINGLE_LINE_LENGTH=2000` / `MAX_SOURCE_FILE_SIZE=2MB`）、单文件分块护栏（`FILE_CHUNK_TIMEOUT=20s` 跳过 / `MAX_CHUNKS_PER_FILE=500` 截断）、有界并发（`CHUNK_CONCURRENCY=4` 分批 gather）、嵌入快速失败（`embeddings.py` 重试耗尽抛 `EmbeddingUnavailableError`，agent_tasks 走 `rag_unavailable` 分支，禁止写入零向量）、进度消息分阶段标注（`CHUNK_PROGRESS_MSG_TEMPLATE` / `EMBED_PROGRESS_MSG_TEMPLATE`）。测试见 `tests/rag/test_indexing_hardening.py`，烟雾脚本 `smoke_rag_indexing.py`。

> **v5.4.0 验证引擎根治（R1-R7）**：`verification.py` 新增确定性状态引擎 `compute_verification_status`（R1：由 sandbox_attempts 证据推导状态，不信任 LLM 自述 verdict，仅保留 false_positive/sandbox_skip_reason 两个显式标注位）、`_bind_runtime_evidence_to_all`（R2：证据按 finding_id 强制绑定，LLM 漏报不丢证据）、`FABRICATION_MARKERS` 反伪造（R3：Simulated/模拟/源码缺失标记 `fabricated` 并排除出判定与门禁）+ 确定性沙箱执行；`orchestrator.py` 门禁 3 次终止（R4 `verification_max_force_redispatch`）、Bug D 判定修正（R5：needs_context 视为未验证）、observations 记录（R6）、中断收口（R7）。`app/core/timeutil.py` 统一 API/SSE 时间为北京时间（UTC+8）。测试见 `tests/agent/test_verification_evidence.py` 与 `tests/agent/test_orchestrator_gates.py`。

## 数据库迁移演进

| 阶段 | 迁移 | 内容 |
|------|------|------|
| MVP | 001 | users, projects, audit_tasks, audit_issues |
| 可定制化 | 004 | prompt_templates, audit_rule_sets, audit_rules |
| Agent 引擎 | 006-009 | agent_tasks, agent_findings, checkpoints（早期安全控件表，后废弃） |
| 多租户 | 011 | tenants 表 + 所有核心表添加 tenant_id（后废弃，018 已删除） |
| RBAC | 013-015 | 权限字段、用户名、部门 |
| 验证流程 | 016-017 | verification_status, verification_coverage |
| 清理 | 018 | cleanup_dead_schema：删除 tenants 表及 tenant_id 列、projects 死列 |
| 清理 | 023 | drop_dead：删除 security_controls/sensitive_operations/operation_required_controls/language_adapters/coverage_tracks 五张死表 |

> 注：迁移链含一次 merge heads（`4c280754c680`），当前 head=`023_drop_dead`。

## 编码规范

- **行宽**: 100 字符（black + ruff 均配置为 100）
- **Python 版本**: 3.11+（target-version = py311）
- **类型检查**: mypy 强制 `disallow_untyped_defs = true`
- **Lint**: ruff（select: E, W, F, I, B, C4, UP），忽略 E501、B008、C901
- **格式化**: black
- **导入排序**: ruff isort（I 规则）
- **测试**: pytest，asyncio 自动模式
- **数据库**: SQLAlchemy 2.0+ 异步引擎（asyncpg）+ Alembic 迁移

## 反模式

- 禁止跳过类型注解
- 禁止未迁移的模型更改
- 禁止硬编码密钥密码
- 沙箱工具禁止网络访问
- 所有 LLM 调用必须经过 `circuit_breaker`
- 外部静态分析工具为审计核心必用：Orchestrator 含 Semgrep 预扫描，沙箱内 7 种外部工具（Semgrep/Bandit/Gitleaks/TruffleHog/npm audit/Safety/OSV-Scanner）；静态检测三套 = 外部工具 + 内置 OWASP 正则模式库 + DB 中 AuditRuleSet（LLM 提示词规则，非 ast-grep）
- 敏感信息必须通过 Fernet 加密存储

## 常用命令

```bash
uv sync                          # 安装依赖
uvicorn app.main:app --reload    # 开发服务器
alembic upgrade head             # 运行迁移
alembic revision --autogenerate -m "描述"  # 生成新迁移
pytest                           # 运行所有测试
ruff check .                     # Lint
black --check .                  # 格式化检查
mypy app/                        # 类型检查
```