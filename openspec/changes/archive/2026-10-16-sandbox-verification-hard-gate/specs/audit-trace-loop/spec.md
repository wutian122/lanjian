# Delta Spec: audit-trace-loop

## ADDED Requirements

### Requirement: audit_trace 文件 MUST 持久化于容器外

`audit_traces/` 目录 SHALL 通过 docker compose volume 挂载到宿主机（与 `/tmp/lanjian` 同样的 bind mount 模式），容器重建 MUST NOT 丢失历史任务的追踪文件。路径配置 SHALL 支持环境变量覆盖（现默认 `./audit_traces/` 相对路径）。

#### Scenario: 容器重建后 trace 仍在
- **WHEN** backend 容器被重建（如版本热修 recreate）
- **THEN** 宿主机 `audit_traces/` 下历史任务的 audit_trace.md/.json/context_archive.json 完整保留

### Requirement: 写点 SHALL 覆盖关键执行事件

AuditTraceManager 的以下写方法 SHALL 在生产路径接线（现为零调用死代码）：`add_tool_call`（Agent 每次工具调用后）、`add_llm_call`（每轮 LLM 调用的 token 消耗）、`add_verification_result`（每个 finding 验证完成后）。既有四个写点（调度/发现/压缩/收尾）保持不变。

#### Scenario: trace 反映工具与 token 消耗
- **WHEN** 任务执行过程中 Analysis 调用了 15 次工具、消耗 63 万 tokens
- **THEN** audit_trace.md 的工具调用与 Token 栏目有对应记录，不再是恒 0

### Requirement: 执行上下文 SHALL 注入 trace 摘要（读侧接线）

Orchestrator 在每轮主循环开始前 SHALL 调用 `get_summary_for_agent()` 获取 trace 摘要（上限 2000 字符），注入对话历史（作为系统提示段落"此前执行轨迹摘要"）；子 Agent 派发时该摘要 SHALL 经 `previous_results` 传递。摘要注入失败 MUST 非致命（warning + 跳过）。摘要内容 SHALL 至少包含：已调度 Agent 及结论、已产出的发现标题列表、关键门禁裁决。

#### Scenario: 后续轮次能查阅历史决策
- **WHEN** 第 3 轮 orchestrator 主循环开始，前两轮已调度 recon/analysis 并有 2 个发现
- **THEN** 本轮 LLM 的对话历史包含"此前执行轨迹摘要"（含已调度 Agent 与 2 个发现标题），可避免重复调度与重复分析

#### Scenario: trace 文件不可用时降级
- **WHEN** audit_traces 目录不可写或摘要生成异常
- **THEN** 发射 warning 事件并跳过注入，任务继续执行不中断

### Requirement: 人工审查入口 SHALL 可用

`GET /api/v1/agent-tasks/{id}` 响应 SHALL 增加 `audit_trace_path` 字段（相对路径，任务已完成时指向 trace 文件）；运维可通过挂载路径直接读取 trace 文件做任务复盘。

#### Scenario: 前端/运维可定位 trace
- **WHEN** 任务完成后调用任务详情 API
- **THEN** 响应含 audit_trace_path，且该路径在宿主机挂载点下真实存在
