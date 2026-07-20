# Repo Cleanup v3.6.0 — Deletion Manifest

分支：`codex/repo-cleanup-20260720`
基准：`main @ 6b532ae`
OpenSpec：`openspec/changes/repo-cleanup-init/`

## 汇总

| 类别 | 数量 |
|------|------|
| 顶层杂项文件删除 | 4 |
| 顶层杂项文件归档到 `docs/history/` | 2 |
| 后端 Python 文件删除 | 6 |
| 后端 Alembic 迁移新增 | 1 |
| 前端 UI 组件删除 | 19 |
| 前端 TS 文件删除 | 1（重复组件） |
| 前端 npm 依赖删除 | 26 |
| 后端 Python 包依赖删除 | 19 |
| 后端 Python 包依赖新增 | 1（pyyaml） |
| 前端代码总减行数 | ~4970 |
| 后端代码总减行数 | ~995 |

## Batch 1：顶层杂项（A 组）

| 操作 | 路径 | 原因 |
|------|------|------|
| DEL | `audit_remote.log` | 137 KB remote-shell 历史日志，已在 `.gitignore` |
| DEL | `frontend/audit_remote.log` | 616 KB 同上 |
| DEL | `docs/deleted-paths.txt` | 陈旧 `.gitignore` 目录快照，与实际不一致 |
| DEL | `CLAUDE.md` | 与 `AGENTS.md` 内容 95% 重复 |
| DEL | `frontend/dist/` | 构建产物，`.gitignore` 已忽略 |
| MOV | `CHANGES-sse-realtime-stream.md` → `docs/history/` | 单次修复交付日志归档 |
| MOV | `e2e_test_report.md` → `docs/history/` | 2026-07-13 一次性 E2E 报告归档 |
| FIX | `.dockerignore` | GBK 乱码注释重写为 UTF-8 |

## Batch 2：前端依赖（D 组）

**package.json 删除 14 个 dependencies**：

- `@google/generative-ai`
- `@radix-ui/react-icons`
- `@supabase/supabase-js`
- `date-fns`
- `eventsource-parser`
- `fflate`
- `i18next`
- `i18next-browser-languagedetector`
- `ky`
- `miaoda-auth-react`
- `miaoda-sc-plugin`
- `react-i18next`
- `streamdown`
- `zod`

**vite.config.ts**：删除 `manualChunks.ai` chunk（`@google/generative-ai`）、从 `utils` chunk 删除 `date-fns` + `qrcode`、从 `optimizeDeps.include` 删除 `@google/generative-ai`

**tailwind.config.js**：删除 `./node_modules/streamdown/dist/**/*.js` content glob

## Batch 3：UI 组件（E 组）

**components/ui/ 删除 19 个未引用组件**：

- `aspect-ratio.tsx`, `breadcrumb.tsx`, `calendar.tsx`, `carousel.tsx`, `chart.tsx`
- `command.tsx`, `drawer.tsx`, `input-otp.tsx`, `map.tsx`, `menubar.tsx`
- `navigation-menu.tsx`, `pagination.tsx`, `qrcodedataurl.tsx`, `resizable.tsx`
- `slider.tsx`, `toaster.tsx`, `toggle.tsx`, `toggle-group.tsx`, `video.tsx`

**package.json 追加删除 12 个 UI 相关 dependencies**：

- `@radix-ui/react-aspect-ratio`, `@radix-ui/react-menubar`, `@radix-ui/react-navigation-menu`
- `@radix-ui/react-slider`, `@radix-ui/react-toggle`, `@radix-ui/react-toggle-group`
- `cmdk`, `embla-carousel-react`, `input-otp`, `qrcode`
- `react-day-picker`, `react-resizable-panels`, `vaul`, `video-react`
- devDependencies: `@types/lodash`, `@types/unist`, `@types/video-react`

## Batch 4：报告导出去重（F 组）

- 合并近 1700 行的 `pages/AgentAudit/components/ReportExportDialog.tsx`（1885 行）到 `components/reports/AgentReportExportDialog.tsx`（1878 行）
- 唯一差异（VerificationStatusBreakdown 面板）合入 canonical 版本
- `pages/AgentAudit/index.tsx` 改导入路径
- `pages/AgentAudit/components/index.ts` 移除 barrel export
- 结果：主 index bundle 从 649 KB → 608 KB

## Batch 5：后端死代码（B 组）

**删除文件**：

- `backend/app/services/controls/__init__.py`（全模块）
- `backend/app/services/controls/config_loader.py`（172 行；`SecurityControlsConfigLoader` 唯一定义处无调用）
- `backend/app/models/security_control.py`（4 个 ORM: `SecurityControlModel`、`SensitiveOperationModel`、`OperationRequiredControlModel`、`LanguageAdapterModel`；除 `models/__init__.py` re-export 外零引用）
- `backend/app/models/coverage.py`（`CoverageTrack` ORM；实际覆盖率在 `services/agent/core/coverage.py`）
- `backend/app/services/agent/tools/kunlun_tool.py`（`KunlunMTool`/`KunlunRuleListTool`/`KunlunPluginTool`；`_build_tools` 中未实例化、`Kunlun-M-master/` 目录不存在）

**新增文件**：

- `backend/alembic/versions/023_drop_dead_tables.py`（drop 5 张空表）

**修改文件**：

- `backend/app/models/__init__.py` 移除死 ORM re-export
- `backend/app/services/agent/tools/__init__.py` 移除 Kunlun 三个类的 import + `__all__`
- `backend/app/services/agent/tools/base.py` 移除 `get_langchain_tool()` 方法（唯一定义处无调用者）
- `backend/app/services/agent/agents/base.py` 移除 `kunlun_scan: 180` 超时字典条目
- `backend/app/services/agent/config.py` 移除 `kunlun_enabled` / `kunlun_timeout_seconds` 配置，`safety_check` key 改为 `safety_scan`
- `backend/app/core/config.py` 移除 `CONTROLS_CONFIG_DIR` / `CONTROLS_ENABLED` / `KB_DOCUMENTS_PATH`
- `backend/app/services/agent/agents/analysis.py` / `recon.py` / `prompts/system_prompts.py` / `AGENTS.md` 移除 `kunlun_scan` prompt 描述

## Batch 6：后端依赖（C 组）

**pyproject.toml 删除 19 个 dependencies**：

- `sse-starlette`, `reportlab`, `aiofiles`, `pygments`, `bandit`
- `langchain`, `langchain-community`, `langchain-openai`, `langgraph`
- `pyjsparser`, `phply`, `esprima`, `jsbeautifier`, `colorlog`
- `portalocker`, `prettytable`, `rarfile`, `beautifulsoup4`, `django`

**pyproject.toml 删除 optional-dependencies**：

- `mysql`（Kunlun-M web 模式绑定）
- `docs`（仓库无 mkdocs.yml）

**pyproject.toml 显式补充**：

- `pyyaml>=6.0`（原为 chromadb/langchain 传递依赖，清理后需显式）

## 保留但值得警惕的依赖

以下依赖静态扫描无 import，但**属于运行时反射/基础设施包，必须保留**：

- `asyncpg`, `greenlet`, `alembic`, `uvicorn` —— DATABASE_URL 反射加载
- `email-validator`, `python-multipart` —— pydantic / FastAPI 反射
- `bcrypt` —— passlib[bcrypt] 拉但独立声明避免版本漂移
- `pygments` **已删除** —— 经 rg 复核无 subprocess/内部 import
- `tree-sitter` —— tree_sitter_language_pack 传递依赖必须显式声明
- `tailwindcss-animate` —— tailwind.config.js `require()` 使用

## 验证记录

- ✅ `python -m compileall backend/app` 全通过（exit 0）
- ✅ 三个 `docker-compose*.yml` YAML 语法校验通过
- ✅ `pnpm build`（Batch 2/3/4 后各一次）产物写出成功
- ⚠️ `uv run pytest` 未在本地跑（沙箱进程无 uv，需老板本地或 docker 内验证）
- ⚠️ `alembic upgrade head` 未跑（远端 CI 或部署前必跑）

## 下一步（老板确认后）

1. `git push origin codex/repo-cleanup-20260720`
2. 通过 GitHub 开 PR 到 `main`
3. PR 合并后，老板本地终端执行 `openspec archive repo-cleanup-init` 归档变更
