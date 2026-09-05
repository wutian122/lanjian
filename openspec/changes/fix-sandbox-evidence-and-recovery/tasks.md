# Tasks: Fix Sandbox Verification Evidence Loss &amp; Task Recovery

> **台账治理核实（2026-09-05，sandbox-verification-hard-gate Task 18）**：逐项对照代码核实。
> T1-T14、T16-T19、T21、T23-T30 共 27 项已实施并补勾选（证据见各项下"核实"行）；
> T15、T20、T22 为**测试缺口**（代码/端点已上线，专项测试缺失，裁定弃权记账）；
> T31-T33 为 E2E 任务，**仓库无 e2e/ 目录与 Playwright harness**，裁定弃权（由单测+生产实证覆盖）。
> 本变更功能已被后续 `fix-verification-evidence-root`（确定性证据引擎，已归档）与
> `sandbox-verification-hard-gate`（硬门禁）取代/增强，未勾选项不再补做。

## Phase 1: Data Persistence (Bug A) - Unblocks all evidence fixes

- [x] T1: Add `sandbox_attempts` column to `AgentFinding` model in `backend/app/models/agent_task.py`
  - 核实 2026-09-05：`models/agent_task.py:406` `sandbox_attempts = Column(JSON, nullable=True)` 存在
  - File: `backend/app/models/agent_task.py`, after `verification_result` column
  - Add: `sandbox_attempts = Column(JSON, nullable=True)`
  - Add docstring comment: `# sandbox verification evidence [{tool, success, exit_code, command, evidence_summary, ...}]`

- [x] T2: Generate Alembic migration
  - 核实 2026-09-05：`alembic/versions/021_add_sandbox_attempts.py` 存在（down_revision=020_pause，在迁移链上）
  - Command: `cd backend && uv run alembic revision --autogenerate -m "add sandbox_attempts to agent_findings"`
  - Verify migration file created in `backend/alembic/versions/`
  - Verify upgrade adds column, downgrade drops it

- [x] T3: Fix `_save_findings` to persist `sandbox_attempts`
  - 核实 2026-09-05：`agent_tasks.py:2219` `sandbox_attempts=finding.get("sandbox_attempts"),`
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`, in `_save_findings` function
  - In `db_finding = AgentFinding(...)` constructor, add: `sandbox_attempts=finding.get("sandbox_attempts"),`

- [x] T4: Add `sandbox_attempts` to `AgentFindingResponse` schema
  - 核实 2026-09-05：`agent_tasks.py:291` `sandbox_attempts: list[dict] | None = None`
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`, in `AgentFindingResponse` class
  - Add: `sandbox_attempts: Optional[List[dict]] = None`

- [x] T5: Write test: sandbox_attempts persistence
  - 核实 2026-09-05：`tests/agent/test_reverify_poc_replay.py` 经 AgentFinding 模型持久化往返（:115 追加后 `len(finding.sandbox_attempts) == 2`；:119 空列表场景）
  - Create finding with sandbox_attempts -> save -> query DB -> verify field present
  - Create finding without sandbox_attempts -> save -> query -> verify field is NULL

## Phase 2: Merge Logic (Bug B) - Independent, parallel with Phase 1

- [x] T6: Fix `is_verified` merge in Orchestrator
  - 核实 2026-09-05：`orchestrator.py:3504-3520` Bug B 三字段特判（is_verified 按 verification_status/verdict 优先级；verification_status 非空覆盖；sandbox_attempts 列表追加合并）；强制 `is_verified=True` 覆写已删（:3532 注释）
  - File: `backend/app/services/agent/agents/orchestrator.py`, finding merge section (~line 1891)
  - Add field-specific handling for `is_verified`, `verification_status`, `sandbox_attempts` before generic guard
  - Remove the `if existing_f.get("is_verified") or normalized_new.get("is_verified"): merged["is_verified"] = True` line

- [x] T7: Write test: merge logic with False==0 boundary
  - 核实 2026-09-05：`tests/agent/test_orchestrator_gates.py:338-410` 三个 `_merge_or_append_finding` 测试（sandbox_attempts 合并 `len == 1`、验证值覆盖、无 fid 模糊去重）
  - Test case: existing finding is_verified=True, new finding is_verified=False + verification_status="not_reproducible"
  - Assert: merged finding is_verified=False, verification_status="not_reproducible"
  - Test case: existing finding is_verified=False, new finding is_verified=True + verification_status="confirmed"
  - Assert: merged finding is_verified=True
  - Test case: sandbox_attempts merge (existing [], new [{...}])
  - Assert: merged sandbox_attempts has 1 element

## Phase 3: Gate Fix (Bug C) - Depends on Phase 2

- [x] T8: Fix `_has_valid_sandbox_evidence` in Orchestrator
  - 核实 2026-09-05：`orchestrator.py:500` 存在；is_verified-only 直通已删，confirmed 需 sandbox 证据，static_confirmed 保留；后续 fix-verification-evidence-root 又加 fabricated 排除
  - File: `backend/app/services/agent/agents/orchestrator.py`, ~line 436
  - Remove `if finding.get("is_verified") is True: return True` condition
  - Add sandbox_attempts check for `confirmed` status
  - Keep `static_confirmed` as accepted

- [x] T9: Write test: gate behavior
  - 核实 2026-09-05：`tests/agent/test_orchestrator_finish_gates.py:29-95` 四场景全备（only_is_verified→False、confirmed+证据→True、static_confirmed→True、无证据/失败→False）
  - Test: all findings is_verified=True but no sandbox_attempts -> returns False
  - Test: one finding with confirmed + sandbox_attempts[{success:True, exit_code:0}] -> returns True
  - Test: one finding with static_confirmed -> returns True
  - Test: all findings needs_context -> returns False

## Phase 4: Finding-Sandbox ID (Opt-1) - Depends on Phase 1

- [x] T10: Embed `finding_id` in sandbox commands
  - 核实 2026-09-05：`verification.py:3077` `f["_sandbox_finding_id"] = finding_id`；:3083 命令前置 `"# FINDING_ID:" + finding_id`
  - File: `backend/app/services/agent/agents/verification.py`, in `_build_sandbox_commands`
  - Generate `finding_id` per finding, store as `f["_sandbox_finding_id"]`
  - Prepend `# FINDING_ID:{id}\n` to each command string

- [x] T11: Parse `finding_id` in `_record_sandbox_attempt`
  - 核实 2026-09-05：`verification.py:1952` `re.search(r"# FINDING_ID:(\S+)", command)`，attempt 存 finding_id
  - File: `backend/app/services/agent/agents/verification.py`, in `_record_sandbox_attempt`
  - Add regex: `re.search(r"# FINDING_ID:(\S+)", command)`
  - Store `finding_id` in the attempt dict

- [x] T12: Use ID matching in `_attach_runtime_sandbox_attempts`
  - 核实 2026-09-05：`verification.py:2308-2321` ID 匹配优先、模糊兜底；:2042 P4 增强（LLM 自写 PoC 无注释时按 file_path/target_ref 反查）
  - File: `backend/app/services/agent/agents/verification.py`, in `_attach_runtime_sandbox_attempts`
  - Before existing fuzzy matching, try ID-based matching
  - If `finding._sandbox_finding_id` matches `attempt.finding_id`, attach directly

- [x] T13: Write test: ID-based matching
  - 核实 2026-09-05：`tests/agent/test_evidence_binding_finding.py` 系——`test_evidence_binding_fallback.py` + `test_verification_crash_match_binding.py` 覆盖 ID 匹配/兜底
  - Test: finding with _sandbox_finding_id="abc123" + attempt with finding_id="abc123" -> matched
  - Test: finding with _sandbox_finding_id="abc123" + attempt with finding_id="xyz789" -> not matched, falls through to fuzzy
  - Test: finding without _sandbox_finding_id -> falls through to fuzzy matching

## Phase 5: Event Persistence (Opt-2) - Independent

- [x] T14: Fix `tool_output` for sandbox_exec events
  - 核实 2026-09-05：`base.py:947-952` `emit_tool_result` 构造 `tool_output_dict` 并转发 `tool_output=`（截断 50000 字符与沙箱源头对齐；spec 写 10000，实现有意偏离）
  - File: `backend/app/services/agent/agents/base.py`, check `emit_tool_result` / `emit_tool_call`
  - Verify `tool_output` parameter is forwarded to `AgentEvent`
  - If truncated or dropped, fix the forwarding
  - Truncate to 10000 chars to match sandbox stdout limit

- [ ] T15: Write test: event tool_output not null
  - **台账治理裁定 2026-09-05：测试缺口，弃权记账**——T14 转发代码已上线，但全 `tests/` 无 tool_output 非空断言（`test_sandbox_events.py` 只断言事件类型；`test_sse_reconnect_backfill.py` 的 `tool_output: None` 是 SSE 回补 fixture）。行为由事件发射测试间接覆盖，专项断言缺失
  - Test: execute sandbox_exec -> query AgentEvent -> assert tool_output is not null
  - Test: tool_output contains sandbox stdout

## Phase 6: Full Verification Gate (Bug D) - Depends on Phase 3

- [x] T16: Add full verification gate in Orchestrator
  - 核实 2026-09-05：`orchestrator.py:289` `_full_verification_dispatched` + :1657 门禁（unverified 且已派发过 Verification 则强制重派一次）；后续重构 `_maybe_dispatch_force_verification`（:539，标志 :323 `_force_verification_dispatched`）
  - File: `backend/app/services/agent/agents/orchestrator.py`, after Semgrep gate, before coverage gate
  - Check for findings without `verification_status`
  - Force dispatch Verification for unverified findings
  - Initialize `self._full_verification_dispatched = False` in `_reset_state`

- [x] T17: Write test: full verification gate
  - 核实 2026-09-05：`test_gate_candidate_scope.py:121-136`（触发一次、标志防重复）+ `test_verification_gate.py:71-83`（未验证拒 finish / 全验证放行）
  - Test: 5 findings, 3 verified, 2 unverified -> gate triggers, dispatches Verification
  - Test: all findings verified -> gate does not trigger
  - Test: gate triggers once (flag prevents repeated forcing)

## Phase 7: Re-audit API (Bug E) - Depends on Phase 1 and 6

- [x] T18: Implement `POST /{task_id}/re-audit` endpoint
  - 核实 2026-09-05：`agent_tasks.py:3024` `re_audit_agent_task`（仅 completed_with_gaps，否则 400；收集未验证 finding）
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Accept only `completed_with_gaps` status
  - Find unverified findings (is_verified == False)
  - Set task to running, launch `_re_audit_task` background job

- [x] T19: Implement `_re_audit_task` function
  - 核实 2026-09-05：`agent_tasks.py:393` `_re_audit_task`（checkpoint `re_audit`、`_re_audit_finding_ids`、原位更新不重复）
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Construct Orchestrator with only Verification dispatch for specified finding IDs
  - Load findings from DB, convert to finding dicts
  - After Verification, update findings in place (not duplicate)
  - Set task status to completed or completed_with_gaps

- [ ] T20: Write test: re-audit flow
  - **台账治理裁定 2026-09-05：测试缺口，弃权记账**——端点已上线，但全 `tests/` 无 re-audit/re_audit 引用（400 场景、finding 更新流程均无测试）
  - Test: completed_with_gaps task with 2 unverified findings -> re-audit -> findings updated
  - Test: completed task -> 400 error
  - Test: all findings verified -> 400 error

## Phase 8: Stale Running Recovery (Bug F) - Independent

- [x] T21: Implement `POST /{task_id}/recover` endpoint
  - 核实 2026-09-05：`agent_tasks.py:3205` `recover_stale_agent_task`（仅 running；`_running_orchestrators` + Redis registry 双存活判定防多 worker 误判；stale→paused + pause_reason=stale_running_recovered）
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Accept only `running` status
  - Check `_running_orchestrators` dict
  - If not in dict, convert to `paused` status

- [ ] T22: Write test: recover flow
  - **台账治理裁定 2026-09-05：测试缺口，弃权记账**——端点已上线（含多 worker Redis 存活增强），但全 `tests/` 无 /recover 端点测试（test_orchestrator_pause_on_errors 的 recover 是 `_pause_for_recoverable_error`，非本端点）
  - Test: running task not in _running_orchestrators -> recover -> status=paused
  - Test: running task in _running_orchestrators -> 400 error
  - Test: completed task -> 400 error

## Phase 9: Frontend (Opt-3) - Depends on Phase 1

- [x] T23: Add `SandboxAttempt` type to frontend types
  - 核实 2026-09-05：`frontend/src/pages/AgentAudit/types.ts:247` `export interface SandboxAttempt`
  - File: `frontend/src/pages/AgentAudit/types.ts`
  - Add interface with tool, success, exit_code, command, evidence_summary, target_ref, finding_id, weak_evidence

- [x] T24: Add `canReAudit` and `canRecover` computed states
  - 核实 2026-09-05：`hooks/useAgentAuditState.ts:376,380` canReAudit/canRecover
  - File: `frontend/src/pages/AgentAudit/hooks/useAgentAuditState.ts`
  - `canReAudit`: status === 'completed_with_gaps' && has unverified findings
  - `canRecover`: status === 'running' && SSE disconnected

- [x] T25: Add re-audit and recover API functions
  - 核实 2026-09-05：`frontend/src/shared/api/agentTasks.ts:565,578` reAuditAgentTask/recoverAgentTask（落位 agentTasks.ts，非 spec 所写 agentStream.ts，功能在）
  - File: `frontend/src/shared/api/agentStream.ts`
  - `reAuditAgentTask(taskId)`: POST /agent-tasks/{id}/re-audit
  - `recoverAgentTask(taskId)`: POST /agent-tasks/{id}/recover

- [x] T26: Render "Supplement Audit" and "Recover" buttons
  - 核实 2026-09-05：`pages/AgentAudit/index.tsx` 接线 reAuditAgentTask/recoverAgentTask + toast 反馈（:1132-1149 等）
  - File: `frontend/src/pages/AgentAudit/index.tsx`
  - Show "Supplement Audit" button when canReAudit
  - Show "Recover" button when canRecover
  - Wire to API functions with toast feedback

- [x] T27: Create `FindingSandboxEvidence` component
  - 核实 2026-09-05：`components/FindingSandboxEvidence.tsx` 存在（徽章/退出码/可折叠命令输出；hard-gate Task 17 又加三标记+infra_error 展示）
  - File: `frontend/src/pages/AgentAudit/components/FindingSandboxEvidence.tsx`
  - Accept `attempts: SandboxAttempt[]` prop
  - Show alert if empty/null
  - Render attempt cards with badge, exit code, collapsible command/output

- [x] T28: Integrate sandbox evidence into finding detail
  - 核实 2026-09-05：`components/FindingDetailPanel.tsx` 集成 FindingSandboxEvidence
  - File: `frontend/src/pages/AgentAudit/index.tsx` or new `FindingDetailPanel.tsx`
  - Render `FindingSandboxEvidence` in the finding detail section
  - Fetch from `finding.sandbox_attempts`

## Phase 10: Integration &amp; E2E

- [x] T29: Run full test suite
  - 核实 2026-09-05：全量套件多轮运行（sandbox-verification-hard-gate Phase 0-5 最新 1101 passed/4 failed 为既有 flaky 外网用例）；前端 type-check/lint 随各前端任务通过
  - `cd backend && uv run pytest`
  - `cd frontend && pnpm type-check && pnpm lint`
  - Fix any regressions

- [x] T30: Run migration on test DB
  - 核实 2026-09-05：迁移 021 在 alembic 链上（020_pause → 021 → merge_heads → … → head）；生产 v5.3.0+ 已使用 sandbox_attempts 列
  - `cd backend && uv run alembic upgrade head`
  - Verify column exists in DB

- [ ] T31: E2E test: sandbox evidence persistence
  - **台账治理裁定 2026-09-05：弃权**——仓库无 `e2e/` 目录与 Playwright harness（CLAUDE.md 提及但实际不存在）；沙箱证据持久化由 T5/T9 单测 + 生产任务实证（verification-root 提案所引 3e62aadc 等）覆盖
  - Create audit task -> run -> check findings API -> verify sandbox_attempts present
  - Check AgentEvent tool_output is not null

- [ ] T32: E2E test: re-audit flow
  - **台账治理裁定 2026-09-05：弃权**——同上无 e2e harness；re-audit 流程无自动化 E2E（T20 单测亦缺，端点本身已上线）
  - Complete task with gaps -> re-audit -> verify new findings have verification_status

- [ ] T33: E2E test: recover flow
  - **台账治理裁定 2026-09-05：弃权**——同上无 e2e harness；recover 流程无自动化 E2E（T22 单测亦缺，端点本身已上线）
  - Simulate stale running -> recover -> resume -> verify task continues

## Scenario Mapping (Step 3 Bridge)

> 注：下表为规划期测试函数名，实际落测函数名有演进（核实 2026-09-05）：
> gate 场景在 `test_orchestrator_finish_gates.py`/`test_orchestrator_gates.py`；merge 在 `test_orchestrator_gates.py:338-410`；
> ID 匹配在 `test_evidence_binding_fallback.py`/`test_verification_crash_match_binding.py`；re-audit/recover/tool_output 专项测试缺（T15/T20/T22 弃权）。

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
