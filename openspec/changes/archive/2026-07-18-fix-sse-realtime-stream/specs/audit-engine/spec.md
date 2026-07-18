## ADDED Requirements

### Requirement: Orchestrator 长耗时同步操作不得冻结事件循环

Orchestrator 的 Semgrep prescan 及任何执行时间可能超过 15 秒的外部子进程调用 SHALL 使用 `asyncio.create_subprocess_exec` 或 `asyncio.to_thread` 等异步机制，绝不得直接使用同步 `subprocess.run`/`subprocess.check_output`。事件循环 MUST 在长耗时操作期间保持响应能力：SSE 心跳（10-15 秒间隔）能正常发送、`request.is_disconnected()` 能被检测、其他并发任务能正常调度。

Semgrep prescan 中每个规则集 SHALL 在开始前发出 `tool_call_start` 事件、结束后发出 `tool_call_end`（或 `tool_call_error`）事件，使前端 `useResilientStream` 进入长操作心跳窗口（默认 180 秒）。

#### Scenario: Semgrep prescan 期间事件循环保持响应
- **WHEN** Orchestrator 执行 `_run_semgrep_prescan()`，单个规则集耗时 45 秒以上
- **THEN** 期间 SSE 心跳事件仍按周期发送到客户端，客户端不因心跳超时而断连

#### Scenario: 每个规则集包裹 tool_call 事件
- **WHEN** Semgrep prescan 开始扫描规则集 `p/security-audit`
- **THEN** 事件流中先出现 `tool_call_start`（tool_name=`semgrep_prescan_p_security_audit`），扫描结束后出现 `tool_call_end`

#### Scenario: 长耗时期间前端进入长操作心跳窗口
- **WHEN** 前端收到 `tool_call_start` 事件（工具名以 `semgrep_prescan_` 开头或任何 `tool_call`/`tool_call_start` 事件）
- **THEN** 前端 `useResilientStream` 心跳超时切换到 `longOperationHeartbeatTimeout` (180 秒)，直到收到对应 `tool_call_end`/`tool_result` 事件后恢复默认 (45 秒)

### Requirement: 任务取消路径发出终态事件

`cancel_agent_task` 端点和 `_execute_agent_task` 的 `asyncio.CancelledError` 分支 SHALL 在更新 `task.status = CANCELLED` 之后调用 `event_emitter.emit_task_cancelled(...)`，使 SSE 流的 `stream_events` 能通过 `task_cancel` 终端事件类型正常退出，前端立即感知取消而不用等心跳超时。

#### Scenario: 手动取消任务发出 task_cancel
- **WHEN** 客户端调用 `POST /agent-tasks/{id}/cancel`
- **THEN** 后端在更新 DB 状态后发出 `task_cancel` SSE 事件，前端在下一次事件循环即感知任务已取消（无需等 15 秒心跳）

#### Scenario: CancelledError 分支也发出 task_cancel
- **WHEN** `_execute_agent_task` 内部触发 `asyncio.CancelledError`（如 taskrunner.cancel）
- **THEN** `except CancelledError` 分支调用 `emit_task_cancelled` 后再 raise，SSE 流正常关闭

### Requirement: 超时保护路径使用正确的 emitter 方法

`_execute_agent_task` 中 `asyncio.wait_for(run_task, timeout=task_timeout)` 的 `TimeoutError` 分支 SHALL 使用 `event_emitter.emit_warning(...)` 而不是不存在的 `event_emitter.emit_event('warning', ...)`。前者在 `AgentEventEmitter` 类上定义，后者会抛 `AttributeError` 导致任务被误判为 FAILED 且不发终态事件。

#### Scenario: 任务超时正常降级为 COMPLETED_WITH_GAPS
- **WHEN** 任务运行超过 `task.timeout_seconds`（默认 1800 秒）
- **THEN** `emit_warning` 成功发出，任务 status 被设置为 `COMPLETED_WITH_GAPS`（`coverage_bypass_info.reason=task_timeout`），`emit_task_complete` 正常发出，SSE 流正常关闭

## MODIFIED Requirements

### Requirement: 覆盖率放行携带完整信息

Orchestrator 所有覆盖率放行分支（5 次拦截放行、analysis 重复调度放行、主迭代耗尽放行、**任务总耗时超时放行**）SHALL 在 `coverage_bypass_info` 中携带 `gaps`、`block_count`、`reason`、`covered_count`、`total_dimensions`，并通过 `result.metadata` 传到完成回调，使 `COMPLETED_WITH_GAPS` 状态可追溯。

#### Scenario: 放行分支携带完整 coverage_info
- **WHEN** 任一覆盖率放行分支触发（含新增的 task_timeout 分支）
- **THEN** `result.metadata.coverage_info` 含 gaps、block_count、reason、covered_count、total_dimensions 五个字段

#### Scenario: 超时放行 reason 为 task_timeout
- **WHEN** `asyncio.wait_for(run_task, timeout=task_timeout)` 抛 `TimeoutError`
- **THEN** 构造的 `AgentResult.metadata.coverage_info.reason` 等于字符串 `"task_timeout"`
