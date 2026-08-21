# Delta Spec: audit-engine

## ADDED Requirements

### Requirement: verification status SHALL be determined by sandbox evidence

The Verification Agent SHALL derive each finding's `verification_status` and `is_verified` from the runtime sandbox evidence (`sandbox_attempts`) via a deterministic pure function `compute_verification_status`, NOT from the LLM's self-reported verdict in the Final Answer. The LLM's `verdict` field SHALL only be honored as an explicit `false_positive` annotation or when reading `sandbox_skip_reason`; it SHALL NOT be the starting point for the status.

#### Scenario: confirmed from evidence even when LLM omitted verdict
- **WHEN** a finding has a sandbox attempt with `success=True`, `exit_code=0`, evidence containing `VULNERABILITY_CONFIRMED`, and the attempt matches the finding
- **AND** the LLM's Final Answer did not set `verification_status=confirmed`
- **THEN** the deterministic engine sets `verification_status="confirmed"` and `is_verified=True`

#### Scenario: no evidence without skip_reason is needs_context
- **WHEN** a finding has no sandbox attempts and no `sandbox_skip_reason`
- **THEN** the deterministic engine sets `verification_status="needs_context"` and `is_verified=False`

#### Scenario: attempts without confirmation are not_reproducible
- **WHEN** a finding has sandbox attempts but none produced confirmation evidence
- **THEN** the deterministic engine sets `verification_status="not_reproducible"` and `is_verified=False`

#### Scenario: explicit false_positive is preserved
- **WHEN** the LLM Final Answer marks a finding `verdict=false_positive`
- **AND** there is no confirmed evidence for it
- **THEN** the deterministic engine sets `verification_status="false_positive"` and `is_verified=False`

### Requirement: fabricated sandbox evidence SHALL be excluded

The Verification Agent SHALL detect fabricated sandbox output (evidence containing `Simulated`, `模拟`, `simulation`, or `Source file not found` alongside a confirmation claim) and mark such attempts with `fabricated=True`. Fabricated attempts SHALL NOT count as valid evidence in `compute_verification_status` nor in the Orchestrator's `_has_valid_sandbox_evidence`.

#### Scenario: simulated output does not confirm
- **WHEN** a sandbox attempt output contains `Simulated` and claims `VULNERABILITY_CONFIRMED`
- **THEN** the attempt is marked `fabricated=True`
- **AND** it does not upgrade the finding to `confirmed`
- **AND** `_has_valid_sandbox_evidence()` does not accept it

#### Scenario: real sandbox execution is unaffected
- **WHEN** a sandbox attempt reads the real source file and outputs `VULNERABILITY_CONFIRMED` with `exit_code=0`
- **THEN** the attempt is NOT marked fabricated
- **AND** it can upgrade the finding to `confirmed`

### Requirement: runtime evidence SHALL be bound to every finding by ID

The Verification Agent SHALL attach runtime sandbox attempts to ALL findings under verification (using `_sandbox_finding_id` matching first, fuzzy path matching as fallback), including findings the LLM omitted from its Final Answer. No finding SHALL be left without its runtime evidence solely because the LLM did not report it.

#### Scenario: evidence bound even when LLM omits finding
- **WHEN** a finding is in `findings_to_verify` but missing from the LLM Final Answer findings
- **AND** runtime sandbox attempts exist with a matching `finding_id`
- **THEN** the finding receives its `sandbox_attempts` from the runtime evidence
- **AND** its verification status is derived from that evidence

### Requirement: sandbox evidence gate SHALL terminate after max redispatch

The Orchestrator SHALL count consecutive finish rejections caused by missing valid sandbox evidence. When the count reaches `verification_max_force_redispatch` (default 3), the Orchestrator SHALL stop forcing Verification redispatch and SHALL allow finish to proceed (task completes with `completed_with_gaps` when coverage is insufficient).

#### Scenario: gate terminates after max rejections
- **WHEN** the Orchestrator has rejected finish 3 times because no finding has valid sandbox evidence
- **THEN** it no longer forces another Verification dispatch
- **AND** it proceeds to the coverage check and finalization

### Requirement: full-verification gate SHALL treat needs_context as unverified

The Orchestrator's full-verification gate SHALL consider findings with `verification_status="needs_context"` as unverified (in addition to missing status) and force a Verification dispatch for them before finish. `confirmed`, `static_confirmed`, `not_reproducible`, and `false_positive` SHALL be treated as terminal.

#### Scenario: needs_context finding triggers full-verification gate
- **WHEN** a finding has `verification_status="needs_context"` and `is_verified=False`
- **AND** Verification has been dispatched at least once
- **THEN** the full-verification gate triggers and forces another Verification dispatch

### Requirement: gate rejection reasons SHALL be persisted to observations

The Orchestrator SHALL append gate rejection reasons (missing sandbox evidence, Semgrep gate, full-verification gate) and coverage bypass info to `agent_tasks.observations` so that gap reasons are traceable. `_save_findings`/task finalization SHALL persist the observations JSON to the database.

#### Scenario: observations persisted after gate rejection
- **WHEN** the Orchestrator rejects finish due to missing sandbox evidence
- **AND** the task finalizes as `completed_with_gaps`
- **THEN** `agent_tasks.observations` is non-empty and contains the rejection reason

### Requirement: interrupted verification sub-agent SHALL emit completion events

When a Verification (or any) sub-agent is interrupted by timeout or cancellation, the Orchestrator SHALL emit a `dispatch_complete` (with `interrupted=True`) and a `phase_complete` event so the event stream remains complete.

#### Scenario: interrupted sub-agent closes its phase
- **WHEN** a sub-agent is interrupted (timeout or cancellation) mid-run
- **THEN** the Orchestrator emits `dispatch_complete` with `interrupted=True`
- **AND** emits `phase_complete` for the sub-agent's phase
