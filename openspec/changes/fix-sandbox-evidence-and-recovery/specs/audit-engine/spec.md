# Delta Spec: audit-engine

## Added Requirements

### Requirement: sandbox_attempts SHALL be persisted to database

`AgentFinding` model SHALL include a `sandbox_attempts` JSON column. The `_save_findings` function SHALL persist the `sandbox_attempts` field from the runtime finding dict to this column. The `/findings` API endpoint SHALL return `sandbox_attempts` in the response.

#### Scenario: sandbox_exec evidence persisted to DB
- **WHEN** Verification Agent calls `sandbox_exec` and `_attach_runtime_sandbox_attempts` populates `finding["sandbox_attempts"]`
- **AND** `_save_findings` writes the finding to DB
- **THEN** the `agent_findings.sandbox_attempts` column contains the evidence array
- **AND** the `/findings` API response includes `sandbox_attempts` with the evidence

#### Scenario: finding without sandbox evidence has NULL
- **WHEN** a finding has no `sandbox_attempts` in the runtime dict
- **AND** `_save_findings` writes the finding to DB
- **THEN** the `agent_findings.sandbox_attempts` column is NULL
- **AND** the API response includes `sandbox_attempts: null`

### Requirement: is_verified merge SHALL respect Verification priority

When Orchestrator merges findings from Analysis and Verification, the `is_verified` and `verification_status` fields from Verification SHALL take priority over Analysis, regardless of True/False value. The merge logic SHALL NOT use `value != 0` as a guard for boolean fields (Python `False == 0` evaluates to `True`, causing `is_verified=False` to be skipped).

#### Scenario: Verification False overrides Analysis True
- **WHEN** Analysis sets `is_verified=True` on a finding
- **AND** Verification returns `is_verified=False` with `verification_status="not_reproducible"`
- **THEN** the merged finding has `is_verified=False` and `verification_status="not_reproducible"`

#### Scenario: Verification True overrides Analysis False
- **WHEN** Analysis sets `is_verified=False` on a finding
- **AND** Verification returns `is_verified=True` with `verification_status="confirmed"`
- **THEN** the merged finding has `is_verified=True` and `verification_status="confirmed"`

#### Scenario: sandbox_attempts merged as list
- **WHEN** existing finding has `sandbox_attempts=[]`
- **AND** Verification returns `sandbox_attempts=[{success: True, exit_code: 0, ...}]`
- **THEN** the merged finding has `sandbox_attempts` with 1 element

### Requirement: sandbox evidence gate SHALL require actual sandbox evidence

`_has_valid_sandbox_evidence()` SHALL NOT accept `is_verified=True` alone as evidence. The function SHALL require either:
1. A finding with `verification_status="confirmed"` AND non-empty `sandbox_attempts` with at least one successful attempt (exit_code=0, success=True), OR
2. A finding with `verification_status="static_confirmed"` (code reasoning, B3 strict standard)

#### Scenario: is_verified=True without sandbox evidence rejected
- **WHEN** all findings have `is_verified=True` but none have `sandbox_attempts`
- **THEN** `_has_valid_sandbox_evidence()` returns `False`

#### Scenario: confirmed with sandbox evidence accepted
- **WHEN** one finding has `verification_status="confirmed"` and `sandbox_attempts=[{success: True, exit_code: 0}]`
- **THEN** `_has_valid_sandbox_evidence()` returns `True`

#### Scenario: static_confirmed accepted
- **WHEN** one finding has `verification_status="static_confirmed"`
- **THEN** `_has_valid_sandbox_evidence()` returns `True`

### Requirement: all findings SHALL be sent to Verification before finish

Orchestrator SHALL check that all findings in `_all_findings` have a `verification_status` before allowing `finish`. Findings without `verification_status` and without `is_verified=True` SHALL trigger a forced Verification dispatch.

#### Scenario: unverified findings force Verification dispatch
- **WHEN** Orchestrator has 8 findings, 3 verified, 5 without verification_status
- **AND** Verification has been dispatched at least once
- **THEN** Orchestrator forces another Verification dispatch for the 5 unverified findings
- **AND** does not allow finish until all findings have verification_status

#### Scenario: all verified allows finish
- **WHEN** all findings have verification_status set
- **THEN** the full verification gate does not trigger

### Requirement: completed_with_gaps tasks SHALL be re-auditable

A new `POST /{task_id}/re-audit` endpoint SHALL allow re-running Verification on tasks with `completed_with_gaps` status. The endpoint SHALL:
1. Find findings with `is_verified=False`
2. Set task status to `running`
3. Launch a background job that dispatches Verification only for unverified findings
4. Preserve existing verified findings

#### Scenario: re-audit completed_with_gaps task
- **WHEN** POST /{task_id}/re-audit is called on a `completed_with_gaps` task
- **AND** the task has 4 unverified findings
- **THEN** task status becomes `running`
- **AND** a background Verification job is launched for the 4 findings
- **AND** the response includes `unverified_count: 4`

#### Scenario: re-audit rejected for completed task
- **WHEN** POST /{task_id}/re-audit is called on a `completed` task
- **THEN** returns 400 error with "only completed_with_gaps tasks can be re-audited"

#### Scenario: re-audit rejected when all verified
- **WHEN** POST /{task_id}/re-audit is called on a `completed_with_gaps` task
- **AND** all findings are already verified
- **THEN** returns 400 error with "all findings already verified"

### Requirement: stale running tasks SHALL be recoverable

A new `POST /{task_id}/recover` endpoint SHALL detect stale running tasks (status=running but orchestrator not in `_running_orchestrators`) and convert them to `paused` status for user-initiated resume.

#### Scenario: recover stale running task
- **WHEN** POST /{task_id}/recover is called on a `running` task
- **AND** the task is not in `_running_orchestrators` (process died)
- **THEN** task status becomes `paused`
- **AND** task.pause_reason is "stale_running_recovered"
- **AND** the response message indicates the task can be resumed

#### Scenario: recover rejected for actually running task
- **WHEN** POST /{task_id}/recover is called on a `running` task
- **AND** the task IS in `_running_orchestrators` (actually running)
- **THEN** returns 400 error with "task is actually running, no recovery needed"

#### Scenario: recover rejected for non-running task
- **WHEN** POST /{task_id}/recover is called on a `paused` task
- **THEN** returns 400 error with "only running tasks can be recovered"

### Requirement: sandbox_exec events SHALL have non-null tool_output

When Verification Agent executes `sandbox_exec`, the resulting `AgentEvent` SHALL have `tool_output` populated with the sandbox execution result (stdout, stderr, exit_code, success indicator), truncated to 10000 characters.

#### Scenario: sandbox_exec event has output
- **WHEN** Verification Agent calls `sandbox_exec` and receives an observation
- **THEN** the `AgentEvent` with `event_type="tool_result"` and `tool_name="sandbox_exec"` has `tool_output` containing the observation string

### Requirement: finding-sandbox matching SHALL use embedded finding_id

The Verification Agent SHALL embed a `# FINDING_ID:{id}` comment in each sandbox command. The `_record_sandbox_attempt` function SHALL parse this ID. The `_attach_runtime_sandbox_attempts` function SHALL use ID-based matching first, falling back to fuzzy file-path/keyword matching when no ID is present.

#### Scenario: ID-based matching
- **WHEN** a sandbox command contains `# FINDING_ID:abc123`
- **AND** a finding has `_sandbox_finding_id="abc123"`
- **THEN** the sandbox attempt is attached to that finding directly via ID match

#### Scenario: fallback to fuzzy matching
- **WHEN** a sandbox command does not contain `# FINDING_ID` comment
- **AND** the finding has no `_sandbox_finding_id`
- **THEN** the existing fuzzy file-path/keyword matching is used

### Requirement: frontend SHALL display sandbox evidence per finding

The Agent Audit frontend SHALL render a sandbox evidence panel in the finding detail view, showing each `sandbox_attempt` with its command, output, exit code, and success/failure status.

#### Scenario: finding with sandbox evidence
- **WHEN** a finding has `sandbox_attempts=[{success: True, exit_code: 0, command: "...", evidence_summary: "..."}]`
- **THEN** the finding detail panel shows a sandbox evidence card with success badge, exit code, collapsible command, and collapsible output

#### Scenario: finding without sandbox evidence
- **WHEN** a finding has `sandbox_attempts=null` or `[]`
- **THEN** the finding detail panel shows an alert indicating no sandbox verification was performed
