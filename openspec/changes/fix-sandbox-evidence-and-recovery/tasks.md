# Tasks: Fix Sandbox Verification Evidence Loss &amp; Task Recovery

## Phase 1: Data Persistence (Bug A) - Unblocks all evidence fixes

- [ ] T1: Add `sandbox_attempts` column to `AgentFinding` model in `backend/app/models/agent_task.py`
  - File: `backend/app/models/agent_task.py`, after `verification_result` column
  - Add: `sandbox_attempts = Column(JSON, nullable=True)`
  - Add docstring comment: `# sandbox verification evidence [{tool, success, exit_code, command, evidence_summary, ...}]`

- [ ] T2: Generate Alembic migration
  - Command: `cd backend && uv run alembic revision --autogenerate -m "add sandbox_attempts to agent_findings"`
  - Verify migration file created in `backend/alembic/versions/`
  - Verify upgrade adds column, downgrade drops it

- [ ] T3: Fix `_save_findings` to persist `sandbox_attempts`
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`, in `_save_findings` function
  - In `db_finding = AgentFinding(...)` constructor, add: `sandbox_attempts=finding.get("sandbox_attempts"),`

- [ ] T4: Add `sandbox_attempts` to `AgentFindingResponse` schema
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`, in `AgentFindingResponse` class
  - Add: `sandbox_attempts: Optional[List[dict]] = None`

- [ ] T5: Write test: sandbox_attempts persistence
  - Create finding with sandbox_attempts -> save -> query DB -> verify field present
  - Create finding without sandbox_attempts -> save -> query -> verify field is NULL

## Phase 2: Merge Logic (Bug B) - Independent, parallel with Phase 1

- [ ] T6: Fix `is_verified` merge in Orchestrator
  - File: `backend/app/services/agent/agents/orchestrator.py`, finding merge section (~line 1891)
  - Add field-specific handling for `is_verified`, `verification_status`, `sandbox_attempts` before generic guard
  - Remove the `if existing_f.get("is_verified") or normalized_new.get("is_verified"): merged["is_verified"] = True` line

- [ ] T7: Write test: merge logic with False==0 boundary
  - Test case: existing finding is_verified=True, new finding is_verified=False + verification_status="not_reproducible"
  - Assert: merged finding is_verified=False, verification_status="not_reproducible"
  - Test case: existing finding is_verified=False, new finding is_verified=True + verification_status="confirmed"
  - Assert: merged finding is_verified=True
  - Test case: sandbox_attempts merge (existing [], new [{...}])
  - Assert: merged sandbox_attempts has 1 element

## Phase 3: Gate Fix (Bug C) - Depends on Phase 2

- [ ] T8: Fix `_has_valid_sandbox_evidence` in Orchestrator
  - File: `backend/app/services/agent/agents/orchestrator.py`, ~line 436
  - Remove `if finding.get("is_verified") is True: return True` condition
  - Add sandbox_attempts check for `confirmed` status
  - Keep `static_confirmed` as accepted

- [ ] T9: Write test: gate behavior
  - Test: all findings is_verified=True but no sandbox_attempts -> returns False
  - Test: one finding with confirmed + sandbox_attempts[{success:True, exit_code:0}] -> returns True
  - Test: one finding with static_confirmed -> returns True
  - Test: all findings needs_context -> returns False

## Phase 4: Finding-Sandbox ID (Opt-1) - Depends on Phase 1

- [ ] T10: Embed `finding_id` in sandbox commands
  - File: `backend/app/services/agent/agents/verification.py`, in `_build_sandbox_commands`
  - Generate `finding_id` per finding, store as `f["_sandbox_finding_id"]`
  - Prepend `# FINDING_ID:{id}\n` to each command string

- [ ] T11: Parse `finding_id` in `_record_sandbox_attempt`
  - File: `backend/app/services/agent/agents/verification.py`, in `_record_sandbox_attempt`
  - Add regex: `re.search(r"# FINDING_ID:(\S+)", command)`
  - Store `finding_id` in the attempt dict

- [ ] T12: Use ID matching in `_attach_runtime_sandbox_attempts`
  - File: `backend/app/services/agent/agents/verification.py`, in `_attach_runtime_sandbox_attempts`
  - Before existing fuzzy matching, try ID-based matching
  - If `finding._sandbox_finding_id` matches `attempt.finding_id`, attach directly

- [ ] T13: Write test: ID-based matching
  - Test: finding with _sandbox_finding_id="abc123" + attempt with finding_id="abc123" -> matched
  - Test: finding with _sandbox_finding_id="abc123" + attempt with finding_id="xyz789" -> not matched, falls through to fuzzy
  - Test: finding without _sandbox_finding_id -> falls through to fuzzy matching

## Phase 5: Event Persistence (Opt-2) - Independent

- [ ] T14: Fix `tool_output` for sandbox_exec events
  - File: `backend/app/services/agent/agents/base.py`, check `emit_tool_result` / `emit_tool_call`
  - Verify `tool_output` parameter is forwarded to `AgentEvent`
  - If truncated or dropped, fix the forwarding
  - Truncate to 10000 chars to match sandbox stdout limit

- [ ] T15: Write test: event tool_output not null
  - Test: execute sandbox_exec -> query AgentEvent -> assert tool_output is not null
  - Test: tool_output contains sandbox stdout

## Phase 6: Full Verification Gate (Bug D) - Depends on Phase 3

- [ ] T16: Add full verification gate in Orchestrator
  - File: `backend/app/services/agent/agents/orchestrator.py`, after Semgrep gate, before coverage gate
  - Check for findings without `verification_status`
  - Force dispatch Verification for unverified findings
  - Initialize `self._full_verification_dispatched = False` in `_reset_state`

- [ ] T17: Write test: full verification gate
  - Test: 5 findings, 3 verified, 2 unverified -> gate triggers, dispatches Verification
  - Test: all findings verified -> gate does not trigger
  - Test: gate triggers once (flag prevents repeated forcing)

## Phase 7: Re-audit API (Bug E) - Depends on Phase 1 and 6

- [ ] T18: Implement `POST /{task_id}/re-audit` endpoint
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Accept only `completed_with_gaps` status
  - Find unverified findings (is_verified == False)
  - Set task to running, launch `_re_audit_task` background job

- [ ] T19: Implement `_re_audit_task` function
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Construct Orchestrator with only Verification dispatch for specified finding IDs
  - Load findings from DB, convert to finding dicts
  - After Verification, update findings in place (not duplicate)
  - Set task status to completed or completed_with_gaps

- [ ] T20: Write test: re-audit flow
  - Test: completed_with_gaps task with 2 unverified findings -> re-audit -> findings updated
  - Test: completed task -> 400 error
  - Test: all findings verified -> 400 error

## Phase 8: Stale Running Recovery (Bug F) - Independent

- [ ] T21: Implement `POST /{task_id}/recover` endpoint
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Accept only `running` status
  - Check `_running_orchestrators` dict
  - If not in dict, convert to `paused` status

- [ ] T22: Write test: recover flow
  - Test: running task not in _running_orchestrators -> recover -> status=paused
  - Test: running task in _running_orchestrators -> 400 error
  - Test: completed task -> 400 error

## Phase 9: Frontend (Opt-3) - Depends on Phase 1

- [ ] T23: Add `SandboxAttempt` type to frontend types
  - File: `frontend/src/pages/AgentAudit/types.ts`
  - Add interface with tool, success, exit_code, command, evidence_summary, target_ref, finding_id, weak_evidence

- [ ] T24: Add `canReAudit` and `canRecover` computed states
  - File: `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts`
  - `canReAudit`: status === 'completed_with_gaps' && has unverified findings
  - `canRecover`: status === 'running' && SSE disconnected

- [ ] T25: Add re-audit and recover API functions
  - File: `frontend/src/shared/api/agentStream.ts`
  - `reAuditAgentTask(taskId)`: POST /agent-tasks/{id}/re-audit
  - `recoverAgentTask(taskId)`: POST /agent-tasks/{id}/recover

- [ ] T26: Render "Supplement Audit" and "Recover" buttons
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - Show "Supplement Audit" button when canReAudit
  - Show "Recover" button when canRecover
  - Wire to API functions with toast feedback

- [ ] T27: Create `FindingSandboxEvidence` component
  - File: `frontend/src/pages/AgentAudit/components/FindingSandboxEvidence.tsx`
  - Accept `attempts: SandboxAttempt[]` prop
  - Show alert if empty/null
  - Render attempt cards with badge, exit code, collapsible command/output

- [ ] T28: Integrate sandbox evidence into finding detail
  - File: `frontend/src/pages/AgentAudit/index.tsx` or new `FindingDetailPanel.tsx`
  - Render `FindingSandboxEvidence` in the finding detail section
  - Fetch from `finding.sandbox_attempts`

## Phase 10: Integration &amp; E2E

- [ ] T29: Run full test suite
  - `cd backend && uv run pytest`
  - `cd frontend && pnpm type-check && pnpm lint`
  - Fix any regressions

- [ ] T30: Run migration on test DB
  - `cd backend && uv run alembic upgrade head`
  - Verify column exists in DB

- [ ] T31: E2E test: sandbox evidence persistence
  - Create audit task -> run -> check findings API -> verify sandbox_attempts present
  - Check AgentEvent tool_output is not null

- [ ] T32: E2E test: re-audit flow
  - Complete task with gaps -> re-audit -> verify new findings have verification_status

- [ ] T33: E2E test: recover flow
  - Simulate stale running -> recover -> resume -> verify task continues

## Scenario Mapping (Step 3 Bridge)

| Scenario | Test Function |
|----------|---------------|
| sandbox_attempts persisted to DB | test_sandbox_attempts_persisted |
| is_verified=False overrides True | test_merge_is_verified_false_overrides |
| sandbox gate rejects is_verified-only | test_sandbox_gate_rejects_without_evidence |
| all findings sent to verification | test_full_verification_gate_triggers |
| completed_with_gaps re-auditable | test_re_audit_endpoint |
| stale running recoverable | test_recover_stale_running |
| sandbox_exec event has tool_output | test_event_tool_output_not_null |
| finding-sandbox ID matching | test_finding_id_based_matching |
| frontend displays sandbox evidence | test_frontend_sandbox_evidence_display |
