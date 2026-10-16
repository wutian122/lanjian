# Proposal: Show Init Progress During Task Startup

## Background

When a user creates a new audit task, the frontend shows a generic "LOADING AUDIT TASK..." spinner for 2-3 minutes while the backend initializes. During this time, the backend is performing:

1. Docker sandbox manager initialization (~1s)
2. ZIP extraction / repository clone (~2-3s)
3. RAG code indexing (15,366 chunks, ~2-3s)
4. Tool creation (LLM service, CodeRetriever, etc.) (~1s)
5. Semgrep pre-scan (~80s for 90 findings)
6. First LLM call (~10-30s)

The backend already emits `phase_start` and `info` events during this period via `event_emitter`, but the frontend does not connect SSE until `task.status === 'running'`. The task stays `pending` during the entire preparation phase, so the user sees nothing but a spinner.

## Goal

Show real-time initialization progress to the user during the task preparation phase, so they know what the system is doing instead of staring at a blank loading screen.

## Non-Goals

- Optimizing the initialization speed itself (ZIP extraction, RAG indexing, Semgrep scan times are inherent).
- Changing the SSE protocol or reconnection policy.
- Modifying the Agent execution flow or tool initialization order.
- Adding new initialization steps.

## Scope

### Files Modified

| File | Changes |
|------|---------|
| `frontend/src/pages/AgentAudit/index.tsx` | Replace static loading screen with dynamic progress display; connect SSE earlier (on `pending` status, not just `running`); show init events in real-time |
| `frontend/src/pages/AgentAudit/components/InitProgress.tsx` (new) | New component showing initialization steps with icons and progress indicators |
| `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts` | Add `isInitializing` computed state (`pending` or `initializing` status) |
| `backend/app/api/v1/endpoints/agent_tasks.py` | Set task status to `initializing` at start of `_execute_agent_task` (before preparation); emit structured init events with step names |

## Total: ~4 files

## Risk Assessment

- **SSE early connection**: Connecting SSE on `pending` status is safe because the backend already creates the event queue before setting status to `running`. Events emitted during preparation will be buffered.
- **Status change**: Adding `initializing` status is backward-compatible. The existing `pending` -> `running` transition still works; we just insert `initializing` in between.
- **Frontend**: The new `InitProgress` component is additive; if it fails, the fallback is the existing loading spinner.

## Verification Approach

1. Create a new audit task and verify init progress events appear in real-time
2. Verify SSE connects during `initializing` phase
3. Verify smooth transition from init progress to audit log view
4. Verify no regression for existing running/paused/completed tasks
