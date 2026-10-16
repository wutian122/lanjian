# Delta Spec: audit-time-governance

## ADDED Requirements

### Requirement: 任务时间预算解析不得被任务级默认值短路

`Orchestrator._resolve_task_timeout` 的第一优先级输入 `input_data["task_timeout_seconds"]` SHALL 仅在**用户显式设置**的任务级 `timeout_seconds` 上生效。任务创建时若请求未携带 `timeout_seconds`，落库值 SHALL 为 NULL（不得落 Pydantic 默认值 1800）；执行入口构造 `task_timeout_seconds` 时对 NULL 不得回退到字面量 1800，SHALL 传 None 使 `_resolve_task_timeout` 落到全局 `agentTimeout` 配置。全局"Agent 总超时"设置（系统设置 → LLM 配置 → Agent 超时配置）MUST 对未显式设置任务级超时的任务实际生效。

#### Scenario: 全局总超时对新建任务生效
- **WHEN** 用户在系统设置中配置 `agentTimeout=7200`，创建任务时未显式传 `timeout_seconds`
- **THEN** 任务落库 `timeout_seconds=NULL`，执行时 `_resolve_task_timeout` 返回 7200，deadline 与 watchdog 均按 7200s 计算

#### Scenario: 显式任务级超时优先于全局
- **WHEN** 创建任务时显式传 `timeout_seconds=3600`
- **THEN** 执行时任务级 3600s 生效，全局配置不覆盖它

#### Scenario: 已存在的旧任务不受影响
- **WHEN** 历史任务 `timeout_seconds=1800` 已落库
- **THEN** 其执行行为与修复前一致（1800s）

### Requirement: 时间预算将尽时禁止无效派发

Orchestrator 在派发子 Agent 前 SHALL 校验剩余预算是否足以支撑该类型子任务的最小有效工作时长（analysis ≥ 300s、verification ≥ 300s、recon ≥ 120s，常量可配置），不足时 SHALL 拒绝派发并进入收口流程，不得出现"派发成功后同一轮询周期立即软停止"的无效派发。拒绝派发事件 SHALL 记入 `_gate_observations`（含剩余秒数与所需最小时长）。

#### Scenario: 剩余预算不足以完成一轮有效分析时收口
- **WHEN** 剩余预算 120s 且 orchestrator 决策派发 analysis（最小有效时长 300s）
- **THEN** 拒绝派发，发射"预算不足以完成有效分析，提前收口"事件，`_gate_observations` 新增一条记录，任务按已有 findings 收口

#### Scenario: 预算充足时正常派发
- **WHEN** 剩余预算 900s
- **THEN** analysis 正常派发，不被新阈值拦截

#### Scenario: 拒发事件可观测
- **WHEN** 任一派发被预算阈值拒绝
- **THEN** `agent_tasks.observations` 收尾时包含 `{gate: "dispatch_budget", remaining_seconds, required_seconds}` 记录
