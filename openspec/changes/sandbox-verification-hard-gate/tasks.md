# Tasks: sandbox-verification-hard-gate

## Phase 1: 基础设施语义修复

### Task 1: infra_error 标记与状态机分离
- Files: `backend/app/services/agent/agents/verification.py`（_record_sandbox_attempt :1606-1696、compute_verification_status :148-207）
- Interfaces: Produces attempt 新字段 `infra_error: bool`；compute 新分支 `("needs_context", "infra_error")`
- TDD: 写失败测试（attempt 全部含 "ImageNotFound"/"Docker not available" 签名 → 终态 needs_context 而非 not_reproducible；混合真实失败 → 仍 not_reproducible；成功铁证 → confirmed 不受影响）→ 跑失败 → 实现签名识别与分支前置 → 通过 → commit

### Task 2: execute_tool_command 异常返回补 stdout 键
- Files: `backend/app/services/agent/tools/sandbox_tool.py`（:378-385）
- Interfaces: 异常返回 dict 与 :220-228 结构对齐（含 stdout/stderr 空串）
- TDD: 失败测试（mock containers.run 抛异常 → 返回 dict 含 "stdout" 键，SandboxTool._execute :860 不 KeyError）→ 实现 → 通过 → commit

### Task 3: SSRF 确定性 PoC network_mode 传递
- Files: `backend/app/services/agent/agents/verification.py`（:2502-2507）
- Interfaces: Consumes 模板 `network_enabled` 与 `settings.SANDBOX_NETWORK_ENABLED`；Produces `execute_with_files(..., network_mode=...)` 实参
- TDD: 失败测试（network_enabled=True 且开关开 → execute_with_files 收到 bridge；开关关 → 收到 None/none）→ 实现 → 通过 → commit

### Task 4: 三个无证据模板补确认输出
- Files: `backend/app/services/agent/agents/verification.py`（path_traversal :2792-2816、hardcoded_secret :3071-3098、deserialization :3099-3124）
- Interfaces: 输出标记 `VULNERABILITY_CONFIRMED(STATIC)` / `VULNERABILITY_STATIC_ONLY`（与 2773/2777 既有语义一致）
- TDD: 失败测试（构造三类 finding 的命令执行 → 输出含对应标记；无 sink → 无标记）→ 实现三分支 → 通过 → commit

## Phase 2: 沙箱硬门禁（豁免封堵）

### Task 5: Semgrep 静态短路先执行确定性 PoC
- Files: `backend/app/services/agent/agents/verification.py`（:2271-2281）
- Interfaces: 短路类型 finding 先产生 attempt；无模板类型写 `sandbox_skip_reason="no_poc_template"`
- TDD: 失败测试（mock hardcoded_secret finding → 存在 attempt 且状态由 attempt+静态证据推导）→ 实现 → 通过 → commit

### Task 6: 软证据升级前置 attempts 非空
- Files: `backend/app/services/agent/agents/verification.py`（:2296-2320）
- Interfaces: 升级前置条件 `len(非 infra attempts)>0`
- TDD: 失败测试（四件套齐备+0 attempts → needs_context；四件套+1 attempt → static_confirmed）→ 实现 → 通过 → commit

### Task 7: 弹性退出/预算耗尽/兜底遍历
- Files: `backend/app/services/agent/agents/verification.py`（弹性退出 :1142-1148、预算耗尽 :1046-1048 与 :1329-1350、兜底 :1404-1474）
- Interfaces: 弹性退出写 `sandbox_skip_reason="elastic_exit"`；预算耗尽收口前补跑剩余确定性 PoC；兜底遍历全部 sandbox_commands
- TDD: 失败测试×3（对应三场景）→ 实现 → 通过 → commit

### Task 8: R4 放行的未验证清单强制标记与报告呈现
- Files: `backend/app/services/agent/agents/orchestrator.py`（:1137-1207）、`backend/app/api/v1/endpoints/agent_tasks.py`（报告生成段）
- Interfaces: 放行时逐 finding 写 `sandbox_skip_reason="gate_release_after_max_redispatch"`；报告含"未沙箱验证清单"段落
- TDD: 失败测试（3 次拒绝后放行 → findings 带标记；报告文本含清单标题与 finding 标题）→ 实现 → 通过 → commit

## Phase 3: 产出下限

### Task 9: Analysis 分层候选提示词改造
- Files: `backend/app/services/agent/agents/analysis.py`（:29-260 系统提示词）、`backend/app/services/agent/prompts/system_prompts.py`（:402、:89）
- Interfaces: Final Answer findings 支持 `needs_verification`；提示词分级产出策略
- TDD: 失败测试（mock LLM 返回 confidence=0.4 + needs_verification=true 的 finding → 通过归一化不被 0.7 阈值丢弃且 is_strict_finding 放行；confidence=0.05 → 仍丢弃）→ 改提示词与 `_normalize_finding`/strict_finding 豁免逻辑 → 通过 → commit

### Task 10: 强制总结维度级下限
- Files: `backend/app/services/agent/agents/analysis.py`（:419-467）
- Interfaces: data 新增 `dimension_gaps_reported`、`output_floor_violated`
- TDD: 失败测试（0 候选 0 豁免 → violated=true 且有一次重试提示；有豁免 → violated=false）→ 实现 → 通过 → commit

### Task 11: orchestrator 产出下限门禁与 Semgrep 兜底
- Files: `backend/app/services/agent/agents/orchestrator.py`（max_dispatch 自动放行 :2091-2107、主循环收尾）、`backend/app/services/agent/agents/orchestrator.py`（归一化豁免联动 Task 9）
- Interfaces: 兜底候选 `{source:"semgrep_fallback", confidence:0.5, needs_verification:true}`；observations 记 `{gate:"output_floor"}`
- TDD: 失败测试（0 findings + 假 semgrep_findings 3 条 → 落库候选带标记进验证队列；output_floor_violated → 收口记 observations）→ 实现 → 通过 → commit

### Task 12: 门禁候选口径统一
- Files: `backend/app/services/agent/agents/orchestrator.py`（has_findings :1133-1145、UNVERIFIED_TERMINAL :1262-1267）、`backend/app/services/agent/agents/verification.py`（findings_to_verify :832-837）
- Interfaces: `needs_verification=true` 候选纳入门禁与验证队列口径
- TDD: 失败测试（仅 3 候选 → verification 派发且门禁按候选计算）→ 实现 → 通过 → commit

## Phase 4: audit_trace 闭环

### Task 13: trace 持久化与路径配置
- Files: `docker-compose.yml`（backend volumes）、`backend/app/services/agent/audit_trace.py`（:50-52 路径 env 化）
- Interfaces: env `AUDIT_TRACE_DIR` 覆盖默认 `./audit_traces`
- TDD: 失败测试（env 设置后目录切换）→ compose 修改（本地验证 volume 生效）→ commit

### Task 14: 写点补全（工具/LLM/验证）
- Files: `backend/app/services/agent/agents/base.py`（execute_tool 收尾、stream_llm_call done 后）、`backend/app/services/agent/agents/verification.py`（验证收尾）
- Interfaces: 调用 `add_tool_call`/`add_llm_call`/`add_verification_result`，全部 try/except 非致命
- TDD: 失败测试（mock 执行一轮 → trace md 工具/Token 栏目非 0）→ 实现 → 通过 → commit

### Task 15: 读侧接线与 API 字段
- Files: `backend/app/services/agent/agents/orchestrator.py`（主循环开头注入）、`backend/app/api/v1/endpoints/agent_tasks.py`（AgentTaskResponse + audit_trace_path）
- Interfaces: 子 agent 经 `sub_input["trace_summary"]`；响应新字段
- TDD: 失败测试（有 trace → 对话历史含摘要段；trace 异常 → warning 跳过不中断；API 含路径）→ 实现 → 通过 → commit

## Phase 5: 前端

### Task 16: 创建表单预算入口
- Files: `frontend/src/components/agent/CreateAgentTaskDialog.tsx`、`frontend/src/components/audit/CreateTaskDialog.tsx`、`frontend/src/shared/api/agentTasks.ts`
- Interfaces: 提交体新增 `timeout_seconds`（分钟输入×60，默认 7200）、`max_iterations`（默认 50）
- TDD: 组件测试（渲染两输入项、提交体含字段、范围校验）→ 实现 → 通过 → commit

### Task 17: 详情页预算/剩余时间与三标记
- Files: `frontend/src/pages/AgentAudit/components/StatsPanel.tsx`、`frontend/src/pages/AgentAudit/components/FindingSandboxEvidence.tsx`、`frontend/src/shared/api/agentTasks.ts`（类型）
- Interfaces: StatsPanel 时间格（运行中=剩余、完成=耗时）；attempt 三徽章（fabricated/static_evidence/poc_error）
- TDD: 组件测试 → 实现 → 通过 → commit

## Phase 6: 台账治理与端到端

### Task 18: OpenSpec 台账治理
- Files: `openspec/changes/fix-sandbox-evidence-and-recovery/tasks.md`（核实补勾）、归档 `fix-verification-evidence-root`
- Interfaces: openspec status/archive 命令
- 验收：status 与实际实施一致；archive 复核四项+Purpose 全过（R18/R24）

### Task 19: 端到端对照验证
- Files: 无代码（验证任务）
- 验收：同一项目跑新代码任务——findings>0（含候选）、每 finding 终态可解释（无 infra 伪装）、报告含未验证清单、trace 文件宿主机可读、前端三处可见；结果记入 observations 并汇报老板
