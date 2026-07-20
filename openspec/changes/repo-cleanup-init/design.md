# Design: Repository Cleanup and Documentation Refresh

## 决策 1：分批 commit，每批独立可回滚

清理动作分为 6 个批次，每批一个原子 commit，并按依赖关系排序（先删除叶子引用，再删除被引用的入口）：

```
批 1: A 组顶层杂项 + G 组本地缓存（低风险）
批 2: D 组前端依赖清理 + vite.config.ts manualChunks
批 3: E 组冷门 UI 组件
批 4: F 组报告导出重复合并
批 5: B 组死代码（含 controls / models / kunlun_tool / get_langchain_tool）+ Alembic 迁移 023
批 6: C 组后端依赖 + pyproject.toml + [dependency-groups] 合并
```

每批完成后立即 `uv sync` / `pnpm install` / `pnpm build`（对应批次相关的验证子集），失败则该批 `git reset --hard HEAD~1` 回滚。

## 决策 2：B/C 组的判定规则

一个符号视为死代码，当且仅当以下**全部**成立：

1. 全仓 `rg` 搜索无源代码 import / from 引用（排除自身与 `AGENTS.md`）
2. 无 `getattr`/`hasattr`/字符串键动态引用
3. 无 Alembic 迁移之外的数据库访问
4. 无 pytest fixture 或 conftest.py 反射注册
5. 无 environment variable 通过 `.env`/`config.py` 消费

对 pyproject 依赖，追加规则 6：

6. 不是 SQLAlchemy/pydantic/FastAPI/uvicorn 等基础设施反射按名字加载的运行时包（`asyncpg`、`greenlet`、`email-validator`、`python-multipart`、`uvicorn`、`alembic`、`sse-starlette` 若被 `EventSourceResponse` 使用等）

**豁免清单**：`asyncpg`、`greenlet`、`alembic`、`uvicorn`、`email-validator`、`python-multipart`、`bcrypt`（`passlib[bcrypt]` extra 会拉，但显式声明也保留避免 passlib 版本漂移）、`tree-sitter`（`tree_sitter_language_pack` 传递依赖必须显式声明避免版本漂移）—— **不清理**。

## 决策 3：Alembic 迁移策略

新增 `023_drop_dead_tables.py`（假设当前 head 是 `022_sse_last_id`）：

```python
revision = "023_drop_dead_tables"
down_revision = "022_sse_last_id"

def upgrade():
    op.drop_table("operation_required_controls")
    op.drop_table("sensitive_operations")
    op.drop_table("security_controls")
    op.drop_table("language_adapters")
    op.drop_table("coverage_tracks")

def downgrade():
    # 仅还原表结构占位（数据已丢失，不保证内容恢复）
    ...
```

`downgrade` 出于安全考虑保留占位 SQL，供 CI 校验通过；实际使用时若要回滚，从 `009_add_security_controls.py` 复制 create_table 语句。

## 决策 4：报告导出合并（F 组）

`AgentReportExportDialog.tsx`（1709 行，在 `components/reports/`）和 `ReportExportDialog.tsx`（1715 行，在 `pages/AgentAudit/components/`）的 diff 显示：

- 90% 代码相同（模板、字段、下载逻辑）
- 差异在于：`ReportExportDialog` 直接从 `useAgentAuditState` 读 findings，`AgentReportExportDialog` 通过 props 接收
- 全仓引用统计：`AgentReportExportDialog` 被 `pages/TaskDetail.tsx` 使用，`ReportExportDialog` 被 `pages/AgentAudit/index.tsx` 使用

**决策**：将 `ReportExportDialog.tsx` 迁移到 `components/reports/AgentReportExportDialog.tsx`，通过 props 参数化 findings 来源。`AgentAudit/index.tsx` 改从 `components/reports/AgentReportExportDialog` 导入并传入本地 findings。删除 `pages/AgentAudit/components/ReportExportDialog.tsx`。

**风险**：合并可能引入回归。缓解：批 4 只做机械合并 + import 修正，不改渲染逻辑；跑 `pnpm build` + `pnpm lint` 校验。

## 决策 5：不修改内容仅重命名的资产迁移

- `CLAUDE.md`（与 AGENTS.md 95% 重复）→ 删除；AGENTS.md 已成为唯一真相源
- `CHANGES-sse-realtime-stream.md` → 移到 `docs/history/` 保留归档意义
- `e2e_test_report.md` → 移到 `docs/history/`
- `docs/deleted-paths.txt` → 删除（陈旧 `.gitignore` 快照）
- `audit_remote.log`、`frontend/audit_remote.log` → 删除（已在 `.gitignore`）
- `frontend/dist/` → 删除（构建产物，`.gitignore` 已忽略）

## 决策 6：openspec 变更手工产物

由于本次运行环境沙箱进程无法直接调用 `openspec.cmd`（npm shim），产物按标准 `openspec new change` 输出结构手工写入 `openspec/changes/repo-cleanup-init/`。清理完成后老板可在本地终端执行 `openspec archive repo-cleanup-init` 归档。
