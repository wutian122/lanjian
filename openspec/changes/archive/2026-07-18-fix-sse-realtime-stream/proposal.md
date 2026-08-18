## Why

Agent 审计任务运行时，前端 `/agent-audit/:taskId` 页面的 SSE 实时数据流会中途断开，用户看到"任务卡死"。生产日志（192.168.238.11 · lanjian-backend-1）实测：8 次 Semgrep prescan **无一例外**耗时 65–171 秒（平均 96s，方差 2.6×），全部超过前端 45s 心跳窗口；且期间事件队列入队为 0，实锤 `subprocess.run` 冻结事件循环。同时观察到 5000 条事件 DB 回补丢失、生产环境仍在跑 `uvicorn --reload`、`_running_event_managers` 内存字典跨进程不共享等多处系统性隐患。断流破坏"实时可观测审计"这一核心承诺，且触发 stale running 任务（前端红色恢复横幅还因 `canRecover && !isRunning` 恒 false 而不显示）。

## What Changes

**P0 部署 & 数据丢失兜底**
- **BREAKING**（部署侧）：生产 `docker-compose.prod.yml` 后端启动去除 `--reload`，改用 `--workers 1`；开发用 `docker-compose.override.yml` 保留 reload
- `event_manager.stream_events` DB 回补支持游标分页（当前 `limit=500` 会静默丢事件）
- `_SSE_TERMINAL_STATUSES` 补齐 `completed_with_gaps`，`initializing` 明确为非终态

**P1 SSE 稳定性核心修复**
- `_run_semgrep_prescan` 改异步子进程：`subprocess.run` → `asyncio.create_subprocess_exec` + `communicate()`，每个规则集包 `tool_call_start`/`tool_call_end` 事件让前端进入 180s 长操作心跳窗口
- 修 `agent_tasks.py` 超时路径 `event_emitter.emit_event('warning', ...)` AttributeError（`AgentEventEmitter` 无此方法，应为 `emit_warning`）
- 取消路径补 `emit_task_cancelled`：`cancel_agent_task` 和 `_execute_agent_task` 的 `CancelledError` 分支
- 两个 SSE 端点接受 `request: Request` 参数并检测客户端断开
- `stream_events` 显式捕获 `asyncio.CancelledError` 并优雅退出

**P1 前端配合**
- `useResilientStream` disconnect 时**不清零** `latestSeenSequenceRef`（保留高水位，重连不重放老事件）
- `hasConnectedRef` cleanup 时复位，让 StrictMode 双挂载和运行时断开都能自愈重连
- `parseSSE` 支持 `id:` 字段用于 Last-Event-ID

**P2 架构增强**
- 事件队列改有界 `asyncio.Queue(maxsize=10000)`，`thinking_token` 类型在队列满时支持丢弃策略，避免慢消费者背压主循环
- **BREAKING**（内部 API）：`_running_event_managers` / `_running_orchestrators` 状态迁移到 Redis（跨 worker 与 `--reload` 后仍能续跑）；后端下发 `orchestrator_alive` 字段供前端判定 stale running
- 心跳发送改独立协程（周期 10s），与队列消费循环解耦
- Alembic 迁移：`agent_events` 表加 `sse_last_id` 字符串列（可选，用于 Last-Event-ID 语义）
- 前端 `useAgentAuditState` 增加 `RECONNECT_ATTEMPT` / `SSE_STREAM_DIED` Action；`canRecover / isRunning` 用后端 `orchestrator_alive` 字段判定

## Capabilities

### New Capabilities
（无新增能力，本次为现有能力的稳定性与架构修复）

### Modified Capabilities
- `audit-engine`: Orchestrator 长耗时同步操作（Semgrep prescan）SHALL 不冻结事件循环；覆盖率放行完成状态 SHALL 与 SSE 终态集合一致；任务取消路径 SHALL 发出终端事件
- `sse-realtime-stream`（新增 spec 文件）：SSE 端点心跳、DB 回补分页、事件队列有界、客户端断开检测、Last-Event-ID 语义、跨进程存活状态

## Impact

**代码**
- 后端：`app/services/agent/event_manager.py`、`app/services/agent/agents/orchestrator.py`、`app/services/agent/agents/base.py`、`app/api/v1/endpoints/agent_tasks.py`、`app/core/redis.py`（新增 `orchestrator_registry` 模块）
- 前端：`src/pages/AgentAudit/hooks/useResilientStream.ts`、`src/pages/AgentAudit/hooks/useAgentAuditState.ts`、`src/pages/AgentAudit/index.tsx`、`src/pages/AgentAudit/types.ts`
- 部署：`docker-compose.prod.yml`、`docker-compose.override.yml`
- 迁移：`backend/alembic/versions/022_sse_last_id.py`

**API / 契约**
- SSE 端点响应新增 `id:` 字段（Last-Event-ID）；`data:` payload 新增 `orchestrator_alive` 字段供前端判定 stale running
- SSE 事件类型集合稳定，`AgentEvent.sequence` 保持单调递增
- 内部：`_running_event_managers` / `_running_orchestrators` 从进程内字典迁移到 Redis 键空间 `lanjian:orch:*`

**测试**
- 新增：`test_semgrep_prescan_yields.py`、`test_sse_reconnect_backfill.py`、`test_event_queue_bounded.py`、`test_orchestrator_registry_redis.py`
- 前端新增：`useResilientStream.reconnect.test.ts`、`useAgentAuditState.reconnect.test.ts`
- E2E：真实审计任务复跑，验证 15+ 分钟无 SSE 断开

**运维**
- 生产必须重新执行 `docker compose up -d --force-recreate backend` 使去 `--reload` 生效
- Redis 内存占用少量增加（每任务约 KB 级）

**兼容性**
- 前端旧客户端不解析 `id:` 字段不影响功能（Last-Event-ID 是渐进增强）
- 后端旧版任务读取仍可继续（Redis 缺失时 fallback 到进程内字典）
