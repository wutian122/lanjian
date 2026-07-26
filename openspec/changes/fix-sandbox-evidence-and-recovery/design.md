# Design: Fix Sandbox Verification Evidence Loss &amp; Task Recovery

## Architecture Context

```
Orchestrator (orchestrator.py)
  |- Analysis Agent -> produces findings (is_verified may be True from code analysis)
  |- Verification Agent (verification.py)
  |    |- _build_sandbox_commands() -> generates PoC commands with finding_id
  |    |- run() ReAct loop -> LLM calls sandbox_exec
  |    |- _record_sandbox_attempt() -> stores runtime evidence
  |    |- _attach_runtime_sandbox_attempts() -> matches evidence to findings
  |    +- _normalize_verification_outcome() -> sets verification_status
  +- _has_valid_sandbox_evidence() -> gate check before allowing finish

agent_tasks.py
  |- _execute_agent_task() -> runs orchestrator
  |- _save_findings() -> persists findings to DB  [BUG A: drops sandbox_attempts]
  |- resume_agent_task() -> resume from checkpoint
  |- POST /re-audit (NEW) -> re-run verification on completed_with_gaps
  +- POST /recover (NEW) -> recover stale running tasks

AgentFinding (agent_task.py)
  +- sandbox_attempts (NEW column, JSON) [BUG A fix]
```

## Bug A: sandbox_attempts Not Persisted

### Root Cause

`AgentFinding` model (`agent_task.py:358`) has no `sandbox_attempts` column. The `_save_findings` function (`agent_tasks.py:1521`) constructs `db_finding = AgentFinding(...)` without passing `sandbox_attempts`. The field exists in the runtime finding dict (populated by `_attach_runtime_sandbox_attempts`), but is silently dropped at DB write time.

### Fix

**Model** (`agent_task.py`, after the `verification_result` column):

```python
sandbox_attempts = Column(JSON, nullable=True)
```

**Migration**:

```python
def upgrade():
    op.add_column('agent_findings', sa.Column('sandbox_attempts', sa.JSON(), nullable=True))

def downgrade():
    op.drop_column('agent_findings', 'sandbox_attempts')
```

**Save** (`agent_tasks.py`, in `_save_findings`, in the `db_finding = AgentFinding(...)` constructor):

```python
sandbox_attempts=finding.get("sandbox_attempts"),
```

**Response Schema** (`agent_tasks.py`, `AgentFindingResponse` class):

```python
sandbox_attempts: Optional[List[dict]] = None
```

### Impact

- Existing findings: `sandbox_attempts` will be NULL (nullable column). No breaking change.
- New findings: sandbox evidence persisted and queryable.
- API: `/findings` endpoint returns `sandbox_attempts` array.

---

## Bug B: is_verified Merge False==0

### Root Cause

Orchestrator finding merge logic (`orchestrator.py:1891-1920`):

```python
for key, value in normalized_new.items():
    if value is not None and value != "" and value != 0:  # False == 0 -> True -> skip!
        merged[key] = value
```

When Verification returns `is_verified=False`, `False != 0` evaluates to `False` in Python, so the condition fails and the value is NOT written. The existing `is_verified=True` from Analysis survives.

Then a second line makes it worse:

```python
if existing_f.get("is_verified") or normalized_new.get("is_verified"):
    merged["is_verified"] = True
```

### Fix

Replace the generic merge guard with a field-specific policy:

```python
for key, value in normalized_new.items():
    # Bug B fix: is_verified uses explicit priority (Verification > Analysis)
    if key == "is_verified":
        if normalized_new.get("verification_status") or normalized_new.get("verdict"):
            merged[key] = value
        continue
    # Bug B fix: verification_status also uses explicit priority
    if key == "verification_status":
        if value is not None and value != "":
            merged[key] = value
        continue
    # Bug B fix: sandbox_attempts merge (list, not scalar)
    if key == "sandbox_attempts" and isinstance(value, list) and len(value) > 0:
        merged[key] = (merged.get(key) or []) + value
        continue
    # Default: skip None/empty/zero
    if value is not None and value != "" and value != 0:
        merged[key] = value
    elif key not in merged or merged[key] is None:
        merged[key] = value
```

Remove the `if existing_f.get("is_verified") or normalized_new.get("is_verified"): merged["is_verified"] = True` line entirely.

### Impact

- Verification results now correctly override Analysis results for `is_verified` and `verification_status`.
- `sandbox_attempts` from Verification are merged into existing findings.
- No data model change.

---

## Bug C: Sandbox Gate Bypass

### Root Cause

`_has_valid_sandbox_evidence()` (`orchestrator.py:436`):

```python
if finding.get("is_verified") is True:  # bypasses sandbox check
    return True
```

### Fix

Remove the third condition entirely:

```python
def _has_valid_sandbox_evidence(self) -> bool:
    for finding in self._all_findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("verification_status") == "confirmed":
            sandbox_attempts = finding.get("sandbox_attempts", [])
            if isinstance(sandbox_attempts, list) and len(sandbox_attempts) > 0:
                has_success = any(
                    isinstance(a, dict) and a.get("success") is True
                    and a.get("exit_code") == 0
                    for a in sandbox_attempts
                )
                if has_success:
                    return True
        if finding.get("verification_status") == "static_confirmed":
            return True
    return False
```

### Impact

- Tasks with only code-analysis-verified findings will not pass the sandbox gate.
- They will be forced to dispatch Verification or complete with `completed_with_gaps`.

---

## Bug D: Incomplete Verification Dispatch

### Root Cause

Orchestrator dispatches Verification only for the first batch of findings. When Analysis is dispatched again and discovers new findings, these are never sent to Verification.

### Fix

Add a new gate check before `finish` (after the existing Semgrep gate):

```python
# Bug D fix: Ensure all findings have been sent to Verification
unverified_findings = [
    f for f in self._all_findings
    if not f.get("verification_status")
    and f.get("is_verified") is not True
]
if unverified_findings and verification_count > 0 and not self._full_verification_dispatched:
    self._full_verification_dispatched = True
    await self.emit_event(
        "warning",
        f"warning: {len(unverified_findings)} unverified findings, forcing Verification dispatch"
    )
    unverified_summary = "\n".join(
        f"- {f.get('file_path', '?')}:{f.get('line_start', 0)} "
        f"[{f.get('vulnerability_type', '?')}] {f.get('title', '')[:60]}"
        for f in unverified_findings
    )
    self._conversation_history.append({
        "role": "user",
        "content": (
            f"Full verification gate: {len(self._all_findings)} findings total, "
            f"{len(unverified_findings)} unverified.\n\n"
            f"Unverified:\n{unverified_summary}\n\n"
            "Action: dispatch_agent\n"
            f"Action Input: {{\"agent\": \"verification\", "
            f"\"task\": \"verify {len(unverified_findings)} unverified findings, "
            f"must use sandbox_exec\"}}"
        ),
    })
    continue
```

Initialize `self._full_verification_dispatched = False` in `_reset_state` / `__init__`.

---

## Bug E: Re-audit API for completed_with_gaps

### Design

New endpoint `POST /{task_id}/re-audit`:

- Only accepts `completed_with_gaps` status.
- Finds unverified findings (where `is_verified == False`).
- Sets task back to `running`, launches `_re_audit_task` background job.
- `_re_audit_task` constructs an Orchestrator that only dispatches Verification for specified finding IDs.
- After Verification completes, findings are updated in place (not duplicated).
- Task status transitions back to `completed` or `completed_with_gaps`.

### Impact

- `completed_with_gaps` tasks can be supplemented.
- Existing verified findings are preserved.
- Task status: `completed_with_gaps` -> `running` -> `completed` / `completed_with_gaps`.

---

## Bug F: Stale Running Recovery

### Design

New endpoint `POST /{task_id}/recover`:

- Only accepts `running` status.
- Checks `_running_orchestrators` dict: if orchestrator is actually running, rejects.
- If not in dict (stale), converts to `paused` status.
- After recovery, the standard resume flow applies.

### Impact

- Stale running tasks can be detected and recovered by users.
- No automatic timer needed (user-triggered).
- After recovery, the standard resume flow applies.

---

## Opt-1: Finding-Sandbox Association by Embedded ID

### Design

In `_build_sandbox_commands()`, embed a `finding_id` in each sandbox command as a comment:

```python
finding_id = f.get("id") or str(uuid4())[:8]
f["_sandbox_finding_id"] = finding_id
command = f"# FINDING_ID:{finding_id}\n" + command
```

In `_record_sandbox_attempt()`, parse the `finding_id`:

```python
finding_id_match = re.search(r"# FINDING_ID:(\S+)", command)
finding_id = finding_id_match.group(1) if finding_id_match else None
```

In `_attach_runtime_sandbox_attempts()`, use ID matching first, then fall back to fuzzy:

```python
finding_id = finding.get("_sandbox_finding_id")
if finding_id:
    matched = [a for a in attempts
               if a.get("finding_id") == finding_id
               and a.get("success") is True]
    if matched:
        finding["sandbox_attempts"] = existing_attempts + matched
        return
# Fallback to existing fuzzy matching
```

### Impact

- Precise 1:1 matching between sandbox executions and findings.
- Fuzzy matching remains as fallback for LLM-written commands without the ID comment.
- No LLM prompt change needed (the comment is injected by the system).

---

## Opt-2: Sandbox Result Persistence to Events

### Design

The `tool_output` field in `AgentEvent` is null for sandbox_exec events. Root cause is in `base.py` event emission: the `tool_output` parameter is either not passed or truncated to empty.

Fix: In `verification.py` run loop, after `observation = await self.execute_tool(...)`, when emitting the tool_result event, ensure the full observation string is passed as `tool_output` (truncated to 10000 chars).

Verify in `base.py` `emit_tool_call` / `emit_tool_result` methods that `tool_output` is actually forwarded to `AgentEvent`.

### Impact

- `tool_output` for `sandbox_exec` events will contain actual sandbox output.
- No schema change needed (field already exists in AgentEvent).

---

## Opt-3: Frontend Sandbox Evidence Display

### Design

New component `FindingSandboxEvidence.tsx` rendered inside the Finding detail panel.

- If `sandbox_attempts` is null or empty: show alert
- If present: render a list of attempt cards, each showing badge (success/failure), exit code, finding ID, collapsible command section, collapsible output/evidence section

**API type** (add to types.ts):

```typescript
interface SandboxAttempt {
  tool: string;
  success: boolean;
  exit_code: number | null;
  command: string;
  evidence_summary: string;
  target_ref?: string;
  finding_id?: string;
  weak_evidence?: boolean;
}
```

### Impact

- Users can visually verify that each finding was actually tested in the sandbox.
- No backend change (data already available via Bug A fix).

---

## Clarification Log

(Brainstorming clarifications will be appended here after Step 2)

---

## Implementation Order

1. Bug A (model + migration + save) - unblocks all other evidence-related fixes
2. Bug B (merge logic) - independent, can be done in parallel with A
3. Bug C (gate fix) - depends on B being correct
4. Opt-1 (finding ID) - depends on A for persistence
5. Opt-2 (event persistence) - independent
6. Bug D (full verification gate) - depends on C for gate behavior
7. Bug E (re-audit API) - depends on A and D
8. Bug F (recover API) - independent
9. Opt-3 (frontend display) - depends on A for data availability

## Acceptance Criteria

1. After a sandbox_exec call, `sandbox_attempts` is persisted to DB and returned by the findings API.
2. When Verification returns `is_verified=False`, the merged finding's `is_verified` is `False`.
3. `_has_valid_sandbox_evidence()` returns `False` when no finding has sandbox evidence (even if `is_verified=True`).
4. All findings are sent to Verification before the task can finish.
5. `completed_with_gaps` tasks can be re-audited via the new API.
6. Stale running tasks can be recovered via the new API.
7. Sandbox_exec events have non-null `tool_output`.
8. Frontend shows sandbox evidence per finding.
9. Finding-sandbox matching uses embedded ID for precision.

## Risk Points

- **DB migration**: Adding nullable JSON column is backward-compatible, but must run on production DB.
- **Merge logic change**: Could affect existing task results if re-run. Mitigated by only changing merge path for `is_verified`/`verification_status`/`sandbox_attempts`.
- **Re-audit API**: New background task `_re_audit_task` needs proper error handling and must not duplicate findings.
- **Gate tightening (Bug C)**: May cause more tasks to end with `completed_with_gaps` instead of `completed`. This is the intended behavior but may surprise users.
- **Finding ID injection (Opt-1)**: LLM may strip comments when rewriting commands. Fallback fuzzy matching handles this.
