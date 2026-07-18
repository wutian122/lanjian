# Delta Spec: audit-engine

## Added Requirements

### Requirement: Task SHALL emit structured initialization progress events

`_execute_agent_task` SHALL set task status to `initializing` before starting preparation (Docker init, ZIP extraction, RAG indexing, Semgrep pre-scan). The backend SHALL emit `info` events with `metadata.init_step` and `metadata.init_status` fields during each preparation step, so the frontend can display real-time progress.

#### Scenario: Init events emitted during preparation
- **WHEN** a new task is created and `_execute_agent_task` starts
- **THEN** task status is set to `initializing`
- **AND** an `info` event with `metadata.init_step="Docker sandbox"` is emitted
- **AND** an `info` event with `metadata.init_step="Extracting project"` is emitted
- **AND** an `info` event with `metadata.init_step="Indexing code"` is emitted
- **AND** an `info` event with `metadata.init_step="Running Semgrep scan"` is emitted

#### Scenario: Status transitions to running after preparation
- **WHEN** all preparation steps are complete
- **THEN** task status is set to `running`
- **AND** the Orchestrator starts its main loop

### Requirement: Frontend SHALL display initialization progress

The Agent Audit page SHALL show an `InitProgress` component when task status is `pending` or `initializing`. The component SHALL display each preparation step with its current status (running or done) as received from SSE events.

#### Scenario: InitProgress shows during initialization
- **WHEN** user navigates to a task with status `initializing`
- **THEN** the `InitProgress` component is rendered instead of the loading spinner
- **AND** each init step event adds a new step to the progress display
- **AND** the current step shows a spinner

#### Scenario: Smooth transition to audit view
- **WHEN** task status transitions from `initializing` to `running`
- **THEN** the `InitProgress` component is replaced by the normal audit log view
- **AND** SSE stream remains connected (no reconnection needed)

### Requirement: SSE SHALL connect during initialization phase

The SSE stream SHALL connect when task status is `pending`, `initializing`, or `running` (not only `running`). This allows real-time init events to reach the frontend during the preparation phase.

#### Scenario: SSE connects on pending status
- **WHEN** user navigates to a newly created task with status `pending`
- **THEN** SSE connects immediately (without waiting for historical events)
- **AND** init events are received in real-time

#### Scenario: SSE stays connected through status transition
- **WHEN** task status changes from `initializing` to `running`
- **THEN** SSE connection is maintained without reconnection
- **AND** subsequent audit events are received normally
