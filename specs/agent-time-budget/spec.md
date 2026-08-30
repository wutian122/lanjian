# agent-time-budget

## Purpose

定义 agent 审计任务的时间预算治理行为：任务超时预算在编排器内部可见并可执行、子 Agent 调度超时与剩余预算联动、预算将尽时 analysis 软终止交卷保全调查成果、调度超时不永久锁死子 Agent、以及"0 发现 + 调度失败"的诚实终态语义。根除 2026-08-30 诊断的大项目审计"正常结束但 0 漏洞"结构性缺陷。

## Requirements

### Requirement: 任务时间预算传入编排器并在主循环执行

The system SHALL 将任务级 `timeout_seconds` 传入 OrchestratorAgent（缺省回退 `_timeout_config.agent_timeout`），并在 run() 主循环每轮迭代开始时检查剩余预算：剩余不足阈值时停止发起新的调度并优雅收口。

#### Scenario: 预算耗尽优雅收口

- **WHEN** 编排主循环剩余预算低于硬阈值（60s）
- **THEN** 主循环 break，结果 metadata 携带 `coverage_bypassed=True` 与 `coverage_info.reason="task_deadline_exhausted"`（covered_count/total_dimensions/gaps/block_count 字段齐全），任务终态为 `completed_with_gaps`

#### Scenario: 预算充足时行为不变

- **WHEN** 任务运行中剩余预算充足（> 硬阈值）
- **THEN** 编排行为与现状完全一致（调度、门禁、覆盖率评估逻辑不受影响）

### Requirement: 子 Agent 调度超时与剩余预算联动

The system SHALL 将每次子 Agent 调度的 wait_for 超时限制为 `min(类型上限, 剩余任务预算)`，不得超出任务剩余预算发起全额超时的调度。

#### Scenario: 剩余预算小于类型上限

- **WHEN** analysis 类型上限为 1200s 而任务剩余预算仅 500s
- **THEN** 本次 dispatch 的 wait_for 超时为 500s

#### Scenario: 剩余预算充足

- **WHEN** 任务剩余预算大于类型上限
- **THEN** dispatch 超时等于类型上限（recon=min(300,·)/analysis=subAgentTimeout/verification=max(·,1800)），与现状一致

### Requirement: 预算将尽时软终止 analysis 并强制交卷

The system SHALL 在任务剩余预算低于软终止阈值（180s）时向在途 analysis 发出软停止信号（与取消语义分离）；analysis 在下一轮循环头响应软停止后退出探索循环，并执行强制总结（Final Answer）将已完成调查声明为 findings。

#### Scenario: 软终止后交卷产出发现

- **WHEN** analysis 处于探索循环中收到软停止信号
- **THEN** analysis 退出循环并执行一次强制总结 LLM 调用，其解析出的 findings 纳入返回结果（不被硬杀丢弃）

#### Scenario: 软停止不影响取消语义

- **WHEN** 用户主动取消任务（全局取消标志置位）
- **THEN** 取消语义优先于软停止：子 Agent 按 `is_cancelled` 路径退出并返回取消结果，强制总结不执行（防幻觉原则：不基于不完整调查虚报）

#### Scenario: 软停止不污染后续调度

- **WHEN** 软停止触发后同一任务再次调度 analysis
- **THEN** 软停止标志不构成取消状态，调度可正常启动

### Requirement: 任务超时经由 watchdog 可靠生效

The system SHALL 在任务到达 `timeout_seconds` 时通过 watchdog 先置 deadline 标志并请求编排器优雅收口（宽限默认 45s），宽限内正常返回的结果 MUST 携带 `coverage_bypassed=True(reason=task_timeout)`；宽限耗尽仍存活时回退到既有 hard-cancel 兜底（保存已有发现并标 `completed_with_gaps`）。

#### Scenario: 优雅收口路径

- **WHEN** watchdog 到点后 orchestrator 在宽限期内完成收口并返回
- **THEN** 任务终态为 `completed_with_gaps`，事件流含 warning（使用 emit_warning），metadata reason="task_timeout"

#### Scenario: 卡死兜底路径

- **WHEN** watchdog 到点后 orchestrator 超过宽限期仍未返回
- **THEN** 系统 hard-cancel 编排协程，保存当时已有发现，任务终态为 `completed_with_gaps`（对应 `agent_tasks.py` 既有 916-936 分支复活）

#### Scenario: 用户取消与超时互不误伤

- **WHEN** 用户在超时前主动取消任务
- **THEN** 任务终态为 `cancelled`（现状语义），watchdog 不改写用户取消结果

### Requirement: 调度超时不永久锁死子 Agent 实例

The system SHALL 在每次子 Agent 调度启动前重置仅由"调度超时清理"造成的取消锁存（重置后立即以外部取消回调复判），保证同类型 Agent 的后续调度可用；用户主动取消/暂停造成的锁存 MUST NOT 被重置洗掉。

#### Scenario: 调度超时后补发调度可用

- **WHEN** analysis 因调度超时被清理（其 cancel() 已锁存）
- **THEN** 编排器随后再次调度 analysis 时实例可正常运行（不再瞬间返回"任务已取消"）

#### Scenario: 用户取消不被复位洗掉

- **WHEN** 用户已通过取消端点取消任务（全局标志置位导致子 Agent 锁存）
- **THEN** 即使执行调度前复位逻辑，外部取消回调复判仍返回已取消，调度不会绕过用户取消继续执行

### Requirement: 失败子 Agent 的已有发现不丢失

The system SHALL 在子 Agent 返回 success=False 但其结果数据中包含 findings 时，仍将这些 findings 合并进编排器的全量发现集。

#### Scenario: 锁死实例的部分产出保全

- **WHEN** 子 Agent 因取消/超时返回 success=False，但其 data 中包含已声明的 findings
- **THEN** 编排器将这些 findings 纳入 `_all_findings` 参与后续验证与落库

### Requirement: 零发现且调度失败的诚实终态

The system SHALL 在任务以零发现收口且（deadline 标志命中或存在调度失败记录）时，将结果标记为覆盖率安全阀放行（reason=`dispatch_budget_exhausted`），任务终态为 `completed_with_gaps` 并发出 warning 事件。

#### Scenario: 0 发现 + 调度失败

- **WHEN** 任务收口时 `_all_findings` 为空且本次任务存在调度超时/取消失败记录
- **THEN** 任务终态为 `completed_with_gaps`（而非 `completed`），事件流含 warning，metadata reason="dispatch_budget_exhausted" 且 5 字段齐全

#### Scenario: 真实零发现不受影响

- **WHEN** 任务正常完成、无任何调度失败且分析确认无漏洞
- **THEN** 任务终态保持 `completed`（现状语义，不虚报缺口）
