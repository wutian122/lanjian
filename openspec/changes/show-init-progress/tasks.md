# Tasks: Show Init Progress During Task Startup

## Phase 1: Backend - Status and Events

- [ ] T1: Add `INITIALIZING` status to `AgentTaskStatus`
  - File: `backend/app/models/agent_task.py`
  - Add: `INITIALIZING = "initializing"`

- [ ] T2: Set status to `INITIALIZING` in `_execute_agent_task`
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - After Docker sandbox init, before preparation: set `task.status = AgentTaskStatus.INITIALIZING`

- [ ] T3: Add `_emit_init_step` helper and wrap preparation steps
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Add helper function `_emit_init_step(event_emitter, step_name, status)`
  - Wrap: Docker init, ZIP extract, RAG index, tool creation, Semgrep pre-scan

## Phase 2: Frontend - State and Reducer

- [ ] T4: Add `isInitializing` computed state
  - File: `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts`
  - `isInitializing = status === 'pending' || status === 'initializing'`
  - Add to return values

- [ ] T5: Add `ADD_INIT_STEP` reducer action and `initSteps` state
  - File: `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts`
  - Add `initSteps: InitStep[]` to state
  - Add `ADD_INIT_STEP` action: appends or updates step status

## Phase 3: Frontend - SSE Connection

- [ ] T6: Change SSE connect condition to include pending/initializing
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - Change condition from `task.status !== 'running'` to `!['pending','initializing','running'].includes(task.status)`

- [ ] T7: Handle init step events in SSE onEvent handler
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - In `streamOptions.onEvent`, for info events with `metadata.init_step`:
    dispatch `ADD_INIT_STEP` with step name and status

## Phase 4: Frontend - UI Component

- [ ] T8: Create `InitProgress` component
  - File: `frontend/src/pages/AgentAudit/components/InitProgress.tsx`
  - Props: `steps: InitStep[]`, `currentStep?: string`
  - Render: vertical step list with checkmarks/spinners + progress bar

- [ ] T9: Replace loading screen with InitProgress for initializing tasks
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - After the `isLoading && !task` check, add `isInitializing && task` check
  - Render `InitProgress` with steps from state

## Phase 5: Integration and Testing

- [ ] T10: Verify end-to-end on deployment
  - Create new audit task
  - Verify init progress steps appear in real-time
  - Verify smooth transition to audit log view when status becomes running
  - Verify existing tasks not affected

## Scenario Mapping

| Scenario | Test Function |
|----------|---------------|
| Init progress shows during preparation | test_init_progress_display |
| SSE connects during initializing | test_sse_connect_on_initializing |
| Smooth transition to audit view | test_transition_to_audit_view |
| Existing tasks unaffected | test_existing_tasks_regression |
