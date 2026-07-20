# 蓝鉴（lanjian）

蓝鉴是一个面向代码安全审计的本地化平台，采用前后端分离架构，围绕“项目导入、规则审计、AI Agent 分析、沙箱验证、报告导出”组织完整工作流。

## GitHub 仓库说明

如果需要在 GitHub 仓库首页直接展示项目简介，可以使用下面这段说明：

> 蓝鉴（lanjian）是一个面向代码安全审计的本地化平台，支持多阶段 Agent 协同分析、RAG 上下文检索、任务恢复续跑与 Docker 沙箱验证，适用于代码审计、漏洞研判与安全交付场景。

建议突出以下项目亮点：

- 面向代码安全审计的本地化工作台，覆盖导入、分析、验证与报告导出
- 强化代码审计方法论、控制驱动分析和可复核证据链
- 支持 Multi-Agent 审计编排、流式过程反馈和任务恢复
- 支持 Docker Hub 镜像部署、生产 Compose 部署和 Nginx 反向代理部署

## 项目定位与增强能力

蓝鉴围绕实际代码审计交付流程进行工程化设计，在项目管理、规则驱动分析、Agent 协同、上下文检索、沙箱验证与报告导出之间形成完整闭环。

当前版本重点增加了以下能力：

- 自定义代码审计方法论，强调覆盖率、证据链和可复核性
- 面向多阶段审计过程的 Agent 编排与状态恢复能力
- 更完整的代码审计流程，包括入口分析、上下文检索、漏洞验证、结果回写与报告导出
- 面向部署交付的 Docker Hub 镜像方案、生产 Compose 方案和 Nginx 反向代理方案
- 针对长期运行场景的配置收敛、环境变量统一和部署文档整理

当前仓库包含：

- FastAPI 后端
- React/Vite 前端
- PostgreSQL 与 Redis 的 Compose 配置
- Agent 审计所需的 Docker 沙箱镜像配置
- 已发布到 Docker Hub 的初始化镜像部署方案

## 核心功能

- 项目管理：支持仓库项目、ZIP 项目和代码片段分析
- 审计任务：支持传统审计任务与 Multi-Agent 审计任务
- AI 审计：由 Orchestrator、Recon、Analysis、Verification 等 Agent 协作完成代码安全分析
- RAG 检索：通过 tree-sitter、Embedding、ChromaDB 为 Agent 提供代码上下文
- 沙箱验证：通过 Docker 沙箱执行漏洞验证、PoC 和多语言代码片段
- 配置管理：支持 LLM、Embedding、提示词、规则、SSH Key、数据库状态等配置
- 实时反馈：通过 SSE 推送 Agent 审计日志、进度、工具调用和结果
- 报告导出：支持任务结果、审计发现、验证状态等导出

## 审计方法论与流程

蓝鉴并不是只把大模型接到代码仓库上，而是把代码审计拆成一个可执行、可恢复、可验证的多阶段流程。

当前实现重点体现为：

- 方法论驱动：围绕规则集、控制驱动审计、RAG 上下文补全和验证闭环组织分析过程
- 编排驱动：由 Orchestrator 统一调度 Recon、Analysis、Verification 等 Agent 分工协作
- 证据驱动：审计过程中保留工具调用、上下文、推理中间结果和最终 finding
- 验证驱动：对高风险结论尽量进入沙箱验证或二次核验，而不是只停留在提示词结论
- 可恢复驱动：任务中断后保留状态基础，便于继续执行而不是整批重来

典型执行链路如下：

```text
项目导入
  -> 规则/任务初始化
  -> RAG 建库与上下文准备
  -> Orchestrator 编排 Recon / Analysis / Verification
  -> SSE 实时推送过程事件
  -> 记录 finding、验证状态与统计信息
  -> 导出审计报告
```

## 技术栈

| 模块 | 技术 |
|---|---|
| 前端 | React 18、TypeScript 5、Vite 5、Tailwind CSS、Radix UI |
| 后端 | Python 3.11+、FastAPI、SQLAlchemy Async、Alembic、Pydantic |
| 数据存储 | PostgreSQL 15、Redis 7 |
| AI / Agent | LangGraph、LiteLLM、自研 Multi-Agent 审计引擎 |
| RAG | ChromaDB、tree-sitter、Embedding 服务 |
| 部署 | Docker Compose、Nginx、Docker 沙箱 |
| 测试 | pytest、TypeScript 编译检查、Playwright E2E 规格 |

## 环境要求

- Python 3.11 或更高版本
- `uv`
- Node.js 18 或更高版本
- `pnpm`
- Docker 与 Docker Compose

如果只使用已经发布的镜像部署，宿主机不需要本地构建前后端依赖，但仍然需要准备 `backend/.env`。

## Docker 镜像

当前 Compose 默认使用 Docker Hub 公开镜像：

```text
wutian449/lanjian-backend:init
wutian449/lanjian-frontend:init
wutian449/lanjian-sandbox:latest
```

可手动拉取：

```bash
docker pull wutian449/lanjian-backend:init
docker pull wutian449/lanjian-frontend:init
docker pull wutian449/lanjian-sandbox:latest
```

## 部署方式

### 1. Docker Hub 拉取镜像部署

适合首次部署、单机部署或希望直接使用已发布镜像的场景。

先手动拉取镜像：

```bash
docker pull wutian449/lanjian-backend:init
docker pull wutian449/lanjian-frontend:init
docker pull wutian449/lanjian-sandbox:latest
```

然后准备环境变量并启动：

```bash
cp backend/env.example backend/.env
docker compose up -d
```

### 首次登录

系统启动时会自动初始化超级管理员账户，优先读取环境变量 `SUPERADMIN_USERNAME` 和 `SUPERADMIN_PASSWORD`。

如果未显式配置，当前默认管理员账户为：

- 用户名：`admin`
- 密码：`123456789`

出于安全考虑，生产环境部署前请先在 `backend/.env` 中修改默认管理员账号和密码，并在首次登录后立即修改密码。

默认访问入口：

- 前端：`http://localhost/`
- 后端健康检查：`http://localhost:8000/health`
- 后端 API 文档：`http://localhost:8000/docs`

### 2. `docker-compose.prod.yml` 生产部署

适合更接近正式环境的部署方式，重点是把前端作为唯一对外入口，并在启动前自动完成数据库迁移。

执行方式：

```bash
cp backend/env.example backend/.env
docker compose -f docker-compose.prod.yml up -d
```

该部署模式下：

- `db-migrate` 会在后端启动前执行 Alembic 迁移
- `backend` 只在容器内部网络暴露 `8000`
- `frontend` 作为唯一对外 HTTP 入口
- `frontend` 内置 Nginx 会将 `/api/` 请求转发到容器网络内的 `backend`

建议配套检查：

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f frontend
docker compose -f docker-compose.prod.yml logs -f backend
```

### 3. Nginx 反向代理部署

适合在服务器前面再加一层宿主机 Nginx，统一域名、证书和外部访问策略。当前推荐做法是让宿主机 Nginx 反向代理到 Compose 暴露出来的 `frontend:80`，再由前端容器内部的 Nginx 继续转发 `/api/` 到后端。

示例宿主机 Nginx 配置：

```nginx
server {
    listen 80;
    server_name audit.example.com;

    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

如果要启用 HTTPS，可以在宿主机 Nginx 上继续补 `listen 443 ssl`、证书路径和 HTTP 到 HTTPS 跳转规则；应用本身不需要额外改动，只需要保证外部流量仍然先进入 `frontend`。如果要保持审计过程中的流式输出体验，建议保留 `proxy_buffering off` 相关设置。

## 快速启动

### 1. 准备环境变量

复制后端环境变量模板：

```bash
cp backend/env.example backend/.env
```

至少确认以下项目：

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=lanjian_change_me
POSTGRES_DB=lanjian
SECRET_KEY=replace-with-a-real-secret
LLM_PROVIDER=openai
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=
REDIS_URL=redis://redis:6379/0
SANDBOX_IMAGE=wutian449/lanjian-sandbox:latest
```

说明：

- `docker-compose.yml` 和 `docker-compose.prod.yml` 都会读取 `backend/.env`
- 当前默认部署不要求 `frontend/.env`
- 没有单独的根目录 `.env` 依赖

### 2. 使用默认 Compose 启动

在项目根目录执行：

```bash
docker compose up -d
```

默认会启动：

| 服务 | 说明 | 端口 |
|---|---|---|
| `db` | PostgreSQL | `127.0.0.1:5432` |
| `redis` | Redis | `127.0.0.1:6379` |
| `backend` | FastAPI API | `8000` |
| `frontend` | Nginx 托管前端 | `80` |
| `sandbox` | 沙箱镜像占位服务 | 无对外端口 |

访问地址：

- 前端：`http://localhost/`
- 后端健康检查：`http://localhost:8000/health`
- 后端 API 文档：`http://localhost:8000/docs`

### 3. 启用本地附加工具

如果需要 Adminer，执行：

```bash
docker compose --profile tools up -d
```

访问地址：

- Adminer：`http://localhost:8081/`

### 4. 使用生产 Compose 启动

生产配置执行：

```bash
docker compose -f docker-compose.prod.yml up -d
```

生产版与默认版的差异：

- 增加了 `db-migrate` 服务，启动前自动执行 Alembic 迁移
- `backend` 不直接映射宿主机端口，仅在内部网络 `expose 8000`
- 由 `frontend` 统一对外提供入口

## 三个 Compose 文件分别做什么

### `docker-compose.yml`

默认部署文件，适合日常启动、测试或单机部署。

特点：

- 直接拉取 `wutian449/*` 公开镜像
- `backend` 对外开放 `8000`
- `frontend` 对外开放 `80`
- `db` 和 `redis` 只绑定到 `127.0.0.1`

### `docker-compose.override.yml`

默认附加层，作用是给本地环境增加辅助服务或开发期工具。

当前只包含：

- `adminer`

并且放在 `tools` profile 下面，所以不会默认启动，只有显式执行 `docker compose --profile tools up -d` 才会参与。

### `docker-compose.prod.yml`

生产部署文件，适合更接近正式环境的方式启动。

特点：

- 增加 `db-migrate`
- `backend` 只在容器网络内暴露
- `frontend` 作为唯一对外入口
- 同样直接拉取 Docker Hub 公共镜像，不再依赖本地 `build`

## 本地源码开发

如果你不是直接跑镜像，而是要在本地改源码开发，可以这样启动。

### 启动基础设施

```bash
docker compose up -d db redis
```

### 启动后端

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 启动前端

```bash
cd frontend
pnpm install
pnpm dev
```

开发地址：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

## 可用脚本与命令

### 后端

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
uv run pytest
uv run ruff check .
uv run black --check .
uv run mypy app/
```

`backend/start.sh` 是 Linux/macOS 环境下的后端启动脚本，内部会检查 `uv`、同步依赖、执行迁移并启动 FastAPI。

### 前端

```bash
cd frontend
pnpm install
pnpm dev
pnpm build
pnpm preview
pnpm lint
pnpm lint:fix
pnpm type-check
pnpm format
pnpm test:account-logout
pnpm clean
pnpm analyze
```

`frontend/scripts/test-account-logout.mjs` 是账户退出流程的轻量回归脚本。

### Docker

```bash
docker compose up -d
docker compose down
docker compose logs -f backend
docker compose logs -f frontend
docker compose --profile tools up -d
docker compose -f docker-compose.prod.yml up -d
```

如果要本地重新构建沙箱镜像：

```bash
cd docker/sandbox
./build.sh
```

## 项目目录

```text
lanjian/
├── backend/                    # FastAPI 后端
│   ├── app/                    # API、核心服务、模型、Schema、Agent、LLM、RAG
│   ├── alembic/                # 数据库迁移
│   ├── tests/                  # 后端测试
│   ├── uploads/                # 上传文件目录
│   ├── env.example             # 后端环境变量模板
│   ├── pyproject.toml          # 后端依赖与工具配置
│   └── uv.lock                 # 后端锁定依赖
├── frontend/                   # React/Vite 前端
│   ├── src/                    # 页面、组件、共享逻辑
│   ├── public/                 # 静态资源
│   ├── scripts/                # 前端专项回归脚本
│   ├── package.json            # 前端依赖与脚本
│   └── pnpm-lock.yaml          # 前端锁定依赖
├── docker/                     # 沙箱 Dockerfile 与安全配置
├── docs/                       # 架构与流程文档
├── e2e/                        # Playwright E2E 规格
├── openspec/                   # OpenSpec 规格与变更记录
├── rules/                      # 安全规则配置
├── docker-compose.yml          # 默认部署配置
├── docker-compose.override.yml # 本地附加工具配置
├── docker-compose.prod.yml     # 生产部署配置
└── LICENSE                     # 开源许可
```

## 主要执行链路

后端入口是 `backend/app/main.py`，启动时会挂载 `backend/app/api/v1/api.py` 中定义的 API 模块，并执行数据库初始化、服务检查和健康检查路由。

前端入口是 `frontend/src/app/main.tsx`，通过 `frontend/src/app/App.tsx` 和 `frontend/src/app/routes.tsx` 组织页面，包含仪表盘、AI 审计助手、项目管理、即时分析、审计任务、规则管理、提示词管理、系统管理、回收站与账户页面。

Agent 审计核心位于 `backend/app/services/agent/`，主流程如下：

```text
创建任务
  -> 准备项目代码
  -> 初始化 LLM、RAG、工具集
  -> Orchestrator 调度 Recon / Analysis / Verification
  -> SSE 推送进度与工具事件
  -> 保存 finding、验证状态、统计信息
  -> 导出报告
```

## 当前仓库状态

仓库仅保留核心运行必需的源码与配置，默认不追踪：

- 前端 `node_modules` 与 `dist`
- Python `__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`
- 本地日志、审计中间产物（`agent_checkpoints/`、`audit_remote.log` 等）
- IDE、Codex/Claude、OpenSpec 临时状态目录

如果要运行源码开发流程，需要按“本地源码开发”一节重新安装依赖。

## 质量验证

建议每次交付前执行：

```bash
cd frontend
pnpm install
pnpm type-check
pnpm build
pnpm test:account-logout
```

```bash
cd backend
uv sync
uv run pytest
```

```bash
docker compose config
docker compose up -d
```

## 贡献规范

- 修改后端模型时同步新增 Alembic 迁移
- 修改 API 响应结构时同步更新前端调用、类型与相关测试
- 修改 Agent 行为时同步更新 OpenSpec 场景和后端 Agent 测试
- 前端提交前至少运行 TypeScript 检查
- 后端提交前至少运行相关 pytest
- 不提交本地日志、构建产物、缓存、依赖安装目录和私密 `.env` 文件

## 许可

本项目使用 [GNU Affero General Public License v3.0](LICENSE)。

本项目在已有开源审计平台经验基础上持续演进，开发过程中参考并继承了 [DeepAudit](https://github.com/lintsinghua/DeepAudit) 项目的部分设计与实现思路，特此说明并致谢。相关开源许可与继承关系以本仓库 LICENSE 为准；如对外提供网络服务，也应同步提供对应版本源码获取方式。
