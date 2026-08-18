# CLAUDE.md — 蓝鉴 (lanjian)

前后端分离的容器化项目。后端 Python (FastAPI + uv + Alembic)，前端 React (Vite + TypeScript + Tailwind + pnpm)，Docker 部署。

## 项目结构

- `backend/` — Python 后端，FastAPI，入口 `app.main:app`，数据库迁移用 Alembic
- `frontend/` — React + Vite + TypeScript 前端，包管理器 pnpm
- `e2e/` — Playwright 端到端测试
- `docker/` — 容器相关配置
- `openspec/` — OpenSpec 规格
- `rules/` — 项目规则

## 后端命令 (backend/)

```bash
uv sync                                    # 安装依赖
uv run alembic upgrade head                # 执行数据库迁移
uv run uvicorn app.main:app --reload       # 本地启动开发服务 (:8000)
uv run pytest                              # 运行测试
uv run ruff check .                        # lint
uv run mypy .                              # 类型检查
uv run black .                             # 格式化
```

- Python 版本要求：`>=3.11`
- 测试目录：`backend/tests/`
- 生产启动见 `backend/start.sh` / `backend/docker-entrypoint.sh`

## 前端命令 (frontend/)

```bash
pnpm install          # 安装依赖
pnpm run dev          # 开发服务 (vite)
pnpm run build        # 构建
pnpm run lint         # tsgo + biome + ast-grep 检查
pnpm run type-check   # tsc --noEmit
pnpm run format       # biome 格式化
pnpm run preview      # 预览构建产物 (:5173)
```

- Lint 工具链：**biome + tsgo + ast-grep**（非 eslint/prettier）
- 格式化统一用 biome

## Docker

```bash
docker compose build      # 构建镜像
docker compose up         # 启动全栈
docker compose logs -f    # 查看日志
```

- 开发：`docker-compose.yml` + `docker-compose.override.yml`
- 生产：`docker-compose.prod.yml`

## 说明

- 后端凭据管理脚本：`python scripts/credctl.py list`
- 远程操作统一走 remote-shell 技能
- 本仓库已建立 `.codegraph` 索引，定位代码优先用 CodeGraph
