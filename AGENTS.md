# AGENTS.md - 蓝鉴 (lanjian)

AI 驱动的本地化代码安全审计平台：项目导入 -> RAG 索引 + 静态扫描（Semgrep/Bandit/Gitleaks 等 7 种外部工具 + 内置 OWASP 正则模式库）-> Multi-Agent AI 分析 -> Docker 沙箱 PoC 验证 -> 报告导出。
前后端分离：后端 Python 3.11+（FastAPI + uv + Alembic + PostgreSQL 15 + Redis 7），前端 React 18（Vite + TypeScript + Tailwind + pnpm），Docker Compose 部署。

## 目录结构

- `backend/` - FastAPI 后端，入口 `app.main:app`（详见 `backend/AGENTS.md`）
- `backend/app/services/agent/` - Multi-Agent 审计引擎（详见其目录下 AGENTS.md）
- `frontend/` - React 前端（详见 `frontend/AGENTS.md`）
- `e2e/` - Playwright E2E（仅 1 个 spec、无 playwright.config，基建实际废弃）
- `docker/sandbox/` - 沙箱镜像构建（多语言 Dockerfile + seccomp；国内镜像源，`ARG TARGETARCH` 多架构）
- `openspec/` - OpenSpec 规格驱动（`specs/` 4 个规格域：audit-engine/llm-adapter/rag/sse-realtime-stream）；2026-08 有 4 个活动变更在途（fix-deployment-drift-2026-08 进行中 5/11，其余 3 个未动工），动手前先对齐
- `docs/` - 代码级流程分析（agent-execution-flow / audit-data-flow）+ 安全加固交付报告
- `rules/` - ast-grep 规则（仅 SelectItem.yml，前端 TSX lint 用；后端审计不使用 ast-grep）
- compose 四件套：`docker-compose.yml`（默认）/ `docker-compose.override.yml`（开发热更）/ `docker-compose.prod.yml`（生产，含独立 db-migrate 服务）/ `docker-compose.b-amd64.yml`（服务器 B 现场 override）
- `.codegraph/` - 已建 CodeGraph 索引，定位代码优先 `codegraph explore` 再用 rg

## 常用命令

### 后端（backend/）

```bash
uv sync                                    # 安装依赖
uv run alembic upgrade head                # 数据库迁移（共 24 个，head=023_drop_dead）
uv run alembic revision --autogenerate -m "描述"  # 生成迁移
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
| 蓝鉴 compose | `docker-compose.b-amd64.yml`（落仓精简版，含 `db seccomp:unconfined`；实测为生效 compose） | `docker-compose.yml`（仓库默认；实测为生效 compose） |
| IMAGE_TAG | compose 显式锁 `v5.4.0`（backend/frontend），sandbox 锁 `v5.1.0`（`.env` 未设此变量） | compose 显式锁 `v5.4.0`（backend/frontend），sandbox 锁 `v5.1.0`（`.env` 残留惰性 `IMAGE_TAG=v5.0.0`，compose 已硬编码 tag 不受影响） |

- 两台均跑 5 容器：`db`（postgres:15-alpine）、`redis`（redis:7-alpine）、`backend`、`frontend`、沙箱（`restart: no` 保持 Exited，仅作 docker.sock 动态起 PoC 容器的基底）。
- 镜像来自 Docker Hub 组织 `wutian449`（lanjian-backend / lanjian-frontend / lanjian-sandbox），`v5.4.0`（backend/frontend）与 `v5.1.0`（sandbox）均为多架构（amd64 + arm64）；生产已锁 `v5.4.0` / `v5.1.0`，禁止 `:latest` 浮动。
- **2026-08-19 只读实测**：两台 5 容器全部 `v5.1.0`、db/redis healthy、sandbox 按设计 Exited(0)；生效 compose 经容器 label 核实（B=`b-amd64.yml`，A=默认 `docker-compose.yml`）；A 机另有 buildx buildkit 常驻容器（本地构建用）。
- **2026-08-20 v5.3.0 已部署**：两台服务器（A/B）backend+frontend 已升级到 `v5.3.0`，**sandbox 仍锁 `v5.1.0`**（本次未重建沙箱镜像）。本次为**每台服务器本地重新构建镜像**（非重打 tag）：基础镜像走国内 registry 镜像源 `docker.m.daocloud.io` + `docker.1ms.run`；backend Dockerfile 改为 `pip install uv`（阿里云 PyPI）替代 `COPY --from=docker.io/astral/uv`。生效 compose 经容器 label 核实（B=`b-amd64.yml`，A=默认 `docker-compose.yml`）。
- **2026-08-22 v6.0.0 已部署**：两台服务器（A/B）backend+frontend 已升级到 `v6.0.0`，**sandbox 仍锁 `v5.1.0`**（未重建沙箱镜像）。本次为每台服务器本地重新构建镜像，内容为登录固定跳 /dashboard（REQ-LR-1）+ 验证证据链根治（B1-B6：失败绑定/标记收窄/回填/索引直写/会话瘦身/模板分级）。
- **2026-08-23 v6.0.1 已部署**：两台服务器（A/B）backend 已升级到 `v6.0.1`（**frontend 保持 `v6.0.0`**，本次无前端改动），**sandbox 仍锁 `v5.1.0`**。内容为验证完整性修复（验证清单全量送验/R4+轮次耗尽程序化补验/模板正则修复+poc_error 分档/sink 挂钩收敛误报/static 口径收紧）。A 机生产回归（nacos）2/2 confirmed 通过。
- **2026-08-21 v5.4.0 已部署**：两台服务器（A/B）backend+frontend 已升级到 `v5.4.0`，**sandbox 仍锁 `v5.1.0`**（本次未重建沙箱镜像）。本次同样为**每台服务器本地重新构建镜像**（基于 v5.3.0 镜像叠加 v5.4.0 代码），内容为验证引擎根治（R1-R7 确定性证据判定与门禁终止）+ 北京时间统一输出。
- ⚠️ **v5.1.0 镜像是重打 tag，不是重新构建**（历史教训）：三个镜像的层创建于 2026-06/07（v5.0.0 时代），v5.1.0 为纯版本号升级；两台前端容器显示的 5.1.0 是 2026-08-18 容器重建后**就地 sed 修补 dist**（index/icons/utils 三个 JS bundle）的产物--**recreate 容器后版本号显示会回退 5.0.0**。**v5.3.0 已改为真正重新构建**（见上一条），改代码必须重新 build 镜像再更新两台，只 bump 版本号无效。
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
- **沙箱 bind mount**：`/tmp/lanjian:/tmp/lanjian:rw` 必须保留。backend 经 docker.sock 起沙箱，daemon 是宿主机进程，看不到容器内解压的 ZIP 就会导致沙箱验证空跑。三个部署 compose（默认/prod/b-amd64）现已全部包含此挂载（2026-08-19 核实，prod.yml 曾缺失的坑已修复）。
- **环境变量分级**（README 称"4 个强制"，实际以代码为准）：真正拒绝启动的只有 `SECRET_KEY`（≥32 位 + 弱值黑名单）和 `POSTGRES_PASSWORD`（≥12 位 + 弱值黑名单）；`CORS_ALLOWED_ORIGINS` 未配置仅降级警告（origins=[]）；`SUPERADMIN_PASSWORD` 缺失或不达标时**跳过超管创建**而非拒绝启动。compose 层对 SECRET_KEY 用 `:?` 直接失败。模板见 `backend/env.example`。
- 后端单 worker（`--workers 1`）的根源：`agent_tasks.py` 模块级内存 dict（`_running_orchestrators`/`_running_event_managers` 等）持有编排器与 SSE 队列；OrchestratorRegistry 已把存活心跳 Redis 化（`lanjian:orch:{task_id}`），但编排本体仍在进程内存，多 worker 仍不可用。
- 敏感字段 Fernet 加密存储，密文带 `enc:v1:` 前缀；SECRET_KEY 轮换会显式抛异常。
- RBAC 三级角色（super_admin / admin / user）+ 行级数据范围隔离；项目资源访问统一走 `assert_can_access_project`（2026-08 安全加固已补上 members.py 遗漏的断言；前端用户管理已对 admin 开放，与后端下辖管理 RBAC 对齐）。
- 沙箱 `/workspace/src` 只读，PoC 写 `/workspace/poc`（容器 read_only + cap_drop ALL + 默认 network none + 60s 超时；SANDBOX_IMAGE 代码默认 `:latest`，生产靠 compose 锁 v5.1.0 覆盖）。
- **uv.lock 与 pyproject 不同步**：lock 停在 v3.5.0 时代（含 langchain/langgraph 等 pyproject 未声明的依赖），pyproject 已 6.1.0；动依赖先 `uv lock` 再全量测试。
- **SSE 只服务 Agent 审计页**：前端 useResilientStream 用 fetch+ReadableStream（非 EventSource，需 Bearer header），心跳 45s/长操作 180s、Last-Event-ID + after_sequence 续传、最多重连 5 次；普通审计任务是 setInterval 轮询（2s->60s 分级），无 SSE。
- **前端版本号在构建期硬编码进 JS bundle**（package.json version 经 vite 注入），运行时不可配；升版本必须重构建前端镜像。

## 代码速览（2026-08-21 全量深读核验）

- 后端：13 个 API 端点模块（`agent_tasks.py` 最大，4803 行）；16 张表 / 9 个 model 文件；24 个 alembic 迁移（head=`023_drop_dead`）；82 个测试文件 575 个用例（asyncio_mode=auto，含 2026-08 新增 `tests/rag/test_indexing_hardening.py`、`tests/agent/test_verification_evidence.py`、`tests/agent/test_orchestrator_gates.py`）。
- 审计引擎：OrchestratorAgent（ReAct 循环 + Semgrep 预扫描）调度 Recon/Analysis/Verification 三子 Agent；27 种 SSE 事件（`models/agent_task.py` 的 `AgentEventType` 枚举）；D1-D10 十维度覆盖率门禁；LLM 调用经熔断器 + 令牌桶限流（均已接线）。
- 静态检测三套：沙箱内外部工具 7 种（Semgrep/Bandit/Gitleaks/TruffleHog/npm audit/Safety/OSV-Scanner）+ 内置 OWASP 正则模式库 + DB 中的 AuditRuleSet（LLM 提示词规则）。
- **RAG 索引加固（v5.3.0 新增）**：`indexer.py` 构建产物路径段排除 + minified 启发式（`BUILD_ARTIFACT_DIR_SEGMENTS` / `MAX_SINGLE_LINE_LENGTH=2000` / `MAX_SOURCE_FILE_SIZE=2MB`）、单文件分块护栏（`FILE_CHUNK_TIMEOUT=20s` 跳过、`MAX_CHUNKS_PER_FILE=500` 截断）、有界并发（`CHUNK_CONCURRENCY=4` 分批 gather）、嵌入快速失败（`EmbeddingUnavailableError` 重试耗尽后抛出，agent_tasks `rag_unavailable` 分支，禁止写入零向量）、进度消息分阶段标注（`CHUNK_PROGRESS_MSG_TEMPLATE`=分块进度 / `EMBED_PROGRESS_MSG_TEMPLATE`=嵌入进度）。烟雾脚本 `backend/smoke_rag_indexing.py`，新测试 `backend/tests/rag/test_indexing_hardening.py`。
- **验证引擎根治（v5.4.0 新增）**：`verification.py` 新增纯函数 `compute_verification_status`（R1 确定性状态引擎：由 sandbox_attempts 证据推导 verification_status/is_verified，不信任 LLM 自述 verdict；仅保留 false_positive / sandbox_skip_reason 两个显式标注位）+ `_bind_runtime_evidence_to_all`（R2 全量证据按 finding_id 强制绑定，LLM 漏报不丢证据）+ `FABRICATION_MARKERS` 反伪造（R3：Simulated/模拟输出/源码缺失标记 `fabricated`，排除出判定与门禁）+ 确定性沙箱执行（R3 前置执行全部预生成 PoC）；`orchestrator.py` 门禁 3 次终止（R4 `verification_max_force_redispatch` 默认 3）+ Bug D 判定修正（R5：needs_context 视为未验证）+ observations 记录（R6）+ 中断收口（R7 补发 dispatch_complete/phase_complete）；`agent_tasks.py` 持久化 observations 到 `agent_tasks.observations`；`app/core/timeutil.py` 新增北京时间统一序列化（UTC+8），`main.py`/`agent_tasks.py` 全部 API/SSE 时间字段走 `serialize_cst`。测试见 `tests/agent/test_verification_evidence.py`（42 用例）与 `tests/agent/test_orchestrator_gates.py`。
- **验证证据链根治 V6（v6.0.0 新增）**：登录成功固定跳 /dashboard（不再回跳 state.from）；`verification.py` B1-B6——绑定层取消 success 前置（失败 attempt 如实绑定 → not_reproducible）、失败标记收窄（`_has_sandbox_failure_marker`：子串级 Traceback/Error:/Exception: 仅 exit≠0 或 stderr 段内生效）、空 Final Answer 回填（`_finalize_findings_without_final_answer` 归一化前按 finding_id 回填）、确定性执行直写索引（`_runtime_attempts_by_finding_id` 双写 + 绑定优先消费索引 + 语义键去重防双计）、会话瘦身（单条 observation 截断保头尾 1500+1500 + 历史超 40 条压缩最旧一半；config `observation_history_max_chars=4000` / `history_soft_limit_messages=40`）、模板 PoC 源码断言（源码缺失 sys.exit(1) 不进演示段）与证据分级（演示确认输出 VULNERABILITY_CONFIRMED(STATIC) 变体 → static_evidence 降档 static_confirmed，最高不得判 confirmed）。测试 `tests/agent/test_verification_evidence.py`（59 用例）。
- **验证完整性修复（v6.0.1 新增）**：待验清单全量送验（verification 去 `[:20]` 硬截断、orchestrator 交接用 `_all_findings` 全量 severity 排序）；R4 门禁与轮次/覆盖率耗尽收尾双路径 `_maybe_dispatch_force_verification` 程序化补验（幂等）；merge-back 按 `_sandbox_finding_id` 回写原对象；4 处模板正则转义修复（ssrf/path_traversal/deserialization）；`poc_error` 分档（崩溃→needs_context+notes `pre-generated PoC crashed`，软证据兜底排除）；6 类模板演示确认挂源码 sink 断言（sink=0 → NO_SINK）；`_has_valid_sandbox_evidence` 对 static_confirmed 要求 attempts 非空；证据摘要保头尾 + 去重证据优先。测试 `test_verification_evidence.py`（36 用例）/`test_orchestrator_gates.py`（18 用例）。
- 前端：16 个页面 / 13 条业务路由（无角色分流，权限全靠后端 API）；React Context + useReducer（无 zustand）；无单测框架（仅 3 个静态断言脚本）；三层 lint = tsgo + Biome（单规则）+ ast-grep。

## 编码规范

- 后端：Python 3.11+、行宽 100、mypy 类型注解、ruff + black；数据库模型变更必须配套 Alembic 迁移。
- 前端：TypeScript 严格模式、禁止 `as any`；格式化统一用 Biome（不用 prettier/eslint）。
- 提交前必做：前端 `pnpm build` + `pnpm lint`；后端 `uv run pytest -x` + `uv run ruff check .`。
- 分支命名 `feat/<topic>` / `fix/<issue>` / `chore/<task>`，Conventional Commits。

## 改动前先读

- 动后端 → `backend/AGENTS.md`（2026-08-20 已核对：端点模块 13 个 / 模型 9 个 / 迁移 24 个（head=`023_drop_dead`）/ 测试 78 个文件，agent_tasks/projects 行数及 Semgrep 条目已修正；其余文件内细节仍以代码为准）
- 动前端 → `frontend/AGENTS.md`（2026-08-20 已删除已被移除的 apiInterceptor 引用（commit 104da40）；组件/路由/zod 等描述仍以代码为准）
- 动审计引擎 → `backend/app/services/agent/AGENTS.md`（2026-08-20 已核对：SSE 事件类型 27 种（`models/agent_task.py` 枚举）、关键超时/迭代常量与 `config.py` 一致）
- 动 RAG → `backend/app/services/rag/AGENTS.md`（与代码一致性良好）
- 动审计引擎规格 → `openspec/specs/audit-engine/spec.md`
- 安全加固背景 → `docs/security-hardening-2026-07-DELIVERY.md`（§6 部署凭证、§7 留待老板事项）
- 代码级流程 -> `docs/agent-execution-flow.md` + `docs/audit-data-flow.md`（2026-06-22 产出；2026-08-20 已修正"熔断/限流未接入"与"18 种事件"两条过时结论，其余行号仍可能有漂移，仅作架构参考）
