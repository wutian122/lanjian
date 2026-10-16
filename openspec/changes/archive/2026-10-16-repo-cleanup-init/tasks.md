# Tasks: Repository Cleanup and Documentation Refresh

> **台账治理裁定（2026-10-16，老板批准弃权归档）**：本变更为 2026-07-29 的仓库清理计划，
> 49 项全部弃权记账、不再补做。部分诉求已随后续工作自然完成（T1.1 audit_remote.log 删除、
> T1.5 CLAUDE.md 删除（fe0cfb3）、docs 历史文档清理（2026-10-16 文档刷新）、frontend/public
> 精简（41c9133，20M→248K））；其余清理项（前端依赖/UI 组件/报告导出去重/后端死代码/
> 迁移整理等）搁置至今已与现状脱节，如需执行应另立新变更逐项重估。
> **spec delta 处置**：delta 中 REMOVED（安全控制矩阵/Kunlun-M）与 MODIFIED（外部安全
> 工具矩阵/LLM 工具适配层）的目标 requirement 在主 `openspec/specs/audit-engine/spec.md`
> 中均已不存在（spec-merger 重构主 spec 时已消失，死表亦由 023 迁移删除），操作无从执行，
> 故按 `--skip-specs` 归档，主 specs 不受影响。

## Batch 1 — 顶层杂项 + 本地缓存（A/G 组）

- [x] T1.1 删除 `audit_remote.log`（137 KB）
- [x] T1.2 删除 `frontend/audit_remote.log`（616 KB）
- [x] T1.3 删除 `docs/deleted-paths.txt`
- [x] T1.4 删除 `frontend/dist/`（构建产物）
- [x] T1.5 删除 `CLAUDE.md`（与 AGENTS.md 重复）
- [x] T1.6 新建 `docs/history/`，移动 `CHANGES-sse-realtime-stream.md`、`e2e_test_report.md`
- [x] T1.7 修正 `.dockerignore` 与 `docker-compose.yml` 中乱码注释（GBK 转 UTF-8）

## Batch 2 — 前端依赖清理（D 组）

- [x] T2.1 `frontend/package.json` 移除 11 个未引用依赖
- [x] T2.2 `frontend/vite.config.ts` 修正 `manualChunks`：删除 `ai` chunk（`@google/generative-ai`）、从 `utils` chunk 删除 `date-fns`、从 `optimizeDeps.include` 删除 `@google/generative-ai`
- [x] T2.3 `frontend/tailwind.config.js` content 数组移除 `./node_modules/streamdown/dist/**/*.js`
- [x] T2.4 `pnpm install` 更新 lockfile

## Batch 3 — UI 组件清理（E 组）

- [x] T3.1 全仓引用扫描确认（`components/ui/carousel.tsx`, `chart.tsx`, `map.tsx`, `qrcodedataurl.tsx`, `video.tsx`, `menubar.tsx`, `input-otp.tsx`, `resizable.tsx`, `theme-toggle.tsx`, `metric-card.tsx`, `toaster.tsx`）
- [x] T3.2 删除已确认零上层引用的组件文件
- [x] T3.3 若组件依赖某 npm 包只此一处使用，评估该 npm 包是否可从 package.json 一并删除

## Batch 4 — 报告导出去重（F 组）

- [x] T4.1 diff `AgentReportExportDialog.tsx` 与 `ReportExportDialog.tsx`
- [x] T4.2 参数化 findings 来源，统一到 `components/reports/AgentReportExportDialog.tsx`
- [x] T4.3 `pages/AgentAudit/index.tsx` 改导入路径
- [x] T4.4 删除 `pages/AgentAudit/components/ReportExportDialog.tsx`
- [x] T4.5 `pnpm build` 验证

## Batch 5 — 后端死代码 + Alembic 迁移（B 组）

- [x] T5.1 删除 `backend/app/services/controls/` 整个目录
- [x] T5.2 删除 `backend/app/models/security_control.py` 与 `backend/app/models/coverage.py`
- [x] T5.3 `backend/app/models/__init__.py` 移除对应 re-export（4 个 ORM + `CoverageTrack`）
- [x] T5.4 删除 `backend/app/services/agent/tools/kunlun_tool.py`
- [x] T5.5 `backend/app/services/agent/tools/__init__.py` 移除 `KunlunMTool`/`KunlunRuleListTool`/`KunlunPluginTool` 相关 import 和 `__all__` 条目
- [x] T5.6 `backend/app/services/agent/agents/base.py` 移除 `get_langchain_tool()` 方法（唯一定义处，无调用者）
- [x] T5.7 `backend/app/services/agent/agents/base.py` 移除 `_tool_timeouts` 中 `kunlun_scan: 180` 条目
- [x] T5.8 `backend/app/services/agent/config.py` 移除 `KUNLUN_M_ENABLED` 配置项与 tool_configs 中 `safety_check` 死条目（因外部工具已在 `_build_tools` 直接实例化）
- [x] T5.9 `backend/app/services/agent/agents/analysis.py`、`recon.py`、`prompts/system_prompts.py` 移除 kunlun_scan 提示词
- [x] T5.10 `backend/app/services/agent/AGENTS.md` 更新工具清单
- [x] T5.11 `backend/app/core/config.py` 移除 `CONTROLS_CONFIG_DIR`、`KB_DOCUMENTS_PATH`
- [x] T5.12 新建 `backend/alembic/versions/023_drop_dead_tables.py`
- [x] T5.13 `uv run pytest tests/agent/test_prod_compose_no_reload.py` 冒烟

## Batch 6 — 后端依赖 + pyproject（C 组）

- [x] T6.1 `backend/pyproject.toml` `[project.dependencies]` 删除：`aiofiles`、`sse-starlette`、`langchain`、`langchain-community`、`langchain-openai`、`langgraph`、`django`、`reportlab`、`bandit`、`pygments`
- [x] T6.2 `backend/pyproject.toml` `[project.dependencies]` 删除 Kunlun-M 传递依赖：`pyjsparser`、`phply`、`esprima`、`jsbeautifier`、`colorlog`、`portalocker`、`prettytable`、`rarfile`、`beautifulsoup4`
- [x] T6.3 显式补充 `pyyaml`（当前隐式来自 chromadb/langchain 传递，清理后需显式）
- [x] T6.4 删除 `[project.optional-dependencies].mysql`（Kunlun-M web 模式绑定）
- [x] T6.5 删除 `[project.optional-dependencies].docs`（无 mkdocs.yml）
- [x] T6.6 合并 `[project.optional-dependencies].dev` 与 `[dependency-groups].dev`，只保留 `[dependency-groups].dev`
- [x] T6.7 `uv sync` 更新 uv.lock
- [x] T6.8 `uv run pytest tests/` 全量测试

## Batch 7 — 验证与文档

- [x] T7.1 `pnpm build` 生产构建 + `pnpm lint`
- [x] T7.2 `docker compose config` 语法校验（本地 + prod）
- [x] T7.3 重写 `README.md` 使其与清理后结构完全匹配
- [x] T7.4 生成 `DELETIONS.md` 完整删除清单
- [x] T7.5 `git status` 干净、`git log --oneline codex/repo-cleanup-20260720 ^main` 展示各批 commit

## Batch 8 — 交付

- [x] T8.1 `git push origin codex/repo-cleanup-20260720`
- [x] T8.2 `mcp__github__create_pull_request` 开 draft PR 到 `main`
- [x] T8.3 老板 review 后合并
- [x] T8.4 老板本地终端跑 `openspec archive repo-cleanup-init` 归档变更
