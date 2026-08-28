# sse-realtime-stream Specification

## Purpose
TBD - created by archiving change fix-sse-realtime-stream. Update Purpose after archive.
## Requirements
### Requirement: SSE 端点集合定义与响应头

后端 SHALL 在 `/api/v1/agent-tasks/{task_id}/events` 和 `/api/v1/agent-tasks/{task_id}/stream` 两个端点上提供 SSE 实时数据流。两端点 MUST 设置以下响应头以确保反向代理/浏览器不缓冲：

- `Content-Type: text/event-stream; charset=utf-8`
- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

两端点 MUST 接受 `after_sequence` 查询参数用于续传，且 SHALL 支持标准 `Last-Event-ID` 请求头（当同时存在时以两者最大值为准）。

#### Scenario: 响应头包含关键防缓冲字段
- **WHEN** 客户端 GET `/api/v1/agent-tasks/{id}/stream`
- **THEN** 响应头包含 `X-Accel-Buffering: no`、`Cache-Control: no-cache`、`Connection: keep-alive`、`Content-Type: text/event-stream`

#### Scenario: after_sequence 与 Last-Event-ID 取最大值
- **WHEN** 客户端请求 `?after_sequence=100` 且携带 `Last-Event-ID: 200` header
- **THEN** 后端从 sequence>200 开始回补

### Requirement: SSE 事件带 id 字段实现 Last-Event-ID 语义

每一条 SSE 事件（`heartbeat` 除外）MUST 包含 `id: {sequence}\n` 字段，使浏览器/客户端在重连时可通过标准 `Last-Event-ID` 请求头传递上次看到的 sequence。sequence MUST 与 `AgentEvent.sequence` 字段严格一致，且在同一 task_id 内单调递增（无跳号、无重复）。

#### Scenario: 事件包含 id 字段
- **WHEN** SSE 端点发送任意非心跳事件
- **THEN** 事件的 SSE 格式包含 `id: {sequence}\n` 行

#### Scenario: sequence 单调递增
- **WHEN** 同一 task_id 的两条连续事件被消费
- **THEN** 后一条的 sequence 大于前一条，且相邻事件间 sequence 差值不大于 1（除 DB 回补分页边界）

### Requirement: 心跳独立协程且 10 秒周期

`stream_events` MUST 用独立协程发送心跳，与队列消费循环解耦。心跳周期 SHALL 为 10 秒（比前端默认 45 秒窗口留 4.5× 余量，比长操作 180 秒窗口留 18× 余量）。心跳协程 MUST 不依赖队列 `get()` 完成，即使消费者极慢，心跳仍能按期发送。

心跳事件 SHALL 使用 `event: heartbeat\ndata: {"ts": ...}\n\n` 格式，MUST 不包含 `id:` 字段（心跳不占用 sequence 空间）。

#### Scenario: 消费者慢时心跳仍按周期发送
- **WHEN** 客户端消费速率低（假设 30 秒读一次），队列无新事件
- **THEN** 客户端仍能每 10 秒收到一次 `heartbeat` 事件

#### Scenario: 心跳不占用 sequence
- **WHEN** 心跳事件被发送
- **THEN** 事件不含 `id:` 字段，客户端不会因心跳更新 `latestSeenSequence`

### Requirement: DB 回补支持游标分页

`stream_events` 在进入实时循环前 SHALL 从数据库回补 `after_sequence` 之后的所有事件。回补 MUST 使用游标分页循环（每批 `LIMIT 500` 或配置值），推进游标直到返回空批次或达到 `max_backfill_events=20000` 保护上限。当前实现单次 `LIMIT 500` 的静默丢事件行为 MUST 被移除。

回补事件 MUST 保持原始 sequence 顺序发送给客户端，且 MUST 在事件的 SSE 格式中包含 `id:` 字段（与实时事件一致）。

#### Scenario: 断连期间 5000 条事件全部回补
- **WHEN** 客户端在任务运行中断线 15 分钟，期间后端队列积压 5000 条事件（全部已落 DB），客户端携带 `after_sequence=X` 重连
- **THEN** 回补循环拉取全部 5000 条事件（分 10 批，每批 500）并按 sequence 顺序发送，客户端连续接收无跳号

#### Scenario: 达到保护上限时截断
- **WHEN** 断连期间累积事件超过 `max_backfill_events=20000`
- **THEN** 回补最多发送 20000 条最新事件；后端日志 WARNING 记录被截断的事件数；发送一条 `event: notice\ndata: {"kind": "backfill_truncated", "sent": 20000, "limit": 20000, "after_sequence": N}` 提示客户端。客户端 SHALL 用 `after_sequence` 值继续拉取或提示用户"部分历史事件未回补"

### Requirement: 事件队列有界与分级丢弃

`EventManager._event_queues[task_id]` MUST 是 `asyncio.Queue(maxsize=10000)`。生产者 `add_event` 在队列满时 SHALL 按事件类型分级处理：

- **可丢弃**（`thinking_token`）：先按时间窗聚合（150ms 窗口或 accumulated 增量 ≥64 字符才发射一条，`thinking_end` 兜底全文），再 `queue.put_nowait`；`QueueFull` 时丢弃并累加 `dropped_thinking_tokens` 计数器（每 100 条打一条 WARNING 日志）
- **重要**（其他非终态事件如 `tool_call`, `tool_result`, `finding_new`）：`queue.put_nowait` 非阻塞；`QueueFull` 时跳过入队并累加 `dropped_important_events` 计数器、打 WARNING 日志，事件仍已落 DB（DB 回补兜底）。生产者 MUST NOT 因队列满而同步等待（2026-08-28 E2E 实证：旧实现 1493 条事件各等满 5s ≈ 124 分钟纯阻塞）
- **终态**（`task_complete`, `task_error`, `task_cancel`）：`await queue.put(evt)` 阻塞入队，MUST 直到成功（30s 超时保护，防止消费者永久离线挂死 orchestrator）

事件 MUST 在入队前完成 DB 落库（`thinking_token` 除外），确保 DB 回补路径永远有兜底数据。

#### Scenario: thinking_token 按时间窗聚合
- **WHEN** orchestrator 在 150ms 窗口内连续发出多条 `thinking_token` 且 accumulated 增量 <64 字符
- **THEN** 仅第一条入队，其余被聚合跳过（不入队、不计数）；前端最终由 `thinking_end` 事件获得完整文本

#### Scenario: thinking_token 在队列满时被丢弃
- **WHEN** 队列已满 10000 条，orchestrator 继续发出 `thinking_token`
- **THEN** 事件不入队，`dropped_thinking_tokens` 计数递增；每 100 条丢弃打一条 WARNING 日志

#### Scenario: tool_result 在队列满时非阻塞跳过
- **WHEN** 队列已满，orchestrator 发出 `tool_result`
- **THEN** 生产者立即返回（不等待），事件跳过入队但已落 DB（DB 回补时能拉到）；`dropped_important_events` 计数递增并打 WARNING 日志

#### Scenario: 终态事件强制入队
- **WHEN** 队列已满，任务完成 orchestrator 发出 `task_complete`
- **THEN** 生产者 `await queue.put(evt)` 阻塞直到入队成功；前端最终能收到 `task_complete` 事件

### Requirement: SSE 终态状态集合完整

`_SSE_TERMINAL_STATUSES` 集合 MUST 包含所有能终结任务的 `AgentTaskStatus`：`completed`, `completed_with_gaps`, `failed`, `cancelled`, `paused`。`initializing` MUST 不在此集合（属于运行中状态）。

DB 轮询流（`stream_agent_events`）在观察到 `task.status in _SSE_TERMINAL_STATUSES` 时 SHALL 发送 `task_end` 事件并关闭流；否则 SHALL 保持连接直到 `max_idle` 超时。

#### Scenario: completed_with_gaps 触发流关闭
- **WHEN** 任务以 `COMPLETED_WITH_GAPS` 结束，客户端刷新页面走 DB 轮询流
- **THEN** 流在检测到该状态后立即发送 `task_end` 事件并关闭连接，前端不用等 300 秒 max_idle

#### Scenario: initializing 不触发流关闭
- **WHEN** 任务处于 `INITIALIZING` 状态
- **THEN** DB 轮询流保持连接，不发送 task_end

### Requirement: SSE 端点感知客户端断开

两个 SSE 端点 MUST 接受 `request: fastapi.Request` 参数并在生成器主循环中通过 `asyncio.wait({queue_get_task, disconnect_check_task}, FIRST_COMPLETED)` 模式检测客户端断开。检测到断开后 SHALL 在 5 秒内退出生成器、释放 event_manager 引用、清理待发送任务。

`stream_events` MUST 显式捕获 `asyncio.CancelledError`（Starlette 内部取消或客户端断开会触发），记录 INFO 日志后重新 raise，让上层 Starlette 正常清理。

#### Scenario: 客户端断开时后端 5 秒内释放资源
- **WHEN** 客户端在实时循环中间关闭 fetch 连接（`AbortController.abort`）
- **THEN** 后端 `request.is_disconnected()` 返回 true，生成器在 5 秒内退出，`event_manager` 队列引用被释放

#### Scenario: CancelledError 被显式捕获
- **WHEN** SSE 生成器在 `await queue.get()` 时被 Starlette 取消
- **THEN** `except asyncio.CancelledError` 分支打 INFO 日志后 `raise`，不吞掉异常

### Requirement: 跨进程 Orchestrator 存活状态

后端 MUST 提供 `OrchestratorRegistry` 抽象，将当前进程内 `_running_event_managers` / `_running_orchestrators` / `_running_asyncio_tasks` 三个 dict 的语义迁移到共享存储（Redis）。字段 MUST 包含：

- `alive_at`（unix ts）：Orchestrator 主循环每 5 秒刷新，Redis TTL 60 秒
- `worker_id`（str）：`{hostname}:{pid}`，用于识别跨 worker 场景
- `event_manager_local`（bool）：本进程 event_manager 是否可用

`GET /agent-tasks/{id}` 响应 MUST 包含 `orchestrator_alive` 布尔字段（`alive_at > now-30s` 即为 true），前端据此显示 stale running 恢复横幅。Redis 不可用时 SHALL 降级到进程内 dict（现有行为）并打 WARNING 日志。

#### Scenario: Redis 中的 alive_at 过期识别为 stale
- **WHEN** Orchestrator 进程被 kill 或 uvicorn --reload 触发重启，Redis 的 `alive_at` 60 秒后过期
- **THEN** `GET /agent-tasks/{id}` 响应中 `orchestrator_alive=false`，前端展示恢复横幅

#### Scenario: 多 worker 下另一个 worker 也能看到
- **WHEN** worker A 启动了任务，worker B 收到该 task_id 的 GET 请求
- **THEN** worker B 通过 Redis 查到 `alive_at` 有效，返回 `orchestrator_alive=true`

#### Scenario: Redis 不可用时降级
- **WHEN** Redis 连接失败
- **THEN** 后端 fallback 到进程内 dict，日志打 WARNING，`orchestrator_alive` 字段仍能返回（仅基于本进程）

### Requirement: 前端 useResilientStream 生命周期与自愈

前端 `useResilientStream` Hook MUST 满足以下生命周期与自愈约束：

- `hasConnectedRef.current` MUST 在 effect cleanup 中复位为 false，使 React 18 StrictMode 双挂载与运行时断开都能自愈重连
- `disconnectInternal` MUST NOT 清零 `latestSeenSequenceRef`（保留高水位，重连不重放老事件）
- `parseSSE` MUST 解析 `id:` 字段并更新 `latestSeenSequenceRef`
- 重连时 MUST 在 fetch 请求上带 `Last-Event-ID: {latestSeenSequence}` header
- 心跳超时窗口 MUST 在收到 `tool_call_start`/`tool_call` 事件后切换到 180 秒，收到对应 `tool_call_end`/`tool_result`/终态事件后恢复 45 秒

#### Scenario: cleanup 复位 hasConnectedRef
- **WHEN** React 18 StrictMode 触发 mount → cleanup → mount 序列
- **THEN** 第二次 mount 时 `hasConnectedRef.current === false`，SSE 连接被正常建立

#### Scenario: disconnect 保留 sequence 高水位
- **WHEN** 前端 disconnect 后重新 connect
- **THEN** `latestSeenSequenceRef.current` 保持断开前的值，不重放老事件

#### Scenario: 重连带 Last-Event-ID header
- **WHEN** 前端心跳超时触发重连，`latestSeenSequenceRef.current === 300`
- **THEN** 新的 fetch 请求携带 `Last-Event-ID: 300` header 与 `?after_sequence=300` 查询参数

### Requirement: 前端状态判定 stale running 用后端字段

前端 `canRecover` 派生状态 MUST 基于后端下发的 `orchestrator_alive` 字段判定，而不是当前 `state.task?.status === 'running'`。`canRecover = task.status === 'running' && task.orchestrator_alive === false`。红色恢复横幅 MUST 能正常显示。

#### Scenario: stale running 显示恢复横幅
- **WHEN** 任务 status='running' 但后端下发 `orchestrator_alive=false`
- **THEN** 前端 `canRecover=true`，红色恢复横幅显示，点击调用 `POST /recover`

#### Scenario: 活跃运行不显示恢复横幅
- **WHEN** 任务 status='running' 且 `orchestrator_alive=true`
- **THEN** 前端 `canRecover=false`，不显示恢复横幅

