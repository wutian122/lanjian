# Tasks: 验证引擎根治 - 确定性证据判定与门禁终止

## Phase 1: 证据可信度（R3 fabricated 标记）

- [x] T1: `_record_sandbox_attempt` 增加 fabricated 检测
  - File: `backend/app/services/agent/agents/verification.py`
  - evidence 含 `Simulated`/`模拟`/`simulation`/`Source file not found` 且含确认标记时打 `fabricated=True`
  - 新增模块级常量 `FABRICATION_MARKERS`

- [x] T2: `_has_valid_sandbox_evidence` 排除 fabricated 证据
  - File: `backend/app/services/agent/agents/orchestrator.py`
  - sandbox_attempts 中的 `fabricated=True` attempt 不计入有效证据

- [x] T3: 反伪造提示词
  - File: `backend/app/services/agent/agents/verification.py`
  - 系统提示与强制引导文案：源码未找到必须标 `sandbox_skip_reason`，禁止模拟/编造输出

## Phase 2: 确定性状态引擎（R1）

- [x] T4: 新增纯函数 `compute_verification_status(finding, attempts)`
  - File: `backend/app/services/agent/agents/verification.py`
  - 按 design 的 6 级规则推导 (status, is_verified, notes)

- [x] T5: `_normalize_verification_outcome` 改为调用确定性引擎
  - 不再以 LLM verdict 为状态起点
  - 保留 false_positive / sandbox_skip_reason 标注读取

## Phase 3: 全量证据强制绑定（R2）

- [x] T6: run() 收尾对 `findings_to_verify` 全量附加运行时证据
  - File: `backend/app/services/agent/agents/verification.py`
  - LLM Final Answer 处理前后各执行一次 `_attach_runtime_sandbox_attempts`

## Phase 4: 确定性沙箱执行（R3 前置执行）

- [x] T7: run() 进入 LLM 循环前对全部 sandbox_commands 确定性执行一次
  - File: `backend/app/services/agent/agents/verification.py`
  - 复用 sandbox_mgr.execute_with_files，单条 timeout 控制
  - 结果写入 `self._sandbox_attempts`（含 fabricated 标记）

## Phase 5: 门禁终止（R4）

- [x] T8: orchestrator 增加 `_finish_gate_rejections` 计数与 3 次终止
  - File: `backend/app/services/agent/agents/orchestrator.py`
  - 达上限后不再强制重派 verification，直接放行 finish
  - 记录 warning

- [x] T9: config 新增 `verification_max_force_redispatch`
  - File: `backend/app/services/agent/config.py`

## Phase 6: Bug D 判定修正（R5）

- [x] T10: `unverified_findings` 判定改为 needs_context 状态
  - File: `backend/app/services/agent/agents/orchestrator.py`

## Phase 7: 中断收口（R7）

- [x] T11: 子 Agent 中断时补发 dispatch_complete/phase_complete
  - File: `backend/app/services/agent/agents/orchestrator.py`

## Phase 8: observations 持久化（R6）

- [x] T12: orchestrator 门禁拒绝/兜底时写 observations
  - File: `backend/app/services/agent/agents/orchestrator.py`

- [x] T13: `_save_findings` 持久化 observations 到 AgentTask
  - File: `backend/app/api/v1/endpoints/agent_tasks.py`

## Phase 9: 测试

- [x] T14: 新增 `test_verification_evidence.py`
  - 确定性判定矩阵（证据→状态全组合）
  - fabricated 排除
  - ID 强制绑定（LLM 漏报仍能获得证据）

- [x] T15: 新增 `test_orchestrator_gates.py`
  - 门禁 3 次终止
  - Bug D 判定修正（needs_context 触发）
  - observations 写入

## Phase 10: 回归

- [x] T16: `uv run pytest` 全量（本机 268 通过，8 文件因缺 fastapi/litellm 依赖无法收集）
- [x] T17: `uv run ruff check .`（本机无 ruff，改用 py_compile 语法校验通过）
- [x] T18: `openspec validate fix-verification-evidence-root` 通过

## Scenario Mapping

| Scenario | Test Function |
|----------|---------------|
| 有铁证且匹配 → confirmed | test_confirmed_from_evidence_even_when_llm_omitted_verdict |
| Simulated 证据 → 不判 confirmed | test_fabricated_attempt_marked_and_excluded |
| LLM 漏报仍获得证据 | test_evidence_bound_for_all_findings_including_llm_omitted |
| 连续拒绝 3 次 → 终止重派 | test_gate_blocks_first_two_then_releases_on_third |
| needs_context → 触发全量验证门禁 | test_bugD_gate_treats_needs_context_as_unverified |
| 门禁拒绝后 observations 非空 | test_record_gate_observation_accumulates |
