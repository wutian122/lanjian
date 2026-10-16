# Proposal: Fix Sandbox Verification Evidence Loss & Task Recovery

## Background

Two production audit tasks (ac74fa16, bea4be71) on the OpenHands project revealed systemic defects in the lanjian audit engine's sandbox verification pipeline and task recovery mechanism:

1. **Task ac74fa16**: After an unexpected disconnection, the task could not be resumed. The "Continue" button never appeared because the task had already auto-completed to `completed_with_gaps` status during the backend process's continued execution after SSE disconnect. No mechanism exists to re-audit or recover such tasks.

2. **Task bea4be71**: 10 `sandbox_exec` calls were made by the Verification Agent, but all 8 findings in the database have `sandbox_attempts = 0`, `poc = null`, and `verification_details = null`. Despite zero sandbox evidence, 4 findings were marked `is_verified = True`. Only 3 of 8 findings were ever sent to Verification Agent; the remaining 5 were never verified.

Root cause analysis identified 6 bugs and 3 architectural improvements:

### Bugs

- **Bug A (Critical)**: `AgentFinding` model has no `sandbox_attempts` column. `_save_findings` drops the field. Evidence is lost at DB persistence.
- **Bug B (Critical)**: Orchestrator finding merge logic uses `value != 0` to skip empty values. Python `False == 0` evaluates to `True`, so `is_verified=False` from Verification cannot overwrite `is_verified=True` from Analysis. A subsequent `if existing or new: merged=True` line further guarantees the merge is always True.
- **Bug C (Severe)**: `_has_valid_sandbox_evidence()` in Orchestrator has a third condition `if finding.get("is_verified") is True: return True` that allows bypassing the sandbox gate without any actual sandbox evidence.
- **Bug D (Severe)**: Orchestrator dispatches Verification only for the first batch of findings from Analysis. Findings discovered in later Analysis rounds are never sent to Verification.
- **Bug E (Severe)**: `completed_with_gaps` is a terminal status with no re-audit entry point. Users cannot supplement or re-run verification on completed tasks.
- **Bug F (Severe)**: No stale-running recovery. If the backend process dies but DB status remains `running`, the task is stuck. The pause API has orphan handling but requires user manual interaction.

### Architectural Improvements

- **Opt-1**: Finding-Sandbox association by embedded ID. Replace fuzzy file-path + keyword matching with a `finding_id` comment embedded in sandbox commands, enabling precise evidence-to-finding linkage.
- **Opt-2**: Sandbox result persistence to AgentEvent. Fix `tool_output = null` for sandbox_exec events by storing full result (command + stdout + stderr + exit_code + success) in event records.
- **Opt-3**: Frontend sandbox evidence display. Show sandbox_attempts detail in the Finding detail panel so users can see the actual verification process per vulnerability.

## Goal

Ensure that every finding's sandbox verification evidence is persisted, traceable, and visible; that the sandbox gate cannot be bypassed by code analysis alone; that all findings are sent to Verification; and that users can recover, re-audit, and supplement completed tasks.

## Non-Goals

- Rewriting the Verification Agent's ReAct loop or prompt strategy.
- Changing the sandbox Docker image or its pre-installed packages.
- Modifying the LLM adapter or model selection logic.
- Adding new vulnerability coverage dimensions (D1-D10 matrix unchanged).
- Changing the SSE streaming protocol or reconnection policy.

## Scope

### Files Modified (Backend)

| File | Changes |
|------|--------|
| `backend/app/models/agent_task.py` | Add `sandbox_attempts` column to `AgentFinding`; add `sandbox_attempts` to `AgentFindingResponse` |
| `backend/app/api/v1/endpoints/agent_tasks.py` | Fix `_save_findings` to persist `sandbox_attempts`; fix `AgentFindingResponse`; add `re-audit` API; add `recover` API |
| `backend/app/services/agent/agents/orchestrator.py` | Fix `is_verified` merge logic; fix `_has_valid_sandbox_evidence`; add full-verification gate before finish |
| `backend/app/services/agent/agents/verification.py` | Add `finding_id` to sandbox commands; fix `_record_sandbox_attempt` to parse ID; fix `_attach_runtime_sandbox_attempts` to use ID matching; persist sandbox results to events |
| `backend/alembic/versions/xxxx_add_sandbox_attempts.py` | New migration: add `sandbox_attempts` column |

### Files Modified (Frontend)

| File | Changes |
|------|--------|
| `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts` | Add `canReAudit` and `canRecover` computed states |
| `frontend/src/pages/AgentAudit/index.tsx` | Render "Supplement Audit" button for `completed_with_gaps`; render "Recover" button for stale running |
| `frontend/src/shared/api/agentStream.ts` | Add `reAuditAgentTask` and `recoverAgentTask` API functions |
| `frontend/src/pages/AgentAudit/components/FindingDetail.tsx` (new) | Sandbox evidence display panel |

### Files Modified (Specs)

| File | Changes |
|------|--------|
| `openspec/changes/fix-sandbox-evidence-and-recovery/specs/audit-engine/spec.md` | Delta spec with new requirements and scenarios |

## Total: ~12 files, 1 migration

## Risk Assessment

- **DB Migration**: Adding a nullable JSON column is backward-compatible. No data loss risk.
- **Merge Logic Fix**: Changing `is_verified` merge behavior could affect existing tasks. Mitigated by: only changing the merge path, not the data model.
- **Re-audit API**: New endpoint, no breaking change to existing APIs.
- **Frontend Changes**: New buttons and panels, no removal of existing UI elements.
- **Finding-Sandbox ID**: Embedded in command comment, LLM can ignore it without breaking execution.

## Verification Approach

1. Unit tests for merge logic (including `False == 0` boundary)
2. Integration test: sandbox_exec -> _save_findings -> DB has sandbox_attempts -> API returns it
3. E2E test: completed_with_gaps -> re-audit -> Verification runs -> findings updated
4. E2E test: stale running -> recover -> resume
5. Frontend: Finding detail panel shows sandbox evidence
