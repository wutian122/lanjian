# SSE 实时数据流断开修复 —— 交付日志

**日期**：2026-07-18
**分支**：`fix-sse-realtime-stream`（worktree: `E:/lanjian-worktrees/sse-fix`）
**归档变更**：`openspec/changes/archive/2026-07-18-fix-sse-realtime-stream/`

---

## 现象

Agent 审计任务运行时前端 SSE 实时数据流会中途断开，用户看到"任务卡住"，刷新页面可继续。老板报告"卡在正在构建代码向量索引... 75%"。

## 根因

多重 bug 交织，Post-Wave 2 深挖发现：

1. **`useResilientStream.ts` config 非稳定引用**（`const config = { ...DEFAULT, ...userConfig }`）—— 每次 rerender 生成新对象，级联触发下游 useCallback identity 变化
2. **`index.tsx` stream useEffect 依赖 connectStream/disconnectStream** —— 引用变化触发 cleanup + 重连循环
3. **Wave 1 §2.4 `await request.is_disconnected()` 竞争 ASGI receive channel** —— 与 Starlette 内建 listen_for_disconnect 冲突，放大重连问题
4. **`_run_semgrep_prescan` 同步 `subprocess.run`**（Wave 1 前）冻结事件循环 65-171 秒

## 交付内容

### 分层修复（8 个 commit，+1200/-100 行）

| Wave | Commit | 内容 |
|---|---|---|
| 规格 | `de28627` | OpenSpec change 骨架（proposal + design + specs delta + tasks） |
| WIP 快照 | `7f3ccd0` | 保存基线（init-progress / re-audit / sandbox-attempts 未完成工作） |
| Wave 0 | `61a89bc` + `6baeea4` | 部署 & 数据丢失兜底：compose 拆分 --reload / DB 回补分页 / SSE 终态集合补齐 |
| Wave 1 | `c96d32d` + `f57eecb` | Semgrep 异步化 / emit_event AttributeError / task_cancelled / SSE id 字段 / 前端 hook 生命周期 / InitProgress 自动刷新 |
| Wave 2 | `5cc311d` + `9562fee` | Redis Registry / 有界队列 / 心跳独立协程 / 前端 reducer + orchestrator_alive |
| PostFix | `1af68d3` | 根因修复：useMemo 稳定 config + ref 引用 connect/disconnect + 删除 is_disconnected 竞争 |

### 修改文件（累计）

**后端**（8 个文件）：
- `backend/app/services/agent/event_manager.py` —— DB 回补分页 / 有界队列 / 分级丢弃 / 心跳独立协程 / CancelledError 捕获
- `backend/app/api/v1/endpoints/agent_tasks.py` —— 3 处 is_disconnected 删除 / emit_warning 修复 / emit_task_cancelled / `orchestrator_alive` 字段
- `backend/app/services/agent/agents/orchestrator.py` —— Semgrep 异步化 + tool_call 事件包裹 / `_pump_orchestrator_alive` 心跳协程
- `backend/app/services/agent/core/orchestrator_registry.py` —— **新增** Redis-backed 存活状态注册表
- `backend/app/models/agent_task.py` —— `AgentEvent.sse_last_id` 字段
- `backend/alembic/versions/022_sse_last_id.py` —— **新增** 迁移
- 3 个 docker-compose*.yml —— 拆分 `--reload`

**前端**（4 个文件）：
- `frontend/src/pages/AgentAudit/hooks/useResilientStream.ts` —— config useMemo 稳定化 / Last-Event-ID header / id 字段解析 / 不清零 sequence 高水位
- `frontend/src/pages/AgentAudit/index.tsx` —— connectStreamRef / stream useEffect 用 ref / phase_start 触发 loadTask / hasConnectedRef cleanup 复位 / 恢复横幅逻辑修复
- `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts` —— RECONNECT_ATTEMPT / SSE_STREAM_DIED reducer / canRecover 用 orchestrator_alive
- `frontend/src/pages/AgentAudit/types.ts` + `frontend/src/shared/api/agentTasks.ts` —— Action 联合 + AgentTask.orchestrator_alive

**测试**（10 个文件全部新增，容器内 42/42 通过）：
- test_prod_compose_no_reload / test_sse_terminal_statuses / test_sse_reconnect_backfill / test_semgrep_prescan_yields / test_emit_event_attribute_bug / test_cancel_emits_task_cancel / test_sse_endpoint_hardening / test_orchestrator_registry_redis / test_event_queue_bounded_and_heartbeat / test_sse_no_manual_disconnect_check

## 部署验证

**生产环境**（192.168.238.11）：
- backend 容器：`--workers 1`（无 --reload）✅
- 4 容器全部 Up + healthy ✅
- 数据库 `alembic_version = 022_sli`（sse_last_id 列已建）✅
- Redis registry keys 就绪：`lanjian:orch:{task_id}`
- frontend 新 bundle（Wave 2 → PostFix 重新 build 两次）

**备份**（可回滚）：
- `/root/lanjian-backups/pre-sse-fix-20260718/`
- `/root/lanjian-backups/wave1-pre-deploy-20260718/`
- `/root/lanjian-backups/wave2-pre-deploy-20260718/`
- `/root/lanjian-backups/postfix-pre-deploy-20260718/`

## Spec 变更

- `openspec/specs/audit-engine/spec.md`：+3 requirement / ~1 modified
  - 长耗时同步操作不冻结事件循环
  - 取消路径发出终态事件
  - 超时路径用正确 emitter 方法
  - MODIFIED: 覆盖率放行含 task_timeout reason

- `openspec/specs/sse-realtime-stream/spec.md`（新增 spec，10 requirement）
  - SSE 端点集合与响应头
  - SSE 事件带 id 字段
  - 心跳独立协程 10s
  - DB 回补游标分页
  - 事件队列有界 + 分级丢弃
  - SSE 终态状态集合完整
  - SSE 端点感知客户端断开
  - 跨进程 Orchestrator 存活状态
  - 前端 useResilientStream 生命周期与自愈
  - 前端状态判定 stale running 用后端字段

## 后续可选优化（已识别但本次未做）

- Wave 2 Review Finding 3：`streamDied` reducer 状态目前 dead（reducer 写但 UI 不消费）。可作为埋点或用于连接失败横幅
- Wave 2 Review Finding 5：`get_registry()` 首次调用建 Redis 连接引入 10-50ms 延迟。可 FastAPI startup 预热
- test_semgrep_prescan_yields.py 的 `test_prescan_does_not_block_event_loop` mock 不完整（`OrchestratorAgent` 缺 config 属性），需完善 fixture
- 5 个 pre-existing 测试失败（token_budget 配置默认值不匹配 / sandbox mock 缺属性）与本次修复无关
