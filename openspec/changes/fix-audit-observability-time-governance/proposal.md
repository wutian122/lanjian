# Proposal: Fix Audit Observability & Time Governance

## Why

两次生产审计任务（nacos `f6c6f4f1`、nginx `acbaa143`，2026-09-02）双双以 `completed_with_gaps` + 0 findings 收场。深度诊断确认系统存在五处"对自身失明/自耗"缺陷：LLM 输出截断后 `finish_reason=length` 被静默丢弃（产出即丢失、无告警）；编排层在时间预算将尽时仍派发大子任务并立即软停止（纯空转 137s+）；Analysis 循环既无 observation 截断也无压缩（每轮 47s 大半耗在 prefill）；0 findings 时三次重复派发的 prompt 决定性成分逐字节相同 + temperature=0.1 近贪心导致复读；沙箱 "Docker sandbox ready" 是无条件发射的假就绪（本机 amd64 实测 0 个沙箱镜像）。另发现任务级 `timeout_seconds` 默认值 1800 必然落库并在 `_resolve_task_timeout` 中以最高优先级短路全局 `agentTimeout` 配置——全局总超时设置形同虚设。

## What Changes

- **BREAKING**（行为修正）：`agent_tasks.py` 任务创建不再落默认 `timeout_seconds=1800`，改落 NULL；执行时 `task_timeout_seconds` 回退到全局 `agentTimeout` 配置（现配置 7200s），使系统设置中的"总超时"真正生效
- 截断可见化：`base.py` 流式消费端读取 `finish_reason`，`length` 时发射 warning 事件并在对话历史注入"输出被截断"提示，Final Answer 解析失败时事件可归因
- 时间治理：`orchestrator.py` 拒发新调度阈值从"剩余 30s"改为"剩余 < 子任务最小有效时长"（analysis/verification 各自类型化）；消除"派发后同一秒软停止"的无效派发
- Analysis observation 截断：`analysis.py` 追加 observation 到对话历史前执行保头尾截断（4000 字符，复用 verification 已有策略），大幅降低每轮 prefill 开销
- 打破复读：`analysis.py` 结果 data 上报 `files_read`/`grep_patterns`；`orchestrator.py` `_search_registry` 据此推进，使 0 findings 时 cross_round_context 逐轮变化
- 沙箱真就绪：`SandboxManager.initialize()` 增加 `SANDBOX_IMAGE` 镜像存在性检查；`agent_tasks.py:688` 的 ready 事件仅在"docker 可用且镜像存在"时发射 done，否则发射 `init_status: "failed"` 并附 `get_diagnosis()` 诊断
- 配置基线（已由用户授权于 2026-09-02 直接落库，本变更仅记录）：`llmMaxTokens=16384`、`llmTemperature=0.3`、`llmTimeout=300000`、`agentTimeout=7200`

## Capabilities

### New Capabilities

- `audit-time-governance`: 任务时间预算的解析优先级、拒发新调度阈值与预算收口行为的规范（修正全局配置被任务级默认值短路的缺陷）
- `audit-observability`: LLM 输出截断告警、Analysis observation 截断、搜索状态上报推进、沙箱就绪真实检查四类可观测性要求

### Modified Capabilities

<!-- audit-engine 现有 spec 的需求不变更（本变更全部为新增行为约束与缺陷修正），如实施中发现与 audit-engine spec 冲突，按 openspec-update-change 流程修订 -->
