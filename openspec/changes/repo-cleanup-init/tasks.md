# Tasks: Repository Cleanup and Documentation Refresh

## Batch 1 — 顶层杂项 + 本地缓存（A/G 组）

- [ ] T1.1 删除 `audit_remote.log`（137 KB）
- [ ] T1.2 删除 `frontend/audit_remote.log`（616 KB）
- [ ] T1.3 删除 `docs/deleted-paths.txt`
- [ ] T1.4 删除 `frontend/dist/`（构建产物）
- [ ] T1.5 删除 `CLAUDE.md`（与 AGENTS.md 重复）
- [ ] T1.6 新建 `docs/history/`，移动 `CHANGES-sse-realtime-stream.md`、`e2e_test_report.md`
- [ ] T1.7 修正 `.dockerignore` 与 `docker-compose.yml` 中乱码注释（GBK 转 UTF-8）

## Batch 2 — 前端依赖清理（D 组）

- [ ] T2.1 `frontend/package.json` 移除 11 个未引用依赖
- [ ] T2.2 `frontend/vite.config.ts` 修正 `manualChunks`：删除 `ai` chunk（`@google/generative-ai`）、从 `utils` chunk 删除 `date-fns`、从 `optimizeDeps.include` 删除 `@google/generative-ai`
- [ ] T2.3 `frontend/tailwind.config.js` content 数组移除 `./node_modules/streamdown/dist/**/*.js`
- [ ] T2.4 `pnpm install` 更新 lockfile

## Batch 3 — UI 组件清理（E 组）

- [ ] T3.1 全仓引用扫描确认（`components/ui/carousel.tsx`, `chart.tsx`, `map.tsx`, `qrcodedataurl.tsx`, `video.tsx`, `menubar.tsx`, `input-otp.tsx`, `resizable.tsx`, `theme-toggle.tsx`, `metric-card.tsx`, `toaster.tsx`）
- [ ] T3.2 删除已确认零上层引用的组件文件
- [ ] T3.3 若组件依赖某 npm 包只此一处使用，评估该 npm 包是否可从 package.json 一并删除

## Batch 4 — 报告导出去重（F 组）

- [ ] T4.1 diff `AgentReportExportDialog.tsx` 与 `ReportExportDialog.tsx`
- [ ] T4.2 参数化 findings 来源，统一到 `components/reports/AgentReportExportDialog.tsx`
- [ ] T4.3 `pages/AgentAudit/index.tsx` 改导入路径
- [ ] T4.4 删除 `pages/AgentAudit/components/ReportExportDialog.tsx`
- [ ] T4.5 `pnpm build` 验证

## Batch 5 — 后端死代码 + Alembic 迁移（B 组）

- [ ] T5.1 删除 `backend/app/services/controls/` 整个目录
- [ ] T5.2 删除 `backend/app/models/security_control.py` 与 `backend/app/models/coverage.py`
- [ ] T5.3 `backend/app/models/__init__.py` 移除对应 re-export（4 个 ORM + `CoverageTrack`）
- [ ] T5.4 删除 `backend/app/services/agent/tools/kunlun_tool.py`
- [ ] T5.5 `backend/app/services/agent/tools/__init__.py` 移除 `KunlunMTool`/`KunlunRuleListTool`/`KunlunPluginTool` 相关 import 和 `__all__` 条目
- [ ] T5.6 `backend/app/services/agent/agents/base.py` 移除 `get_langchain_tool()` 方法（唯一定义处，无调用者）
- [ ] T5.7 `backend/app/services/agent/agents/base.py` 移除 `_tool_timeouts` 中 `kunlun_scan: 180` 条目
- [ ] T5.8 `backend/app/services/agent/config.py` 移除 `KUNLUN_M_ENABLED` 配置项与 tool_configs 中 `safety_check` 死条目（因外部工具已在 `_build_tools` 直接实例化）
- [ ] T5.9 `backend/app/services/agent/agents/analysis.py`、`recon.py`、`prompts/system_prompts.py` 移除 kunlun_scan 提示词
- [ ] T5.10 `backend/app/services/agent/AGENTS.md` 更新工具清单
- [ ] T5.11 `backend/app/core/config.py` 移除 `CONTROLS_CONFIG_DIR`、`KB_DOCUMENTS_PATH`
- [ ] T5.12 新建 `backend/alembic/versions/023_drop_dead_tables.py`
- [ ] T5.13 `uv run pytest tests/agent/test_prod_compose_no_reload.py` 冒烟

## Batch 6 — 后端依赖 + pyproject（C 组）

- [ ] T6.1 `backend/pyproject.toml` `[project.dependencies]` 删除：`aiofiles`、`sse-starlette`、`langchain`、`langchain-community`、`langchain-openai`、`langgraph`、`django`、`reportlab`、`bandit`、`pygments`
- [ ] T6.2 `backend/pyproject.toml` `[project.dependencies]` 删除 Kunlun-M 传递依赖：`pyjsparser`、`phply`、`esprima`、`jsbeautifier`、`colorlog`、`portalocker`、`prettytable`、`rarfile`、`beautifulsoup4`
- [ ] T6.3 显式补充 `pyyaml`（当前隐式来自 chromadb/langchain 传递，清理后需显式）
- [ ] T6.4 删除 `[project.optional-dependencies].mysql`（Kunlun-M web 模式绑定）
- [ ] T6.5 删除 `[project.optional-dependencies].docs`（无 mkdocs.yml）
- [ ] T6.6 合并 `[project.optional-dependencies].dev` 与 `[dependency-groups].dev`，只保留 `[dependency-groups].dev`
- [ ] T6.7 `uv sync` 更新 uv.lock
- [ ] T6.8 `uv run pytest tests/` 全量测试

## Batch 7 — 验证与文档

- [ ] T7.1 `pnpm build` 生产构建 + `pnpm lint`
- [ ] T7.2 `docker compose config` 语法校验（本地 + prod）
- [ ] T7.3 重写 `README.md` 使其与清理后结构完全匹配
- [ ] T7.4 生成 `DELETIONS.md` 完整删除清单
- [ ] T7.5 `git status` 干净、`git log --oneline codex/repo-cleanup-20260720 ^main` 展示各批 commit

## Batch 8 — 交付

- [ ] T8.1 `git push origin codex/repo-cleanup-20260720`
- [ ] T8.2 `mcp__github__create_pull_request` 开 draft PR 到 `main`
- [ ] T8.3 老板 review 后合并
- [ ] T8.4 老板本地终端跑 `openspec archive repo-cleanup-init` 归档变更
