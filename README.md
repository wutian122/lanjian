# 蓝鉴（lanjian）

> **AI 驱动的本地化代码安全审计平台**  —— 项目导入 → 规则审计 → Multi-Agent AI 分析 → Docker 沙箱验证 → 报告导出

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-v5.3.0-brightgreen.svg)](https://github.com/wutian122/lanjian/releases)
[![Docker Hub](https://img.shields.io/badge/docker-hub-2496ED?logo=docker)](https://hub.docker.com/u/wutian449)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Node 18+](https://img.shields.io/badge/node-18+-green.svg)](https://nodejs.org/)

## 核心功能

- **项目导入**：Git 仓库克隆或 ZIP 上传，支持多语言项目
- **AI 审计引擎**：LLM 驱动的 Multi-Agent 编排（Recon / Analysis / Verification）
- **审计记忆系统**：跨任务漏洞记忆 —— 新审计任务自动加载同项目历史已确认漏洞（类型 / 位置 / 验证状态），作为复查线索注入 Agent 上下文，聚焦历史问题点
- **外部工具集成**：Semgrep、Bandit、Gitleaks、npm audit、Safety、TruffleHog、OSV Scanner
- **RAG 语义检索**：tree-sitter AST 拆分 + ChromaDB 向量存储 + 7 提供商 embedding 适配
- **Docker 沙箱验证**：动态 PoC 生成 + 多语言执行环境（PHP/Python/JS/Java/Go/Ruby/Shell）
- **实时 SSE 流**：断线重连 + 心跳监控 + Last-Event-ID 语义
- **暂停/恢复**：任务级 checkpoint + 自动周期检查点
- **报告导出**：Markdown / PDF / JSON 多格式导出

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 Async · PostgreSQL 15 · Redis 7 |
| 前端 | React 18 · TypeScript 5.7 · Vite 5 · Tailwind CSS · Radix UI (shadcn/ui) |
| AI | LiteLLM 多提供商适配 · ChromaDB RAG · tree-sitter-language-pack |
| 部署 | Docker Compose 3 文件（默认 / override / prod） |

## 目录结构

```
lanjian/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/endpoints/  # 13 个 API 端点模块
│   │   ├── core/              # 配置、安全、RBAC、Redis
│   │   ├── db/                # 异步会话工厂 + seed
│   │   ├── models/            # 10 个 SQLAlchemy ORM
│   │   ├── schemas/           # Pydantic schemas
│   │   ├── services/
│   │   │   ├── agent/         # Multi-Agent 审计引擎（orchestrator / recon / analysis / verification）
│   │   │   ├── llm/           # LLM 适配器工厂（LiteLLM + 百度/豆包/MiniMax 原生）
│   │   │   ├── rag/           # ChromaDB RAG 管道
│   │   │   └── scanner.py     # 传统 SAST 扫描器
│   │   └── main.py            # FastAPI 入口
│   ├── alembic/               # 数据库迁移（001 → 023）
│   ├── tests/                 # 60+ 个 pytest 用例
│   └── pyproject.toml
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── app/               # 应用入口与路由
│   │   ├── pages/             # 15 个业务页面
│   │   ├── components/
│   │   │   ├── ui/            # shadcn/ui 组件（37 个）
│   │   │   ├── agent/, audit/, reports/, database/, system/, layout/, common/
│   │   ├── features/          # 领域服务（analysis / projects / reports）
│   │   └── shared/            # api / config / hooks / utils
│   ├── scripts/
│   ├── nginx.conf
│   └── package.json
├── docker/sandbox/             # 沙箱镜像构建（Dockerfile + seccomp）
├── docker-compose.yml         # 默认部署
├── docker-compose.override.yml # 开发热更覆盖
├── docker-compose.prod.yml    # 生产部署
├── openspec/                  # 规格驱动开发（changes/ + specs/）
├── docs/                      # 架构文档 + 历史交付日志
├── e2e/                       # Playwright E2E 测试
├── rules/                     # ast-grep 静态规则
└── README.md
```

## 环境依赖

- **Docker & Docker Compose v2**（推荐部署方式）
- **Python 3.11+** + [uv](https://docs.astral.sh/uv/)（本地后端开发）
- **Node 18+** + [pnpm 9+](https://pnpm.io/)（本地前端开发）
- **PostgreSQL 15** + **Redis 7**（可用 docker compose 起）
- **外部工具**（可选，在容器内已预装）：semgrep、bandit、gitleaks、safety、trufflehog、osv-scanner

## 快速开始

### 方式 A：Docker Compose 一键启动（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/wutian122/lanjian.git
cd lanjian

# 2. 准备后端环境变量
cp backend/env.example backend/.env
# ⚠️ 必须设置以下 4 个强变量，否则后端拒绝启动（P0-1/P0-3/P0-4/P3-1 强制注入）：
#   SECRET_KEY           ≥32 位，用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成
#   CORS_ALLOWED_ORIGINS 逗号分隔的前端域名（如 http://192.168.1.10）
#   SUPERADMIN_PASSWORD  ≥12 位、大小写+数字+特殊字符
#   POSTGRES_PASSWORD    ≥12 位、非弱值黑名单

# 3. 拉取 Docker Hub 上的最新镜像并启动全部服务
docker compose pull
docker compose up -d

# 4. 访问
#    前端: http://localhost
#    后端 API 文档: http://localhost:8000/docs
```

**镜像版本控制**：`docker-compose.yml` 中的 `image` 显式锁版本到 `v5.3.0`（生产已禁用 `:latest` 浮动 tag）。`v5.3.0` 为 **多架构镜像**（`linux/amd64` + `linux/arm64`），`docker compose pull` 自动匹配宿主机架构，无需手动指定架构。

锁定到具体版本：

```bash
IMAGE_TAG=v5.3.0 docker compose pull
IMAGE_TAG=v5.3.0 docker compose up -d
```

可选：在部署机根目录建 `.env` 永久锁版本 —— `echo "IMAGE_TAG=v5.3.0" > .env`。

> **镜像说明**：backend / frontend / sandbox 三镜像均发布多架构清单（manifest list）。前端镜像基于 `nginx:1.31.2-alpine`（锁定版本，兼容旧内核如 CentOS 7 / 3.10，避免浮动 tag 重建引入不兼容）。

**Docker Hub 镜像仓库**（组织 [`wutian449`](https://hub.docker.com/u/wutian449)）：

| 镜像 | Docker Hub |
|------|-----------|
| 后端 | [`wutian449/lanjian-backend`](https://hub.docker.com/r/wutian449/lanjian-backend) |
| 前端 | [`wutian449/lanjian-frontend`](https://hub.docker.com/r/wutian449/lanjian-frontend) |
| 沙箱 | [`wutian449/lanjian-sandbox`](https://hub.docker.com/r/wutian449/lanjian-sandbox) |

### 方式 B：本地开发

**后端**：

```bash
cd backend
uv sync                                            # 安装依赖
uv run alembic upgrade head                        # 数据库迁移
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**前端**：

```bash
cd frontend
pnpm install
pnpm dev                                           # 开发服务器（:5173）
```

**仅启动基础设施（本地开发时）**：

```bash
docker compose up -d db redis
```

## 常用脚本

### 后端

```bash
uv sync                                            # 安装依赖
uv run uvicorn app.main:app --reload               # 开发服务器
uv run alembic upgrade head                        # 运行迁移
uv run alembic revision --autogenerate -m "描述"    # 生成迁移
uv run pytest                                      # 运行全部测试
uv run pytest tests/agent/test_file_search_tool_contract.py -v  # 单个测试
uv run ruff check .                                # Lint
uv run black --check .                             # 格式检查
uv run mypy app/                                   # 类型检查
```

### 前端

```bash
pnpm install                                       # 安装依赖
pnpm dev                                           # 开发服务器 (:5173)
pnpm build                                         # 生产构建
pnpm lint                                          # Biome + tsgo + ast-grep 三层 lint
pnpm type-check                                    # tsc --noEmit
pnpm format                                        # Biome 格式化
```

### Docker

```bash
docker compose up -d                               # 默认部署
docker compose -f docker-compose.prod.yml up -d    # 生产部署
docker compose --profile tools up -d               # 附加 Adminer 管理界面
docker compose up -d db redis                      # 仅启动基础设施
docker compose logs -f backend                     # 追踪日志
```

## 关键设计

- **覆盖率门禁**：D1-D10 十维度覆盖率矩阵（注入 / 认证 / 授权 / 反序列化 / 文件 / SSRF / 加密 / 配置 / 业务逻辑 / 供应链）。达标条件为覆盖 ≥6/10（浅覆盖计入）且核心三角 D1/D2/D3 深度覆盖；硬拦截最多 3 次后安全阀放行（`COMPLETED_WITH_GAPS`）
- **审计记忆注入**：任务启动时从 `agent_findings` 加载同项目历史 confirmed/static_confirmed 发现，按 fingerprint 去重后作为线索注入编排上下文（复用 Semgrep 线索注入范式，零表结构变更）
- **暂停恢复**：Orchestrator 通过 `request_pause()` 落 checkpoint，`resume_state` 序列化编排状态
- **Token 预算**：默认 60M，超限降级为 `COMPLETED_WITH_GAPS`
- **SSE 弹性流**：`useResilientStream` 心跳 45s、指数退避 1s→30s、Last-Event-ID 断点续传；历史事件回放显示事件真实发生时间
- **RBAC**：三级角色（super_admin / admin / user），Fernet 加密敏感字段

## 贡献规范

1. **分支命名**：`feat/<topic>`、`fix/<issue>`、`chore/<task>`、`refactor/<area>`、`docs/<scope>`
2. **提交信息**：Conventional Commits 风格（`feat:`、`fix:`、`chore(cleanup):` 等）
3. **代码规范**：
   - 后端：Python 3.11+、行宽 100、mypy 类型注解、ruff + black
   - 前端：TypeScript 严格模式、禁止 `as any`、Biome + tsgo + ast-grep 三层 lint
   - 数据库：模型变更必须配套 Alembic 迁移
4. **提交前必做**：`pnpm build` + `pnpm lint`（前端）、`uv run pytest -x` + `uv run ruff check .`（后端）
5. **PR 描述**：说明背景、方案、风险、验证方式；关联 OpenSpec 变更时贴上 change ID
6. **审查**：默认 draft PR，通过 review + CI 后合并到 `main`

## OpenSpec 规格驱动

大任务实施前应先创建 OpenSpec change，产物齐全后再落码：

```bash
openspec new change <kebab-case-name>
openspec status --change "<name>" --json
openspec instructions apply --change "<name>" --json
```

规格产物位于 `openspec/specs/`，活动变更位于 `openspec/changes/`，归档位于 `openspec/changes/archive/`。

## 许可证

本项目采用 [AGPL-3.0-only](LICENSE) 许可。

## 相关文档

- [后端详细架构](backend/AGENTS.md)
- [前端详细架构](frontend/AGENTS.md)
- [Agent 引擎架构](backend/app/services/agent/AGENTS.md)
- [RAG 管道设计](backend/app/services/rag/AGENTS.md)
- [审计引擎规格](openspec/specs/audit-engine/spec.md)
- [历史交付日志](docs/history/)
