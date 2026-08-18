# AGENTS.md - 蓝鉴 (lanjian)

AI 驱动的本地化代码安全审计平台：项目导入 -> 规则审计 -> Multi-Agent AI 分析 -> Docker 沙箱验证 -> 报告导出。
前后端分离：后端 Python 3.11+（FastAPI + uv + Alembic + PostgreSQL 15 + Redis 7），前端 React 18（Vite + TypeScript + Tailwind + pnpm），Docker Compose 部署。

## 目录结构

- `backend/` - FastAPI 后端，入口 `app.main:app`（详见 `backend/AGENTS.md`）
- `backend/app/services/agent/` - Multi-Agent 审计引擎（详见其目录下 AGENTS.md）
- `frontend/` - React 前端（详见 `frontend/AGENTS.md`）
- `e2e/` - Playwright E2E 测试
- `docker/sandbox/` - 沙箱镜像构建（Dockerfile + seccomp）
- `openspec/` - OpenSpec 规格驱动（`specs/` 规格 + `changes/` 活动变更 + `changes/archive/` 归档）
- `docs/` - 架构文档 + 安全加固交付报告
- `rules/` - ast-grep 静态规则
- compose 三件套：`docker-compose.yml`（默认）/ `docker-compose.override.yml`（开发热更）/ `docker-compose.prod.yml`（生产）

## 常用命令

### 后端（backend/）

```bash
uv sync                                    # 安装依赖
uv run alembic upgrade head                # 数据库迁移
uv run uvicorn app.main:app --reload       # 开发服务 (:8000)
uv run pytest                              # 测试
uv run pytest tests/agent/test_xxx.py -v   # 单个测试
uv run ruff check .                        # lint
uv run mypy app/                           # 类型检查
uv run black .                             # 格式化
```

### 前端（frontend/）

```bash
pnpm install
pnpm dev          # 开发服务 (:5173)
pnpm build        # 构建（提交前必做）
pnpm lint         # Biome + tsgo + ast-grep 三层 lint（非 eslint）
pnpm type-check   # tsc --noEmit
pnpm format       # Biome 格式化
```

### Docker

```bash
docker compose up -d                            # 默认全栈
docker compose -f docker-compose.prod.yml up -d # 生产
docker compose up -d db redis                   # 仅基础设施（本地开发）
docker compose logs -f backend                  # 日志
```

## 生产部署（两台服务器）

| | 服务器 B（amd） | 服务器 A（arm） |
|---|---|---|
| IP | `192.168.238.11` | `10.129.7.87` |
| 架构 | amd64 | arm64 |
| OS | CentOS Linux 7 (kernel 3.10.0-1160) | Kylin Linux Advanced Server V10 (kernel 4.19.90) |
| SSH 端口 | 22 | **62222** |
| 部署路径 | `/root/lanjian/` | `/root/lanjian/` |
| 前端入口 | http://192.168.238.11/ | http://10.129.7.87/ |
| 蓝鉴 backend | `:8000` 直接对外暴露 | `:8000` 直接对外暴露 |
| docker build | 需代理 `10.129.1.238:10808`（**未配通**），代码靠现场 override compose | 直连正常，可本地 build |
| 其它业务 | 宿主机 nginx `:8080`（`/etc/nginx/conf.d/drone-platform.conf` 反代，2026-08-03 启用） | 宿主机 xrdp + Xvnc + xray（运维远程/代理用） |
| 蓝鉴 compose | `docker-compose.b-amd64.yml`（落仓，独立精简版，含 `db seccomp:unconfined`） | `docker-compose.yml`（3875B 仓库默认原文） |
| IMAGE_TAG | 显式锁 `v5.1.0` | 镜像层已 v5.1.0，由 `docker-compose.yml` 显式锁 |

- 两台均跑 5 容器：`db`（postgres:15-alpine）、`redis`（redis:7-alpine）、`backend`、`frontend`、沙箱（`restart: no` 保持 Exited，仅作 docker.sock 动态起 PoC 容器的基底）。
- 镜像来自 Docker Hub 组织 `wutian449`（lanjian-backend / lanjian-frontend / lanjian-sandbox），`v5.1.0` 为多架构（amd64 + arm64）；生产已锁 `v5.1.0`，禁止 `:latest` 浮动。
- 部署凭证（SUPERADMIN/POSTGRES 密码、SECRET_KEY）见 `docs/security-hardening-2026-07-DELIVERY.md` §6，登录凭证已录入 remote-shell 加密凭证库（credctl）。
- **远程操作唯一入口是 remote-shell 技能**，默认只读，危险操作须老板确认。

### 各服务器部署的业务

- **两台跑同一套蓝鉴全栈**（数据与凭证各自独立）：`frontend`（nginx :80，SPA + `/api/` 反代 `backend:8000`，SSE 已关代理缓冲）-> `backend`（uvicorn 单 worker，挂 docker.sock）-> `db`（postgres:15-alpine）/ `redis`（redis:7-alpine）；`sandbox` 镜像仅作 backend 经 docker.sock 动态起 PoC 容器的基底（自身 `restart: no` 不常驻）。数据卷：`postgres_data` / `redis_data` / `backend_uploads`。
- **服务器 B（amd）另有 drone-platform 业务**：宿主机 nginx `:8080`（`/etc/nginx/conf.d/drone-platform.conf`），反代外部服务（MQTT → 192.168.128.3:8083 / MinIO → 127.0.0.1:9000 / 地图瓦片 → 10.129.28.130:8005 / 天气 → 10.129.30.115:8088 / 直播 → 127.0.0.1:1984 / AI 检测 → 127.0.0.1:5000）。蓝鉴与 drone 共存，端口不冲突（80/8000 vs 8080）。
- **服务器 A（arm）无独立业务容器**，仅有宿主机运维工具（xrdp/Xvnc/xray）。
- **两台代码更新路径不同**：B 无法 docker build（代理未通），改动靠现场 `docker-compose.b-amd64.yml` override 维持；A 可直接 `docker build` 沙箱镜像。
- 两台历史数据均已重初始化（2026-07 交付事故），现库为交付后新建；实时核对容器/版本状态需老板先 `credctl unlock` 解锁凭证库。

### 部署铁律（历史教训，2026-07 交付时踩过）

1. 动数据库前**必须先 `pg_dump`**（曾因 pg14→pg15 冲突导致整库丢失）。
2. 任何删除/重置服务器目录的操作前，必须先取出并保存 `.env`（曾因整目录删除丢失 LLM_API_KEY/GITHUB_TOKEN）；清理动作先列精确目标、经老板确认，优先可恢复操作。
3. 上传新 compose 前核对 db image 版本，避免容器连带重建。
4. 部署验证必须覆盖 backend **和** frontend（曾漏前端导致修复未生效）。

## 关键约束与坑

- **生产严禁 `--reload`**：uvicorn 热重启会掐断所有 SSE 连接、丢失内存中 Orchestrator/EventManager 状态，任务进入 stale running。热更只写在 `docker-compose.override.yml`。
- **沙箱 bind mount**：`/tmp/lanjian:/tmp/lanjian:rw` 必须保留。backend 经 docker.sock 起沙箱，daemon 是宿主机进程，看不到容器内解压的 ZIP 就会导致沙箱验证空跑。注意：仓库内 `docker-compose.prod.yml` 的 backend **未包含**此挂载（交付修复只写进了默认 `docker-compose.yml`），改用 prod.yml 部署前必须补上。
- **4 个强制环境变量**（缺失拒绝启动）：`SECRET_KEY`（≥32 位）、`CORS_ALLOWED_ORIGINS`、`SUPERADMIN_PASSWORD`（≥12 位复杂度）、`POSTGRES_PASSWORD`（黑名单校验）。模板见 `backend/env.example`。
- 后端单 worker（`--workers 1`），SSE/任务状态在进程内存中；跨进程 Registry 未落地。
- 敏感字段 Fernet 加密存储，密文带 `enc:v1:` 前缀；SECRET_KEY 轮换会显式抛异常。
- RBAC 三级角色（super_admin / admin / user）+ 行级数据范围隔离，资源访问统一走 `assert_can_access_project`。
- 沙箱 `/workspace/src` 只读，PoC 写 `/workspace/poc`。

## 编码规范

- 后端：Python 3.11+、行宽 100、mypy 类型注解、ruff + black；数据库模型变更必须配套 Alembic 迁移。
- 前端：TypeScript 严格模式、禁止 `as any`；格式化统一用 Biome（不用 prettier/eslint）。
- 提交前必做：前端 `pnpm build` + `pnpm lint`；后端 `uv run pytest -x` + `uv run ruff check .`。
- 分支命名 `feat/<topic>` / `fix/<issue>` / `chore/<task>`，Conventional Commits。

## 改动前先读

- 动后端 → `backend/AGENTS.md`
- 动前端 → `frontend/AGENTS.md`
- 动审计引擎 → `backend/app/services/agent/AGENTS.md`
- 动 RAG → `backend/app/services/rag/AGENTS.md`
- 动审计引擎规格 → `openspec/specs/audit-engine/spec.md`
- 安全加固背景 → `docs/security-hardening-2026-07-DELIVERY.md`
