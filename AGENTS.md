# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

蓝鉴（lanjian）是一个面向代码安全审计的本地化平台，采用前后端分离架构。核心能力：项目导入 → 规则审计 → Multi-Agent AI 分析 → Docker 沙箱验证 → 报告导出。

- **后端**: Python 3.11+ / FastAPI / SQLAlchemy 2.0 Async / PostgreSQL 15 / Redis 7
- **前端**: React 18 / TypeScript 5.7 / Vite 5 / Tailwind CSS / Radix UI (shadcn/ui)
- **AI 引擎**: 自研 Multi-Agent 编排（LangGraph 概念参考）+ LiteLLM 多提供商适配 + ChromaDB RAG
- **部署**: Docker Compose（3 个文件：默认/override/生产），Docker Hub 公开镜像

## 常用命令

### 后端（`backend/`）

```bash
uv sync                          # 安装依赖
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000  # 开发服务器
uv run alembic upgrade head      # 运行数据库迁移
uv run alembic revision --autogenerate -m "描述"  # 生成新迁移
uv run pytest                    # 运行所有测试
uv run pytest tests/agent/test_file_search_tool_contract.py -v  # 运行单个测试
uv run ruff check .              # Lint 检查
uv run black --check .           # 格式化检查
uv run mypy app/                 # 类型检查
```

### 前端（`frontend/`）

```bash
pnpm install          # 安装依赖
pnpm dev              # 开发服务器 (:5173)
pnpm build            # 生产构建
pnpm lint             # Biome + tsgo + ast-grep 三层 lint
pnpm type-check       # tsc --noEmit
pnpm format           # Biome 格式化
```

### Docker

```bash
docker compose up -d                              # 默认部署
docker compose -f docker-compose.prod.yml up -d   # 生产部署
docker compose --profile tools up -d              # 附加 Adminer
docker compose up -d db redis                     # 仅启动基础设施（本地开发）
```

## 架构总览

### 后端分层

```
api/v1/endpoints/   ← HTTP 路由层（13 个端点模块，agent_tasks.py 最复杂 ~3600 行）
    ↓
services/agent/     ← Multi-Agent 审计引擎核心（Orchestrator → Recon/Analysis/Verification）
services/llm/       ← LLM 适配器工厂（LiteLLM 统一 + 百度/豆包/MiniMax 原生适配器）
services/rag/       ← ChromaDB RAG 管道（tree-sitter AST 拆分 → Embedding → 语义检索）
services/scanner.py ← 传统 SAST 扫描器
    ↓
models/             ← SQLAlchemy ORM（11 个模型）
core/               ← 配置(Settings)、安全(JWT/密码/Fernet加密)、RBAC(三级角色)、Redis
db/                 ← 异步会话工厂 + 种子数据初始化
```

### Agent 审计引擎架构（`backend/app/services/agent/`）

```
agents/
├── orchestrator.py   ← 编排层：LLM 驱动的 ReAct 循环，决策调度子 Agent
├── recon.py          ← 侦察层：项目结构分析、技术栈识别、入口点发现
├── analysis.py       ← 分析层：深度代码审计、漏洞检测（优先使用 Semgrep/Bandit 等外部工具）
└── verification.py   ← 验证层：Docker 沙箱动态验证、PoC 生成

core/
├── state.py          ← Agent 状态机（8 种状态：created→running→waiting→paused→completed/failed/stopped）
├── coverage.py       ← D1-D10 十维度覆盖率矩阵（注入/认证/授权/反序列化/文件/SSRF/加密/配置/业务逻辑/供应链）
├── registry.py       ← 全局 Agent 注册表（动态 Agent 树）
├── message.py        ← Agent 间消息总线
├── circuit_breaker.py ← LLM 调用熔断器
├── rate_limiter.py   ← 工具/LLM 速率限制
├── cross_round.py    ← 跨轮上下文传递（R1→R2 增量补漏）
├── attack_chain.py   ← 攻击链分析（多漏洞组合风险评估）
├── executor.py       ← 动态 Agent 树执行器（顺序/并行/自适应）
├── graph_controller.py ← Agent 图控制器（停止/消息/统计）
├── retry.py          ← LLM 调用重试策略
├── fallback.py       ← 优雅降级策略
└── validation.py     ← 输入验证
```

**典型执行流程**：
1. `agent_tasks.py` 创建任务 → 初始化 RAG 索引 + 工具集
2. Orchestrator 启动 → Semgrep 预扫描获取热点文件
3. Orchestrator ReAct 循环：LLM 决策 → `dispatch_agent("recon")` → `dispatch_agent("analysis")` → `dispatch_agent("verification")`
4. 覆盖率和沙箱验证门禁拦截 finish（最多 5 次硬拦截 + 安全阀放行）
5. 攻击链分析 → 保存 findings → 报告生成

### LLM 适配器架构（`backend/app/services/llm/`）

```
factory.py         ← LLMFactory：根据 provider 创建适配器
├── litellm_adapter.py ← LiteLLM 统一适配器（覆盖 8/11 提供商：OpenAI/Codex/Gemini/DeepSeek/智谱/月之暗面/Ollama/Qwen）
├── baidu_adapter.py   ← 百度文心原生适配器（OAuth token 认证）
├── minimax_adapter.py ← MiniMax 原生适配器
└── doubao_adapter.py  ← 字节豆包原生适配器
```

配置回退链：用户数据库配置 > 环境变量 `.env` > 平台专属 API Key

### 前端路由架构

```
/login              ← 公开页面
/ (ProtectedRoute)  ← 需要登录
├── /dashboard      ← 仪表盘（安全态势概览）
├── /ai             ← AI 审计控制中心
├── /projects       ← 项目列表
├── /projects/:id   ← 项目详情（隐藏路由）
├── /instant-analysis ← 即时代码分析
├── /audit-tasks    ← 审计任务列表
├── /tasks/:id      ← 任务详情（隐藏路由）
├── /agent-audit/:taskId ← ⭐ Agent 审计核心页面（SSE 流式，隐藏路由）
├── /audit-rules    ← 审计规则管理
├── /prompts        ← 提示词模板管理
├── /admin          ← 系统管理（超管重定向目标）
├── /recycle-bin    ← 回收站
└── /account        ← 账号管理（侧栏底部入口）
```

### 数据模型要点

- **AgentTask**: 11 种状态（pending→initializing→running→...→completed/completed_with_gaps/failed/cancelled/paused），支持暂停恢复
- **AgentFinding**: 7 种状态（confirmed/not_reproducible/needs_context/false_positive/...），含 verification_status 分布
- **AgentEvent**: 18 种事件类型，SSE 实时推送
- **AgentCheckpoint**: 支持手动暂停 + 自动周期检查点（每 5 轮），含完整 resume_state
- **User**: UUID 主键，三级 RBAC（super_admin/admin/user），密码策略（12 位复杂度 + 历史 + 锁定 + 过期）
- **Project**: 支持 repository/zip 两种类型，含项目成员

## 关键设计决策与模式

### 覆盖率门禁系统
- D1-D10 十维度覆盖率矩阵，硬拦截最多 5 次后安全阀放行
- 软覆盖率评估（基于 findings + 文本证据） + 硬覆盖率检查（基于 CoverageMatrix）
- `COMPLETED_WITH_GAPS` 状态标记覆盖率不足的完成

### 暂停与恢复
- Orchestrator 通过 `request_pause()` 请求暂停 → 落 checkpoint → 抛出 `AgentExecutionPaused`
- `export_resume_state()` / `load_resume_state()` 序列化/反序列化完整编排状态
- 恢复时通过 `resume_checkpoint` 参数传入，跳过已完成的迭代

### Token 预算硬门禁
- `config.token_budget` = 60M（Agent 配置），超限优雅降级为 `COMPLETED_WITH_GAPS`
- 每轮 LLM 调用后累加 `_total_tokens`，子 Agent 独立统计 `_sub_agent_total_tokens`

### 前端 SSE 流式连接
- `useResilientStream` Hook：心跳监控（45s 超时）、指数退避重连（1s→30s）、单实例锁
- `useAgentAuditState`：useReducer 集中管理 14 种 Action
- 自适应轮询策略：2s→5s→15s→60s（按时间分阶段）

### 代码规范
- 后端：Python 3.11+，行宽 100，mypy 强制类型注解，ruff + black
- 前端：TypeScript 严格模式，禁止 `as any`，Biome + tsgo + ast-grep 三层 lint
- 数据库：所有模型变更必须配套 Alembic 迁移
- 安全：敏感字段 Fernet 加密存储，沙箱默认无网络，LLM 调用必须经过熔断器

## 重要文件索引

| 文件 | 用途 |
|------|------|
| `backend/AGENTS.md` | 后端详细架构文档 |
| `frontend/AGENTS.md` | 前端详细架构文档 |
| `backend/app/main.py` | FastAPI 入口 + 生命周期 |
| `backend/app/api/v1/endpoints/agent_tasks.py` | Agent 任务 API（最核心端点，~3600 行） |
| `backend/app/services/agent/agents/orchestrator.py` | 编排 Agent（~2500 行，核心编排逻辑） |
| `backend/app/services/agent/agents/base.py` | Agent 基类（LLM 调用/流式/事件发射） |
| `backend/app/services/agent/config.py` | Agent 配置（~80 配置项，环境变量 AGENT_ 前缀） |
| `backend/app/services/agent/core/coverage.py` | D1-D10 覆盖率矩阵 |
| `backend/app/services/llm/factory.py` | LLM 适配器工厂 |
| `backend/app/services/llm/adapters/litellm_adapter.py` | LiteLLM 统一适配器 |
| `backend/app/services/rag/indexer.py` | RAG 代码索引器 |
| `backend/app/core/config.py` | 全局 Settings（80+ 配置项） |
| `backend/app/core/rbac.py` | 三级 RBAC 权限控制 |
| `backend/app/models/agent_task.py` | Agent 任务/事件/发现/检查点模型 |
| `frontend/src/pages/AgentAudit/` | Agent 审计前端核心页面 |
| `frontend/src/shared/api/agentStream.ts` | SSE 流处理客户端 |
| `openspec/specs/audit-engine/spec.md` | 审计引擎规格 |
| `docker-compose.prod.yml` | 生产部署配置 |