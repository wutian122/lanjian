# Proposal: Sandbox Verification Hard Gate & Finding Output Floor

## Why

老板硬需求"**每个审计到的漏洞必须被沙箱验证**"在当前代码下不成立：存在十条可绕过沙箱直达终态的路径（Semgrep 四类静态短路、软证据四件套升级不要求 attempts 非空、skip_reason 豁免、R4 三次放行、弹性退出、预算耗尽不补跑、兜底只跑第一个等）；确定性 PoC 天花板是 static_confirmed 且三个模板（path_traversal/hardcoded_secret/deserialization）根本没有确认输出分支；SSRF 模板的 network_enabled 参数在确定性执行路径被丢弃；基础设施故障（镜像缺失/Docker 缺席）被 `compute_verification_status` 分支 4 伪装成 `not_reproducible` 终态。同时 nginx 任务暴露的行为性 0 findings 根因未解决：Analysis 提示词"宁可漏报不可误报"+自验可利用性导致 LLM 主动放弃产出，系统无产出下限机制。audit_trace 机制写侧已接线但 AI 零读点、容器内不持久化（两次任务的 trace 已随容器重建物理丢失）。OpenSpec 台账漂移（fix-sandbox-evidence-and-recovery 0/33 in-progress 但部分代码已实施）。

## What Changes

- **产出下限（分层候选制）**：Analysis 提示词从"宁可漏报"改为"高置信直接报告、低置信转候选"——每个 finding 带 confidence 分级与 `needs_verification` 标记；强制总结要求每个未覆盖维度至少输出 1 个候选或书面豁免；orchestrator 覆盖率门禁不再在 max_dispatch=3 后无条件放行 0 候选
- **Semgrep 兜底落库**：Analysis 0 产出时，Semgrep 预扫发现作为"待验证候选"落库（confidence 按规则置信度、needs_verification=true），直接进入验证队列
- **infra_error 状态分离**：`_record_sandbox_attempt` 识别 Docker 缺席/镜像缺失/pull 失败错误签名打 `infra_error=true`；`compute_verification_status` 在分支 4 前先判全 infra_error → `needs_context`（附诊断说明），不再伪装成"漏洞未复现"
- **沙箱硬门禁（十条豁免路径封堵）**：
  - (a) Semgrep 四类静态短路仍执行对应确定性 PoC，无 attempt 不得 `is_verified=true`
  - (b) 软证据升级前置条件加 `len(attempts)>0`，否则降 `needs_context`
  - (c/d) skip_reason 与 R4 放行前，未验证 finding 强制标记 `needs_context(+skip 原因)` 且报告生成必须呈现"未沙箱验证"清单
  - (f) 弹性退出放行时强制写 `needs_context(elastic_exit)`
  - (g) 预算耗尽收口前仿 cancel 路径补跑剩余确定性 PoC
  - (h) LLM 拒调兜底从只跑 `sandbox_commands[0]` 改为遍历全部
  - (i) 强制引导改为程序化执行剩余未跑模板（不再依赖 LLM 自觉）
- **确定性 PoC 修复**：SSRF 确定性执行传递 `network_mode`（模板 `network_enabled` 参数被 2502-2507 丢弃的缺陷）；path_traversal/hardcoded_secret/deserialization 三模板补确认输出分支
- **沙箱环境预检**（承 B 变更）：`initialize()` 镜像检查 + ready 事件真实化 + 前端任务创建前沙箱可用性预警
- **audit_trace 闭环**：compose 挂载 `audit_traces/` volume 持久化；每轮派发前将 `get_summary_for_agent()` 摘要注入 orchestrator 与子 agent 上下文；补齐 add_tool_call/add_llm_call 写点
- **前端**：任务创建对话框增加 `timeout_seconds`（60-7200）与 `max_iterations`（1-200）输入项（后端 AgentTaskCreate 已支持）；详情页 StatsPanel 显示预算/剩余时间；FindingDetail 沙箱证据区展示 `fabricated`/`static_evidence`/`poc_error` 三标记
- **观测性增补**：前端 `FindingSandboxEvidence` 消费三标记；orchestrator observations 记录每次豁免路径的封堵命中
- **台账治理**：核实并补勾 `fix-sandbox-evidence-and-recovery` 已实施任务、归档 `fix-verification-evidence-root`（18/18 complete）；本变更任务随实施逐项勾选（R1/R24/R25）

## Capabilities

### New Capabilities

- `sandbox-verification-gate`: "每个 finding 终态必须至少一次沙箱执行或显式 infra/skip 标记"的门禁规范，含十条豁免路径的封堵语义与 infra_error 状态机
- `finding-output-floor`: Analysis 分层候选制产出下限规范（confidence 分级、needs_verification 语义、维度豁免要求）与 Semgrep 兜底落库行为
- `audit-trace-loop`: audit_trace 文件的持久化、写点完备性与 AI 读侧注入规范

### Modified Capabilities

<!-- 沙箱执行参数（network_mode 传递）与 verification 状态机扩展涉及 audit-engine 既有行为的细化，实施时按 delta 修订 -->
