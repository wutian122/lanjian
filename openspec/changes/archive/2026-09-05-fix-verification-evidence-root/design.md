# Design: 验证引擎根治 - 确定性证据判定与门禁终止

## Architecture Context

```
Orchestrator (orchestrator.py)
  |- finish 门禁链（三层）：
  |    L1 沙箱证据门禁 _has_valid_sandbox_evidence()   [R4: 3次终止]
  |    L2 Semgrep 门禁                               [保留]
  |    L3 全量验证门禁 unverified_findings            [R5: 判定修正]
  |- 覆盖率门禁 / 兜底放行                            [保留，但拒绝原因写 observations]
  |- observations 记录与落库                          [R6]

Verification Agent (verification.py)
  |- run() ReAct 循环（LLM 参与，保留）
  |    +- R3 确定性沙箱执行：进入循环前先执行全部预生成 PoC
  |    +- _record_sandbox_attempt()  -> 增加 fabricated 标记 [R3]
  |    +- _attach_runtime_sandbox_attempts() -> 全量按 finding_id 强制绑定 [R2]
  |    +- _normalize_verification_outcome() -> 改为确定性引擎 [R1]
  |         +- compute_verification_status(finding, attempts) [R1 纯函数]
  +- 中断收口：dispatch_complete/phase_complete 补发 [R7]

agent_tasks.py
  |- _save_findings() -> observations 持久化 [R6]
```

## R1: 确定性验证状态引擎

### 根因

`_normalize_verification_outcome`（verification.py:1721-1825）以 `finding.get("verification_status") or verdict or NEEDS_CONTEXT` 为起点，只处理"LLM 说 confirmed 但无证据→降级"，**没有"有证据但 LLM 没写 confirmed→升级"分支**。因此真实沙箱铁证（success=True + exit_code=0 + VULNERABILITY_CONFIRMED）被白白浪费，状态停留 needs_context，门禁永远失败。

### 设计

新增纯函数（模块级，可单测）：

```python
def compute_verification_status(finding: dict, attempts: list) -> tuple[str, bool, dict]:
    """由运行时沙箱证据确定性推导验证状态。
    返回 (verification_status, is_verified, notes)。
    证据优先级高于 LLM 自述；LLM 的 false_positive / sandbox_skip_reason 仅作标注。
    """
```

判定规则（严格优先级）：
1. 过滤 `fabricated` 证据（R3 标记），仅用可信 attempts。
2. **confirmed**：存在 attempt 满足 `success is True and exit_code == 0` 且 `_attempt_has_vuln_evidence(a)`（VULNERABILITY_CONFIRMED 等）且 `_sandbox_attempt_matches_finding(a, finding)`。
3. **static_confirmed**：无动态铁证，但 (a) 存在 success attempt 且 `weak_evidence`（方案C宽松兜底），或 (b) semgrep 确定性类型（hardcoded_secret/weak_crypto/deserialization/xxe）。
4. **false_positive**：LLM 显式标 verdict=false_positive，且无 confirmed 证据。
5. **not_reproducible**：存在 attempts 但均未确认（跑了但没复现）。
6. **needs_context**：无任何 attempts，且有 `sandbox_skip_reason`（如实说明无法验证）；或完全无证据无 skip。
   - 与 not_reproducible 的区分：not_reproducible = 尝试过但失败；needs_context = 未尝试/无法尝试。

`_normalize_verification_outcome` 改为调用该纯函数，不再以 LLM verdict 为状态起点。LLM 的 verdict 仅用于：
- `false_positive` 标注（保留）
- `sandbox_skip_reason` 读取（保留）
- 其余一律以证据计算为准。

## R2: 全量证据强制绑定

### 根因

`_attach_runtime_sandbox_attempts` 在 run() 中只对 `final_result["findings"]`（LLM Final Answer 里出现的 findings）调用（verification.py:1174-1177）。LLM 漏报的 finding 永远不会附加运行时证据 → 证据丢失（本任务 4/5）。

### 设计

在 run() 收尾处（构造 `verified_findings` 后、返回前），**对 `findings_to_verify` 全量遍历**：
- 若 finding 已有 attempts（LLM Final Answer 已带或已附加）→ 跳过。
- 否则调用 `_attach_runtime_sandbox_attempts(f)` 按 finding_id（`_sandbox_finding_id`）强制附加运行时证据。
- ID 匹配失败时走现有模糊匹配兜底（`_sandbox_attempt_matches_finding` / 方案C weak_evidence）。
- 该步骤在 LLM Final Answer 处理前后各执行一次（前=尝试附加，后=兜底补漏），保证全覆盖。

## R3: 确定性沙箱执行 + 反伪造

### 根因

验证证据完全依赖 LLM 主动调用 sandbox_exec。LLM 在"源码读不到/不耐烦"时输出 `Simulated trust-all context` + 假 JWT + VULNERABILITY_CONFIRMED，被 `_record_sandbox_attempt` 记为 success=True，可被门禁当有效证据。

### 设计

1. **确定性前置执行**：run() 进入 LLM 循环前，对每个 `sandbox_commands` 逐条在沙箱执行一次（复用现有兜底逻辑 `sandbox_mgr.execute_with_files`），结果写入 `self._sandbox_attempts`。LLM 循环保留用于复杂漏洞的补充/动态验证，但"是否验证过"不再依赖 LLM。
   - 受控：单条命令超时（现有 30s/60s）；全部失败不影响流程（证据如实记录）。
2. **fabricated 标记**：`_record_sandbox_attempt` 增加检测——evidence 含 `Simulated`/`模拟`/`simulation`/`Source file not found` 且同时声称确认 → 打 `fabricated=True`。`compute_verification_status` 与 `_has_valid_sandbox_evidence` 一律排除 fabricated。
3. **反伪造提示词**：系统提示与强制引导文案增加"源码未找到时必须输出 sandbox_skip_reason 如实说明，禁止模拟/编造 PoC 输出"。

## R4: 门禁确定性终止

### 根因

orchestrator.py:861 finish 门禁对"有发现但无有效沙箱证据"无限拒绝并强制重派 verification，无次数上限，烧 token 直到 max_iterations 耗尽。

### 设计

- 新增 `self._finish_gate_rejections = 0`（`__init__` 初始化）。
- 每次 L1 门禁拒绝时 `self._finish_gate_rejections += 1` 并写入 observations（R6）。
- 当 `self._finish_gate_rejections >= config.verification_max_force_redispatch`（默认 3）：
  - 不再强制重派 verification，改为直接放行 finish（带 `completed_with_gaps` 语义，由覆盖率兜底/正常收尾处理）。
  - 记录 warning："验证门禁已达最大重试次数，按覆盖率不足完成"。
- 保留"审计轮次已耗尽"终极兜底。

## R5: Bug D 全量验证门禁判定修正

### 根因

orchestrator.py:949-953 `unverified_findings` 判 `not f.get("verification_status")`，但 analysis 默认写 needs_context（truthy）→ 恒空 → 门禁永不触发。

### 设计

```python
UNVERIFIED_STATUSES = {"needs_context"}  # 需补充验证的状态
unverified_findings = [
    f for f in self._all_findings
    if f.get("verification_status") in UNVERIFIED_STATUSES
    and f.get("is_verified") is not True
]
```

即：状态为 needs_context（未确认/未尝试）的 finding 视为未验证，触发强制调度 Verification。confirmed/static_confirmed/not_reproducible/false_positive 视为已终结，不触发。

## R6: observations 持久化

### 根因

`agent_tasks.observations`（agent_task.py:124）无任何写入点。

### 设计

orchestrator 完成/兜底时把以下内容写入 observations（JSON 数组）：
- 每次门禁拒绝（L1/L2/L3）的 reason + 时间。
- 覆盖率兜底放行的 `_coverage_bypass_info`（reason/covered_count/gaps/block_count）。
- `_save_findings` / 任务收尾处把 orchestrator 返回的 observations 写入 `AgentTask.observations`。

## R7: 子 Agent 中断收口

### 根因

本任务 verification #2（dispatch 02:43:10）无 dispatch_complete/phase_complete，02:48:01 直接开始 #3——子 Agent 被中断（超时/取消）未收口。

### 设计

orchestrator `_dispatch_agent` 的 `run_with_cancel_check` 包裹逻辑：子 Agent 因超时/取消/异常退出时，补发 `dispatch_complete`（带 `interrupted=True`）与 `phase_complete`，保证事件流完整。

## Implementation Order

1. R3 fabricated 标记 + 反伪造（verification.py）——证据可信度是后续判定的前提。
2. R1 确定性状态引擎 `compute_verification_status`（verification.py）——纯函数，先行可单测。
3. R2 全量证据强制绑定（verification.py）。
4. R4 门禁 3 次终止 + observations 记录（orchestrator.py）。
5. R5 Bug D 判定修正（orchestrator.py）。
6. R7 中断收口（orchestrator.py）。
7. R6 observations 持久化（agent_tasks.py）+ config 新增项。
8. 测试 + 全量回归。

## Acceptance Criteria

1. 有 `success+exit0+VULNERABILITY_CONFIRMED` 匹配证据的 finding，verification_status 必为 confirmed（即使 LLM 未写 confirmed）。
2. 含 Simulated/模拟 标记的证据不被判定为 confirmed，也不被门禁接受。
3. LLM Final Answer 漏报的 finding 仍能获得运行时 sandbox_attempts（ID 强制绑定）。
4. orchestrator 连续拒绝 finish 达 3 次后不再强制重派 verification，直接按 coverage 收尾。
5. `unverified_findings` 对 needs_context 状态生效（Bug D 门禁可触发）。
6. `agent_tasks.observations` 在门禁拒绝/兜底放行后非空且可追溯。
7. verification 子 Agent 中断时补发 dispatch_complete/phase_complete。

## Risk Points

- R1 状态口径变化可能改变历史任务展示结果（needs_context → confirmed/not_reproducible）——预期且正确。
- R3 确定性执行增加验证耗时（每 finding 约 30s），用现有 timeout 控制，不影响正确性。
- R4 3 次上限是保守值；可通过配置调整。
- 需确保 `_has_valid_sandbox_evidence` 与 `compute_verification_status` 证据口径一致（都排除 fabricated、都要求 VULNERABILITY_CONFIRMED）。
