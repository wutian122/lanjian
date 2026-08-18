## Context

蓝鉴（lanjian）SSE 实时数据流在中大型项目审计任务运行时会中途断开。生产日志实测 8 次 Semgrep prescan 全部 65–171 秒（平均 96s），期间 asyncio 事件循环被 `subprocess.run` 冻结，事件队列入队为 0，前端 45s 心跳超时触发断连，5 次指数退避重连全部失败（因事件循环仍卡住），前端 `hasConnectedRef` 泄漏后不会自愈。此外还发现：

- 生产 `docker-compose.prod.yml` 后端启动带 `--reload`（生产不应有此配置，任何文件变更会硬重启，SSE 全断 + 内存 orchestrator 丢失）
- `event_manager.stream_events` DB 回补 `limit=500`，实测已出现 `skipped 5000` 的静默事件丢失
- `_running_event_managers` / `_running_orchestrators` 用进程内 `dict`，`--reload` 或多 worker 部署下 stale running 任务无法恢复
- 无界 `asyncio.Queue(maxsize=0)` + `await q.put()` 背压，慢消费者会导致内存长期占用
- `_SSE_TERMINAL_STATUSES` 缺 `completed_with_gaps`，前端刷新后一直显示"运行中"直到 300s max_idle
- `agent_tasks.py:726` `emit_event('warning', ...)` 是 AttributeError（`AgentEventEmitter` 无此方法），超时路径必炸
- 前端 `useResilientStream` disconnect 时清零 `latestSeenSequenceRef`，重连重放大量老事件
- 前端 `canRecover && !isRunning` 恒为 false（`canRecover` 要 status='running'，`isRunning` 也含 'running'），恢复横幅永远不显示

**约束**：
- 后端 Python 3.11+ / FastAPI async / asyncio 单事件循环
- 前端 React 18 StrictMode 开发模式（cleanup 会双执行）
- 部署 Docker Compose，Redis 已启用（`lanjian-redis-1` healthy）
- 数据库 PostgreSQL 15，Alembic 迁移
- 需保持向前兼容：旧客户端不支持 `id:` 字段应继续工作

**利益方**：审计任务操作员（老板）、AI Agent 运维、二次开发者。

## Goals / Non-Goals

**Goals**
- SSE 实时数据流在 Semgrep prescan / RAG 索引 / 长 LLM 调用等 60+ 秒操作期间**不断开**
- 客户端网络抖动断线后能自愈重连并从正确 sequence 续传，**不丢事件**（DB 回补覆盖）
- 后端进程重启 / `--reload` 后 stale running 任务可被前端明确识别并触发 `/recover`
- 事件队列内存占用有上限，慢消费者不会拖垮服务端
- 生产部署配置正确，无 `--reload`
- 修 `emit_event` AttributeError，取消路径能发终态事件

**Non-Goals**
- 不修改 Semgrep 规则集内容 / 覆盖率维度定义
- 不重构 orchestrator ReAct 主循环逻辑
- 不引入 SSE 之外的实时传输通道（WebSocket / gRPC 不在本次范围）
- 不改动 findings 落库结构（除了 sse_last_id 一个可选列）
- 不重写 RBAC、认证、LLM 适配器

## Decisions

### D1. Semgrep prescan 从同步 subprocess.run 迁移到 asyncio.create_subprocess_exec

**选择**：`asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE, env=clean_env, cwd=project_root)` + `await proc.communicate(timeout=180)`。

**理由**：
- 生产实测同步 `subprocess.run` 是根因：event loop 冻结期间无心跳、无事件、无重连
- `asyncio.to_thread(subprocess.run, ...)` 虽可绕过 event loop 阻塞，但线程池阻塞后无法取消底层进程
- 原生 asyncio subprocess 支持 `proc.kill()` 精确取消，且 stdout/stderr 支持流式读取（未来可对接进度事件）

**替代方案**：
- `asyncio.to_thread(subprocess.run)` — 被否，缺乏取消能力
- 保留 subprocess 但每规则集前 `await asyncio.sleep(0)` — 无效，`subprocess.run` 内部循环不 yield

**辅助措施**：每个规则集前后包 `tool_call_start`/`tool_call_end` 事件（`tool_name='semgrep_prescan_<ruleset>'`），使前端 `useResilientStream` 进入 `longOperationHeartbeatTimeout=180s` 模式（`resilientStreamPolicy.ts:19-22`）。

### D2. DB 回补改游标分页

**选择**：`stream_events` DB 回补循环拉取 `WHERE task_id=? AND sequence > cursor ORDER BY sequence LIMIT 500`，直到返回空或超过 `max_backfill_events=20000` 上限。

**理由**：
- 实测出现 `skipped 5000` — 5000 条事件永久对客户端不可见
- 分页游标推进直到没有更多事件，是最简单且正确的方案
- 20000 上限保护极端异常任务

**替代**：
- 单次拉全部 — 大任务下可能拖 DB 或 OOM
- 用 `AgentEvent.id` 而非 `sequence` 分页 — sequence 已单调递增且是主查询字段，不需要改

### D3. 事件队列改有界 + 分级丢弃策略

**选择**：`asyncio.Queue(maxsize=10000)`。生产者用 `try: queue.put_nowait(evt) except QueueFull:` 走分级策略：
- `thinking_token`：直接丢弃并计数（无关键语义，纯 UX 增强）
- `tool_result` / `tool_call`：等待最多 5s（`await asyncio.wait_for(queue.put(evt), 5)`）；超时后落 DB 但不入队（DB 回补兜底）
- 终态事件（`task_complete` / `task_error` / `task_cancel`）：**始终** `await queue.put()` 直到成功，保证前端能感知任务结束

**理由**：
- 无界队列在客户端离线时无限增长，实测断连 15 分钟就积 5780 条
- thinking_token 丢弃对前端只是流式效果打折，不影响正确性
- 终态事件强制入队保证前端不会永远等下去

**替代**：全部丢弃 — 否，会丢关键事件；全部阻塞 — 否，退回背压。

### D4. `_running_*` 状态迁移到 Redis

**选择**：新增 `app/services/agent/core/orchestrator_registry.py`，把 `_running_event_managers` / `_running_orchestrators` / `_running_asyncio_tasks` 三个模块级 dict 抽象成 `OrchestratorRegistry`。默认实现走 Redis 键空间 `lanjian:orch:{task_id}`，字段包含：
- `alive_at`：秒级 unix ts，Orchestrator 主循环每 5 秒刷新（TTL 60s）
- `worker_id`：进程 uuid + hostname，用于跨 worker 识别
- `event_manager_ref`：本进程 event_manager 是否存活（true/false）

**理由**：
- `--workers >1` 或 `--reload` 后进程内 dict 无用；Redis 已在生产 healthy
- `alive_at` + TTL 天然解决 stale running：Redis key 过期 = orchestrator 死亡
- 前端可通过后端 `/tasks/{id}` 响应中的 `orchestrator_alive` 布尔值判断 stale running

**替代**：
- PostgreSQL 心跳表 — 写入频率过高（每 5s），Redis 更合适
- ZooKeeper / etcd — 引入新依赖，Redis 已在栈内

**Fallback**：Redis 不可用时降级到进程内 dict（现状），日志告警。

### D5. 心跳独立协程

**选择**：`stream_events` 拆两个协程：
- `_pump_events`：从 queue 拉事件并 yield 给客户端
- `_pump_heartbeats`：每 10s 无条件 yield `heartbeat` 事件

两协程用 `asyncio.wait({...}, return_when=FIRST_COMPLETED)` 复用同一个 `yield` 出口。

**理由**：
- 当前实现 `asyncio.wait_for(queue.get(), timeout=15)` 若客户端消费慢，`get()` 满足前无法发心跳；分开协程后心跳独立于队列
- 10s 心跳仍在前端 45s 窗口内（4.5× 余量）

**替代**：keep-alive 注释行（`: heartbeat\n\n`）— 简单但不能触发前端 `handleEvent`，无法重置 `latestSeenSequenceRef`。

### D6. SSE 端点 request.is_disconnected + CancelledError 捕获

**选择**：
- `stream_agent_events` / `stream_agent_with_thinking` 端点签名接 `request: Request`
- 生成器主循环里用 `asyncio.wait({queue_get_task, disconnect_check_task}, FIRST_COMPLETED)` 模式
- `stream_events` 显式 `except asyncio.CancelledError: logger.info(...); raise` 让 Starlette 正常清理

**理由**：不改造无法及时释放 event_manager 引用，会拖着队列不清；`CancelledError` 是 `BaseException` 不被现有 `except Exception` 捕获。

### D7. 前端 hasConnectedRef 生命周期

**选择**：`useEffect` cleanup 里 `hasConnectedRef.current = false;`；StrictMode 双挂载时第二次 mount 能看到 false 并重连。

**理由**：目前一旦断了永远不自愈；单元测试可覆盖。

**风险 → 缓解**：cleanup 会在真正 unmount 时也复位，但后续挂载会正常连接，无副作用。

### D8. 前端 latestSeenSequenceRef 不清零

**选择**：删除 `disconnectInternal` 中 `latestSeenSequenceRef.current = 0`。

**理由**：清零后 `getEffectiveAfterSequence` 使用 `streamOptions.afterSequence`，可能回到很久以前，导致大量重放 + 日志跳跃。保留水位可正确续传。

### D9. `_SSE_TERMINAL_STATUSES` 补齐

**选择**：加入 `"completed_with_gaps"`；`"initializing"` 不入终态集合。

### D10. 修 emit_event AttributeError

**选择**：`agent_tasks.py:726` 改为 `await event_emitter.emit_warning(...)`；同时把 `_execute_agent_task` 的 `CancelledError` 分支补 `await event_emitter.emit_task_cancelled(...)`。

### D11. Last-Event-ID 语义

**选择**：SSE 端点为每条事件写 `id: {sequence}\n`；前端 `parseSSE` 识别 `id:` 并 `EventSource`-兼容语义（但仍用 `fetch` + Reader）；断连重连时通过 `Last-Event-ID` header 传上次看到的 sequence。

**理由**：属于 SSE 标准，双方低成本升级；对旧客户端不影响（不解析 id 也能用）。

## Risks / Trade-offs

- **Semgrep 异步化后总耗时不变** → 前端仍会看到 60~180s 的"长工具执行中"心跳。方案：`tool_call_start` 事件带 `tool_input.eta_seconds` 让前端展示进度条估计。
- **Redis 依赖变强** → 缓解：fallback 到进程内 dict + 告警日志。
- **有界队列 + thinking_token 丢弃** → 断连恢复时用户看不到断连期间的流式打字效果（DB 不存 thinking_token）。缓解：文档说明；重连回补时用 `[断连期间已省略 N 条流式思考]` 提示。
- **状态迁移到 Redis 的迁移期兼容** → `OrchestratorRegistry` 双写一段时间（进程内 dict + Redis），Wave 2 全面切换后再删。
- **前端 `hasConnectedRef` cleanup 复位** → 若 cleanup 执行完后同一个 taskId 立即又触发 mount，可能连续建两条 SSE。缓解：`connectInternal` 内部用 `abortControllerRef` 已能保证只有一个活跃流。
- **Docker `--reload` 关闭后开发者热更需重启** → 缓解：`docker-compose.override.yml` 显式保留 `--reload`；生产 `docker-compose.prod.yml` 不包含 override。

## Migration Plan

按 Wave 独立可上线：

**Wave 0（P0 · 半天，可当天上线）**
1. 改 `docker-compose.prod.yml` / `docker-compose.override.yml`
2. `event_manager.stream_events` DB 回补分页
3. `_SSE_TERMINAL_STATUSES` 补齐
4. verification-loop 独立验证
5. 上线：`docker compose -f docker-compose.prod.yml up -d --force-recreate backend`
6. 观察 30 分钟：`docker logs -f lanjian-backend-1` 无 `--reload` 迹象

**Wave 1（P1 · 2 天）**
1. Semgrep 异步化 + tool_call 事件（后端）
2. 修 `emit_event` AttributeError（后端）
3. 取消路径 emit_task_cancelled（后端）
4. SSE 端点 is_disconnected + CancelledError（后端）
5. 前端 `hasConnectedRef` / `latestSeenSequenceRef` / Last-Event-ID
6. verification-loop + `/orch-review` ≤ 3 轮
7. 上线 + 观察 24 小时（真实审计任务复跑）

**Wave 2（P2 · 3 天）**
1. Alembic 迁移 022_sse_last_id
2. `OrchestratorRegistry` Redis 实现 + fallback
3. 事件队列有界 + 分级丢弃
4. 心跳独立协程
5. 前端 reducer Action 补齐 + `canRecover` 用后端字段
6. verification-loop + 最终 `/orch-review`
7. 上线 + E2E 长时任务验证

**回滚策略**：每波都是独立 git commit + 独立 compose 重启，可 `docker compose rollback` 到前一 tag。

## Open Questions

1. **旧任务 `_running_*` 迁移期长度**：双写多久后可下线进程内 dict？建议 Wave 2 上线后观察 48 小时。
2. **thinking_token 丢弃是否需要前端主动请求补齐**：Wave 2 是否加"点击补齐 thinking"按钮？先不做，观察用户反馈。
3. **Last-Event-ID 是否需要 HTTP 标准 header 名（`Last-Event-ID`）而不是 URL 参数 `after_sequence`**：Wave 2 讨论。先保留现有 `after_sequence` 查询参数（兼容），header 作为可选增强。
4. **Semgrep prescan 是否可以完全跳过**（当项目文件数 > N 时改由子 Agent 按需调 semgrep 工具）：不在本次范围。
