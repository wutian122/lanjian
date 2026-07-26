# Proposal: Repository Cleanup and Documentation Refresh (v3.6.0)

## Background

仓库经过 3.5.0 系列多轮功能迭代后，累积了以下问题：

1. 顶层目录散落一次性交付日志、备份日志和过时快照（`audit_remote.log`、`e2e_test_report.md`、`CHANGES-sse-realtime-stream.md`、`docs/deleted-paths.txt` 等）。
2. 后端存在明显死代码路径：`services/controls/`、`models/security_control.py`（4 个 ORM）、`models/coverage.py` (`CoverageTrack`)、`services/agent/tools/kunlun_tool.py` (3 个 Kunlun-M 工具类)、`AgentTool.get_langchain_tool()` 方法均无任何调用方。
3. `pyproject.toml` 声明的第三方依赖中 9 个包在真实代码中无 import，且不是运行时反射依赖：`aiofiles`、`sse-starlette`、`langchain*`、`langgraph`、`django`（知识库示例字符串误标）、`reportlab`、`bandit`、`pygments`、以及 Kunlun-M 传递依赖 `pyjsparser`/`phply`/`esprima`/`jsbeautifier`/`colorlog`/`portalocker`/`prettytable`/`rarfile`/`beautifulsoup4`。
4. `frontend/package.json` 声明的 11 个依赖在 `src/` 中无 import：`@supabase/supabase-js`、`@google/generative-ai`、`miaoda-*`、`streamdown`、`fflate`、`eventsource-parser`、`i18next` 全家、`ky`、`zod`、`date-fns`、`@radix-ui/react-icons`。
5. 前端 `components/ui/` 存在 11 个未被上层引用的 shadcn 备用组件。
6. 存在两个近 1700 行的报告导出组件（`AgentReportExportDialog.tsx` 与 `ReportExportDialog.tsx`）近似重复。
7. `README.md` 与真实仓库结构、脚本、依赖不同步。

## Goal

- 保留仓库核心运行必需的有效文件与代码逻辑
- 消除所有确认无引用的死代码、死配置、死依赖
- README.md 与清理后的项目状态完全匹配
- 不破坏任何现有运行时行为（构建、测试、docker compose 启动路径必须继续可用）

## Non-goals

- 不重构现有正确工作的代码逻辑（除 F 组的报告导出去重）
- 不改动 Alembic 已归档的历史迁移（只追加新迁移 `023_drop_dead_tables.py`）
- 不改动 OpenSpec 已归档变更 `archive/2026-07-18-fix-sse-realtime-stream/`
- 不改动服务器 `192.168.238.11` 上的部署（那部分走独立的镜像发布流程）

## Scope

仓库根 + `backend/` + `frontend/` + `docker*` + `docs/` + `openspec/`；范围详见 `tasks.md` 的分批清单。

## Success criteria

- `uv sync` 成功且 `uv run pytest` 通过（或明确报告哪些测试因清理需要更新）
- `pnpm install && pnpm build` 成功
- `docker compose config` 语法校验通过
- 每一项被删除的文件/依赖在 `DELETIONS.md` 有条目和原因
- README.md 内容与清理后代码/脚本/目录一一对应

## Risks

- **风险 R1**：某依赖被字符串/反射间接加载但未被静态发现 → 缓解：分批清理，每批清完立即 `uv sync` + 冒烟。
- **风险 R2**：Alembic 旧迁移 009 仍在链上，drop 表迁移必须放在链尾且与 `023` 编号一致 → 缓解：先跑 `alembic heads` 定位 head，再新增。
- **风险 R3**：`docker cp` 后远端容器与镜像不一致 → 缓解：本次清理仅动本地仓库，不修改远端；镜像发布另走 P2-A `docker compose build --pull` 流程。
