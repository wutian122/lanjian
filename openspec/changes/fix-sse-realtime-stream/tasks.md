## 1. Wave 0 · P0 部署 & 数据丢失兜底

### 1.1 docker-compose 拆分 --reload
- [ ] 1.1.1 阅读 `docker-compose.yml` / `docker-compose.override.yml` / `docker-compose.prod.yml` 现状
- [ ] 1.1.2 RED: 写 `test_prod_compose_no_reload.py`（读 prod yml，断言 backend command 不含 `--reload`）
- [ ] 1.1.3 GREEN: `docker-compose.prod.yml` backend command 改为 `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`
- [ ] 1.1.4 GREEN: `docker-compose.override.yml` 保留 `--reload`（开发用）
- [ ] 1.1.5 REFACTOR: 更新 README 部署章节说明生产/开发差异

### 1.2 stream_events DB 回补分页
- [ ] 1.2.1 RED: 写 `test_sse_reconnect_backfill.py::test_backfill_paginates_beyond_500`（构造 1500 条事件的 mock task，断言全部回补）
- [ ] 1.2.2 RED: 追加 `test_backfill_respects_max_events_cap`（构造 25000 条，断言最多回补 20000 条且发送 `notice/backfill_truncated`）
- [ ] 1.2.3 GREEN: 修改 `event_manager.stream_events` DB 回补循环：`while True: batch = fetch(cursor, limit=500); if not batch: break; cursor = batch[-1].sequence; yield ...; count += len(batch); if count >= 20000: emit_truncated_notice; break`
- [ ] 1.2.4 GREEN: 补 `notice` 事件类型定义（若不存在）
- [ ] 1.2.5 REFACTOR: 抽出 `_backfill_events_paged(after_sequence, max_events=20000)` 私有方法

### 1.3 _SSE_TERMINAL_STATUSES 补齐
- [ ] 1.3.1 RED: `test_sse_terminal_statuses.py::test_completed_with_gaps_in_set`（断言集合含该值）
- [ ] 1.3.2 RED: `test_completed_with_gaps_triggers_task_end`（模拟 DB 轮询流，任务 `COMPLETED_WITH_GAPS` 时发送 task_end）
- [ ] 1.3.3 GREEN: `agent_tasks.py:260` 集合加入 `"completed_with_gaps"`
- [ ] 1.3.4 GREEN: 确认 `"initializing"` 不在集合（明确注释）

### 1.4 Wave 0 交付
- [ ] 1.4.1 `uv run pytest backend/tests/agent/test_sse_reconnect_backfill.py backend/tests/agent/test_sse_terminal_statuses.py -v` 全绿
- [ ] 1.4.2 `superpowers:verification-loop` 独立验证：所有 Scenario 有对应测试证据
- [ ] 1.4.3 提交 commit `p0: fix sse deployment and backfill dropout`
- [ ] 1.4.4 老板评审：是否立即上线 Wave 0（`docker compose -f docker-compose.prod.yml up -d --force-recreate backend`）
- [ ] 1.4.5 生产观察 30 分钟：`docker top` 无 `--reload`，`docker logs` 无 `WatchFiles` 迹象

## 2. Wave 1 · P1 SSE 稳定性核心修复

### 2.1 Semgrep prescan 异步化
- [ ] 2.1.1 RED: `test_semgrep_prescan_yields.py::test_prescan_does_not_block_event_loop`（用 `asyncio.gather` 并发跑 prescan + 心跳协程，断言心跳在 20 秒内至少发送 1 次）
- [ ] 2.1.2 RED: `test_prescan_emits_tool_call_events`（断言每规则集前后有 tool_call_start / tool_call_end 事件）
- [ ] 2.1.3 GREEN: `orchestrator._run_semgrep_prescan` 中 `subprocess.run` → `proc = await asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE, env=clean_env, cwd=project_root)`; `stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)`
- [ ] 2.1.4 GREEN: 每规则集包裹 `await self.emit_event('tool_call_start', metadata={'tool': {'name': f'semgrep_prescan_{ruleset.replace("/", "_")}'}})` / `emit_event('tool_call_end', ...)`
- [ ] 2.1.5 GREEN: 版本检查 `semgrep --version` 也改异步
- [ ] 2.1.6 REFACTOR: 抽出 `_run_single_semgrep_ruleset(ruleset, project_root, env)` 私有方法
- [ ] 2.1.7 REFACTOR: 前端 `resilientStreamPolicy.LONG_OPERATION_START_EVENTS` 若未含 tool_call_start 已含，不改；否则补充

### 2.2 修 emit_event AttributeError
- [ ] 2.2.1 RED: `test_task_timeout_emits_warning.py`（构造 `wait_for` timeout，断言 `emit_warning` 被调用一次，任务终态为 `COMPLETED_WITH_GAPS`）
- [ ] 2.2.2 RED: 追加断言 `AttributeError` 不再抛出
- [ ] 2.2.3 GREEN: `agent_tasks.py:726` `await event_emitter.emit_event('warning', ...)` → `await event_emitter.emit_warning(...)`
- [ ] 2.2.4 REFACTOR: 全仓 grep 是否还有其他 `emit_event('warning', ...` / `emit_event('info',...` 误用

### 2.3 取消路径 emit_task_cancelled
- [ ] 2.3.1 RED: `test_cancel_emits_task_cancel_event.py::test_cancel_endpoint_emits`（调 cancel 端点后 SSE 流应收 `task_cancel` 事件）
- [ ] 2.3.2 RED: 追加 `test_cancelled_error_branch_emits`（模拟 `CancelledError` 分支）
- [ ] 2.3.3 GREEN: `cancel_agent_task` 在更新 status 后调用 `event_emitter.emit_task_cancelled(...)`
- [ ] 2.3.4 GREEN: `_execute_agent_task` 的 `except asyncio.CancelledError` 分支调用 `emit_task_cancelled` 后 raise

### 2.4 SSE 端点 is_disconnected + CancelledError
- [ ] 2.4.1 RED: `test_sse_client_disconnect.py::test_generator_exits_within_5s`（客户端 abort 后 5 秒内 event_manager 队列引用被释放）
- [ ] 2.4.2 RED: `test_stream_events_catches_cancelled_error`（模拟 Starlette cancel，断言日志有对应 INFO 且异常被 re-raise）
- [ ] 2.4.3 GREEN: `stream_agent_events` / `stream_agent_with_thinking` 端点签名加 `request: Request`
- [ ] 2.4.4 GREEN: 生成器主循环用 `asyncio.wait({queue_get, is_disconnected_check}, FIRST_COMPLETED)`；断开时 break
- [ ] 2.4.5 GREEN: `stream_events` 加 `except asyncio.CancelledError: logger.info(...); raise`
- [ ] 2.4.6 REFACTOR: 抽出 `_watch_client_disconnect(request)` 辅助

### 2.5 前端 useResilientStream 生命周期
- [ ] 2.5.1 RED: `useResilientStream.reconnect.test.ts::hasConnectedRef_cleanup_resets` (React Testing Library, 用 renderHook + StrictMode 包裹)
- [ ] 2.5.2 RED: `latestSeenSequenceRef_not_reset_on_disconnect`
- [ ] 2.5.3 RED: `reconnect_carries_last_event_id_header`
- [ ] 2.5.4 GREEN: `index.tsx` effect cleanup 中 `hasConnectedRef.current = false`
- [ ] 2.5.5 GREEN: `useResilientStream.disconnectInternal` 删除 `latestSeenSequenceRef.current = 0` 行
- [ ] 2.5.6 GREEN: `parseSSE` 支持 `id:` 字段解析
- [ ] 2.5.7 GREEN: `connectInternal` fetch headers 加 `Last-Event-ID: ${latestSeenSequenceRef.current}`
- [ ] 2.5.8 GREEN: 后端 SSE 生成器读取 `Last-Event-ID` header 与 `after_sequence` 取最大值

### 2.6 SSE 事件带 id 字段
- [ ] 2.6.1 RED: `test_sse_events_include_id.py::test_events_have_id_line`
- [ ] 2.6.2 RED: `test_heartbeat_no_id_line`
- [ ] 2.6.3 GREEN: `event_manager.stream_events` 事件格式化处加 `f"id: {evt.sequence}\n"` 前缀（非心跳）
- [ ] 2.6.4 GREEN: 心跳保持不带 id

### 2.7 Wave 1 交付
- [ ] 2.7.1 后端全测通过：`uv run pytest backend/tests/agent/ -k 'sse or semgrep or cancel or timeout' -v`
- [ ] 2.7.2 前端全测通过：`cd frontend && pnpm test useResilientStream`
- [ ] 2.7.3 `superpowers:verification-loop` 覆盖 Wave 1 全部 Scenario
- [ ] 2.7.4 `/orch-review`（最多 3 轮，超过标记"需人工介入"）
- [ ] 2.7.5 `superpowers:receiving-code-review` 先验证再实施反馈
- [ ] 2.7.6 老板评审 → 上线 Wave 1
- [ ] 2.7.7 24 小时真实审计任务复跑观察

## 3. Wave 2 · P2 架构增强

### 3.1 Alembic 迁移
- [ ] 3.1.1 RED: `test_migration_022_sse_last_id.py::test_upgrade_adds_column`
- [ ] 3.1.2 GREEN: `backend/alembic/versions/022_sse_last_id.py` 加 `agent_events.sse_last_id` 可空字符串列
- [ ] 3.1.3 GREEN: `AgentEvent` 模型加对应字段
- [ ] 3.1.4 REFACTOR: alembic downgrade 分支

### 3.2 OrchestratorRegistry Redis 实现
- [ ] 3.2.1 RED: `test_orchestrator_registry_redis.py::test_alive_at_ttl_expires`
- [ ] 3.2.2 RED: `test_cross_worker_visibility`（模拟 2 个 registry 实例共享 Redis）
- [ ] 3.2.3 RED: `test_redis_unavailable_falls_back_to_dict`
- [ ] 3.2.4 GREEN: 新建 `app/services/agent/core/orchestrator_registry.py`，定义 `OrchestratorRegistry` 类
- [ ] 3.2.5 GREEN: 后台任务 `_heartbeat_alive_at()` 每 5 秒刷新 Redis key（TTL 60s）
- [ ] 3.2.6 GREEN: `orchestrator.run()` 启动时注册，`finally` 清理
- [ ] 3.2.7 GREEN: `_running_event_managers` / `_running_orchestrators` 引用点全部迁移到 registry API
- [ ] 3.2.8 GREEN: `GET /agent-tasks/{id}` 响应加 `orchestrator_alive` 字段
- [ ] 3.2.9 GREEN: `AgentTaskResponse` schema 加对应字段
- [ ] 3.2.10 REFACTOR: 移除 `_running_*` 模块级 dict（保留 fallback 实现在 registry 内部）

### 3.3 事件队列有界 + 分级丢弃
- [ ] 3.3.1 RED: `test_event_queue_bounded.py::test_queue_maxsize_10000`
- [ ] 3.3.2 RED: `test_thinking_token_dropped_when_full`
- [ ] 3.3.3 RED: `test_tool_result_waits_5s_then_drops`
- [ ] 3.3.4 RED: `test_terminal_events_always_enqueued`
- [ ] 3.3.5 GREEN: `EventManager.create_queue` 用 `asyncio.Queue(maxsize=10000)`
- [ ] 3.3.6 GREEN: `add_event` 根据事件类型走 3 种入队策略
- [ ] 3.3.7 GREEN: `dropped_thinking_tokens` 计数 + 每 100 条 WARNING 日志
- [ ] 3.3.8 REFACTOR: 抽出 `_enqueue_strategy(event_type)` 辅助

### 3.4 心跳独立协程
- [ ] 3.4.1 RED: `test_heartbeat_independent.py::test_slow_consumer_still_gets_heartbeat`（用 asyncio.Queue 的 backpressure，验证心跳周期）
- [ ] 3.4.2 GREEN: `stream_events` 拆两个 async generator 用 `asyncio.wait({...}, FIRST_COMPLETED)` 合并
- [ ] 3.4.3 GREEN: 心跳协程周期 10 秒（现 15 秒）
- [ ] 3.4.4 REFACTOR: 抽出 `_pump_heartbeats(interval=10)` / `_pump_events()` 私有函数

### 3.5 前端 reducer 与 canRecover 用后端字段
- [ ] 3.5.1 RED: `useAgentAuditState.canRecover.test.ts::test_uses_orchestrator_alive_field`
- [ ] 3.5.2 GREEN: `types.ts` 的 `AgentTask` 加 `orchestrator_alive?: boolean`
- [ ] 3.5.3 GREEN: `canRecover = task?.status === 'running' && task?.orchestrator_alive === false`
- [ ] 3.5.4 GREEN: `AgentAuditAction` 补 `RECONNECT_ATTEMPT` / `SSE_STREAM_DIED`
- [ ] 3.5.5 GREEN: reducer 处理这两个 Action

### 3.6 Wave 2 交付
- [ ] 3.6.1 全测通过：`uv run pytest backend/tests/ && cd frontend && pnpm test`
- [ ] 3.6.2 `superpowers:verification-loop` 覆盖 Wave 2 全部 Scenario
- [ ] 3.6.3 最终 `/orch-review` 一轮
- [ ] 3.6.4 E2E：真实审计 OpenHands-1.7.0（服务器已有 21M / 2249 文件项目）
- [ ] 3.6.5 观察指标：15 分钟内 SSE 无断开、`dropped_thinking_tokens < 500`、`orchestrator_alive` 正确翻转

## 4. 交付归档

- [ ] 4.1 `superpowers:finishing-a-development-branch`：呈现 4 选项（合并/PR/保留/丢弃）
- [ ] 4.2 老板选择上线路径后执行
- [ ] 4.3 `openspec archive fix-sse-realtime-stream --yes`
- [ ] 4.4 `openspec-sync-specs`：合并 delta 到主 `openspec/specs/audit-engine/spec.md` 与新的 `openspec/specs/sse-realtime-stream/spec.md`
- [ ] 4.5 更新 growth-log：记录 SSE 断流根因 + 双重心跳窗口设计模式
- [ ] 4.6 delivery-gate 通过（Hook 自动检查）
