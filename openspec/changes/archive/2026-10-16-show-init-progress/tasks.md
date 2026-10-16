# Tasks: Show Init Progress During Task Startup

## Phase 1: Backend - Status and Events

- [x] T1: Add `INITIALIZING` status to `AgentTaskStatus`
  - File: `backend/app/models/agent_task.py`
  - Add: `INITIALIZING = "initializing"`
  - ✅ 2026-10-16 归档核验：`models/agent_task.py:25` 已定义

- [x] T2: Set status to `INITIALIZING` in `_execute_agent_task`
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - After Docker sandbox init, before preparation: set `task.status = AgentTaskStatus.INITIALIZING`
  - ✅ 2026-10-16 归档核验：`agent_tasks.py:907-908` 设置；`:125` 重启守卫覆盖 INITIALIZING

- [x] T3: Add `_emit_init_step` helper and wrap preparation steps
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Add helper function `_emit_init_step(event_emitter, step_name, status)`
  - Wrap: Docker init, ZIP extract, RAG index, tool creation, Semgrep pre-scan
  - ✅ 2026-10-16 归档核验：实现为内联 `emit_info(metadata={"init_step","init_status"})`（非独立 helper，形态偏差不影响契约）——`agent_tasks.py:916/926/1057-1058/1171-1172/1216` 覆盖 Docker sandbox/Indexing code/Preparing agents/Starting audit/Extracting project 五步；前端按 `metadata.init_step` 消费，两端契约一致

## Phase 2: Frontend - State and Reducer

- [x] T4: Add `isInitializing` computed state
  - File: `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts`
  - `isInitializing = status === 'pending' || status === 'initializing'`
  - Add to return values
  - ✅ 2026-10-16 归档核验：`useAgentAuditState.ts:367` useMemo、`:395` 导出

- [x] T5: Add `ADD_INIT_STEP` reducer action and `initSteps` state
  - File: `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts`
  - Add `initSteps: InitStep[]` to state
  - Add `ADD_INIT_STEP` action: appends or updates step status
  - ✅ 2026-10-16 归档核验：`:27` initSteps 初始态、`:208-219` ADD_INIT_STEP case（append/update 双路径）

## Phase 3: Frontend - SSE Connection

- [x] T6: Change SSE connect condition to include pending/initializing
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - Change condition from `task.status !== 'running'` to `!['pending','initializing','running'].includes(task.status)`
  - ✅ 2026-10-16 归档核验：`index.tsx:1017` 连接条件与任务描述一致

- [x] T7: Handle init step events in SSE onEvent handler
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - In `streamOptions.onEvent`, for info events with `metadata.init_step`:
    dispatch `ADD_INIT_STEP` with step name and status
  - ✅ 2026-10-16 归档核验：`index.tsx:598` onEvent 签名含 init_step/init_status、`:633-637` dispatch ADD_INIT_STEP；`:969` 另有 RAG 索引期轮询兜底（REQ-IP-2）

## Phase 4: Frontend - UI Component

- [x] T8: Create `InitProgress` component
  - File: `frontend/src/pages/AgentAudit/components/InitProgress.tsx`
  - Props: `steps: InitStep[]`, `currentStep?: string`
  - Render: vertical step list with checkmarks/spinners + progress bar
  - ✅ 2026-10-16 归档核验：组件存在且实际 Props 为 `steps` + `indexingProgress`（RAG 索引进度增强，超出原设计）

- [x] T9: Replace loading screen with InitProgress for initializing tasks
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - After the `isLoading && !task` check, add `isInitializing && task` check
  - Render `InitProgress` with steps from state
  - ✅ 2026-10-16 归档核验：`index.tsx:1423` 渲染、`:618-620` phase_start 过渡回主视图

## Phase 5: Integration and Testing

- [x] T10: Verify end-to-end on deployment
  - Create new audit task
  - Verify init progress steps appear in real-time
  - Verify smooth transition to audit log view when status becomes running
  - Verify existing tasks not affected
  - ✅ 2026-10-16 归档核验：功能随 v6.6.0→v6.9.1 多版本双机生产部署持续运行（部署记录 06b6ef8 等），初始化进度为生产日常可见行为；归档前全量回归 1842 passed/4 预存失败（与 P13 基线一致，零新增变红）

## Scenario Mapping

| Scenario | Test Function |
|----------|---------------|
| Init progress shows during preparation | test_init_progress_display |
| SSE connects during initializing | test_sse_connect_on_initializing |
| Smooth transition to audit view | test_transition_to_audit_view |
| Existing tasks unaffected | test_existing_tasks_regression |
