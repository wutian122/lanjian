# Design: Show Init Progress During Task Startup

## Architecture Context

```
Current Flow:
  User clicks "Create Task"
    -> POST /agent-tasks (creates task with status=pending)
    -> navigate to /agent-audit/{taskId}
    -> Frontend: isLoading=true, shows "LOADING AUDIT TASK..." spinner
    -> Backend: _execute_agent_task starts in background
      -> Docker init, ZIP extract, RAG index, Semgrep scan...
      -> Sets status=running
    -> Frontend: loadTask() sees status=running, loads data
    -> Frontend: SSE connects (condition: status === 'running')
    -> User sees audit log

  Problem: 2-3 minutes of blank spinner before audit log appears

New Flow:
  User clicks "Create Task"
    -> POST /agent-tasks (creates task with status=pending)
    -> navigate to /agent-audit/{taskId}
    -> Frontend: isLoading=true, shows InitProgress component
    -> Frontend: SSE connects immediately (condition: status in pending/initializing/running)
    -> Backend: _execute_agent_task starts
      -> Sets status=initializing
      -> Emits structured init events: "Docker sandbox initializing", "Extracting project files", "Indexing code (15366 chunks)", "Running Semgrep pre-scan", "Preparing AI agents"
      -> Sets status=running
    -> Frontend: InitProgress shows real-time steps from SSE events
    -> Frontend: When status=running, transitions to audit log view
    -> User sees what's happening during initialization
```

## Backend Changes

### 1. Add `INITIALIZING` status

File: `backend/app/models/agent_task.py`

In `AgentTaskStatus` class, add:

```python
INITIALIZING = "initializing"  # 初始化中（项目准备、RAG索引、Semgrep预扫描）
```

### 2. Set status to `initializing` at start of `_execute_agent_task`

File: `backend/app/api/v1/endpoints/agent_tasks.py`

In `_execute_agent_task`, right after the Docker sandbox init and before the preparation phase:

```python
# Set status to INITIALIZING so frontend can show progress
task.status = AgentTaskStatus.INITIALIZING
task.current_phase = AgentTaskPhase.PLANNING
await db.commit()
```

### 3. Emit structured init events

File: `backend/app/api/v1/endpoints/agent_tasks.py`

The backend already emits `info` events via `event_emitter.emit_info(...)` during preparation. These are already handled by the frontend SSE `onEvent` handler. We just need to make them more structured and visible.

Add a helper to emit init step events:

```python
async def _emit_init_step(event_emitter, step_name: str, status: str = "start"):
    """Emit a structured initialization step event."""
    await event_emitter.emit_event(
        "info",
        f"{'▶' if status == 'start' else '✅'} {step_name}",
        metadata={"init_step": step_name, "init_status": status}
    )
```

Then wrap the existing preparation steps:

```python
await _emit_init_step(event_emitter, "初始化 Docker 沙箱环境")
sandbox_manager = SandboxManager()
await sandbox_manager.initialize()

await _emit_init_step(event_emitter, "准备项目文件")
project_root = await _get_project_root(...)

await _emit_init_step(event_emitter, f"索引代码 ({total_files} 个文件)")
tools = await _initialize_tools(...)

await _emit_init_step(event_emitter, "执行 Semgrep 安全预扫描")
# Semgrep runs inside orchestrator.run(), but we can emit before
```

## Frontend Changes

### 4. Add `isInitializing` computed state

File: `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts`

```typescript
const isInitializing = useMemo(() => {
    return state.task?.status === 'pending' || state.task?.status === 'initializing';
}, [state.task?.status]);
```

Add to return values.

### 5. Connect SSE during initialization phase

File: `frontend/src/pages/AgentAudit/index.tsx`

Change the SSE connect condition from:

```typescript
if (!taskId || !task?.status || task.status !== 'running') return;
```

To:

```typescript
if (!taskId || !task?.status) return;
if (!['pending', 'initializing', 'running'].includes(task.status)) return;
```

Also remove the `historicalEventsLoaded` gate for initial connection. For new tasks (no historical events), there's nothing to load, so we can connect immediately:

```typescript
// For new tasks, connect immediately without waiting for historical events
if (task.status === 'pending' || task.status === 'initializing') {
    if (!historicalEventsLoaded) {
        // For new tasks, skip historical event loading
        setHistoricalEventsLoaded(true);
    }
}
```

### 6. Create `InitProgress` component

File: `frontend/src/pages/AgentAudit/components/InitProgress.tsx`

A visual component that shows initialization steps received from SSE events. It displays:
- A list of completed/running steps with icons
- The current step with a spinner
- A progress bar (estimated based on step count)

```tsx
interface InitStep {
    name: string;
    status: 'running' | 'done';
}

function InitProgress({ steps, currentStep }: { steps: InitStep[]; currentStep?: string }) {
    // Render a vertical list of steps with checkmarks and spinners
    // Show estimated progress bar
}
```

### 7. Replace loading screen with `InitProgress`

File: `frontend/src/pages/AgentAudit/index.tsx`

Replace:

```tsx
if (isLoading && !task) {
    return <LoadingSpinner />;
}
```

With:

```tsx
if (isLoading && !task) {
    return <LoadingSpinner />;
}

if (isInitializing && task) {
    return (
        <div className="h-screen bg-background flex items-center justify-center">
            <InitProgress steps={initSteps} currentStep={currentInitStep} />
        </div>
    );
}
```

The `initSteps` and `currentInitStep` are derived from SSE events received during initialization. The SSE `onEvent` handler already dispatches `ADD_LOG` for info events. We add a new dispatch type `ADD_INIT_STEP` to track init steps separately:

```typescript
// In SSE onEvent handler, for info events with init_step metadata:
if (event.metadata?.init_step) {
    dispatch({
        type: 'ADD_INIT_STEP',
        payload: {
            name: event.metadata.init_step,
            status: event.metadata.init_status || 'start',
        }
    });
}
```

## Clarification Log

(Brainstorming clarifications will be appended here)

## Implementation Order

1. Backend: Add `INITIALIZING` status to model
2. Backend: Set status to `initializing` and emit structured init events in `_execute_agent_task`
3. Frontend: Add `isInitializing` state and `ADD_INIT_STEP` reducer action
4. Frontend: Create `InitProgress` component
5. Frontend: Change SSE connect condition to include `pending`/`initializing`
6. Frontend: Replace loading screen with `InitProgress` for initializing tasks
7. Frontend: Handle init step events in SSE `onEvent` handler

## Acceptance Criteria

1. When a new task is created, the user sees a progress screen instead of a blank spinner.
2. Init steps appear in real-time as the backend completes each preparation phase.
3. When the task transitions to `running`, the init progress screen transitions smoothly to the audit log view.
4. Existing running/paused/completed tasks are not affected.
5. SSE connects during `pending`/`initializing` phase, not only `running`.
