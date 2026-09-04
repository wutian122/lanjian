# Tasks: sandbox-verification-hard-gate

## Phase 0: LLM 调用韧性（2026-09-04 服务端排查结论吸收）

> 排查结论：①R1 空响应=reasoning 吃光 max_tokens 预算（已由 32768 修复，护栏保留）；②R2 残余空响应=模型 reasoning 后自然停止零正文（finish_reason=stop、无截断、无服务端错误，纯模型行为）；③Orchestrator 空 name/空参数 tool_calls（模型退化，Task 7 自愈路径喂回后仍可能连续退化）；④10.129.2.101 服务端 mm 崩溃（32 Traceback）对应老板下午任务时段，触发器精确复现未完成、**服务端零操作**（老板指令），mm 触发器二分诊断挂起。

### Task 20: 空响应强化重试（reasoning-后-无正文 nudge）
- [x] **Task 20 完成**（commit 30e06af，review CLEAN——tool_calls 轮豁免双重保险、判定链顺序正确（tool_calls→truncated→reasoning_only→other）、四接入点无漏接、上限轮不追加锁定、与 Task 2B 截断机制/Task 8 submit_findings 协同不冲突；3 Minor 记账：空列表归一注释、getattr 冗余防御、run 循环 mock 桩强度）
- Files: `backend/app/services/agent/agents/base.py`（stream_llm_call 空响应判定处 :1330 附近）、`backend/app/services/agent/agents/{analysis,recon,verification}.py`（空响应重试提示词 :706-730/:955-959 等各处）
- Interfaces: 空响应重试提示词升级——区分两种形态并分别 nudge：①finish_reason=stop 且正文空（"你上一轮只输出了思考没有给出行动，请直接输出 Action 或调用工具，不要重复思考"）；②finish_reason=length 且正文空（提示由 B 变更截断机制已有，此处补充"输出预算被思考耗尽，请精简思考"）。连续空响应达上限后的收口行为保持现状
- TDD: 失败测试（mock 两种空响应形态 → 断言重试提示包含对应 nudge 文案且连续计数正确）→ 实现 → 通过 → commit

### Task 21: 空/无效 tool_calls 强 nudge 自愈
- [x] **Task 21 完成**（实施 e31de50 + 回归测试补丁 759f11f，review 初审 NEEDS_FIX（Important：顺带修复的 else 分支回写无测试锁定——变异实证删除该行 580 测试全绿）→ 重审 CLEAN（新测试为回写行唯一精确守卫，变异独立复核）；**顺带修复实锤：旧 else 分支 observation 从未回写 step.observation，"Observation:\nNone" 喂回模型——旧泛化自愈从未生效，R2 退化持续的真根因之一**；Minor 记账：连续无效无上限暂停（max_iterations 兜底）、分类器与映射的解析重复）
- Files: `backend/app/services/agent/agents/orchestrator.py`（_step_from_tool_calls :2108-2205 与未知操作分支 :1532-1534）
- Interfaces: 空 name/坏 JSON/空参数 tool_calls 的自愈 observation 强化——现状喂"未知操作: "泛化提示，改为：①空 name → 喂"工具调用缺少函数名，请重新输出，可用操作与参数 schema 如下：[完整三函数定义]"; ②dispatch_agent 空参数 → 喂"agent 参数缺失，必须为 recon/analysis/verification 之一，task 必须非空"; ③连续 2 次无效 tool_calls → 追加"请改用文本格式 Thought:/Action:/Action Input: 输出"（协议降级 nudge）
- TDD: 失败测试（三种无效形态 → 断言 observation 含 schema 重喂与协议降级 nudge）→ 实现 → 通过 → commit

## Phase 1: 基础设施语义修复

### Task 1: infra_error 标记与状态机分离
- [x] **Task 1 完成**（commit 6a699a6 + 0686dd9 缺口修复，review CLEAN——两 Scenario 8 探针实证、签名 8 项锁定、attempt 合并链 infra_error 保留性核对通过；实施者主动核验修复 language_test/fallback 两处缺口 + connection 守卫防误判（SSRF PoC 容器内拒绝是真实执行）；2 Important 交接：①**确定性路径 exit_code=-1 合成致 connection 签名抑制**（daemon 中断窄时序漏判）→ Task 2/3 处理 _format_sandbox_result 退出码语义；②**软证据升级不排除 infra_error**（needs_context 被洗白 static_confirmed）→ Task 6 验收强制核对；4 Minor 记账）
- Files: `backend/app/services/agent/agents/verification.py`（_record_sandbox_attempt :1606-1696、compute_verification_status :148-207）
- Interfaces: Produces attempt 新字段 `infra_error: bool`；compute 新分支 `("needs_context", "infra_error")`
- TDD: 写失败测试（attempt 全部含 "ImageNotFound"/"Docker not available" 签名 → 终态 needs_context 而非 not_reproducible；混合真实失败 → 仍 not_reproducible；成功铁证 → confirmed 不受影响）→ 跑失败 → 实现签名识别与分支前置 → 通过 → commit

### Task 2: execute_tool_command 异常返回补 stdout 键
- [x] **Task 2 完成**（实施 045388e + 修复 3839b8a，review 初审 NEEDS_FIX（Important：sandbox_language.py 六处渲染残留——"退出码: None"泄漏 + error 不渲染 + success 翻 True，infra 伪装在语言工具路径仍可达）→ 重审 CLEAN（六处守卫 + error 行闭环 _has_sandbox_failure_marker/_is_infra_error 双消费者、两个独立变异实证、fallback 五分支推演）；**_sandbox_failure 统一失败工厂 12 处收口 + exit_code 语义修正（None=未进容器/-1=超时）完整落地 Task 1 review Important-1**；4 Minor 记账：事件文案 exit=None、手动重跑端点不写 infra_error（Task 6 顺带）、sandbox_vuln.py 五处同款渲染（Minor）、fallback 持久化已修）
- Files: `backend/app/services/agent/tools/sandbox_tool.py`（:378-385）
- Interfaces: 异常返回 dict 与 :220-228 结构对齐（含 stdout/stderr 空串）
- TDD: 失败测试（mock containers.run 抛异常 → 返回 dict 含 "stdout" 键，SandboxTool._execute :860 不 KeyError）→ 实现 → 通过 → commit

### Task 3: SSRF 确定性 PoC network_mode 传递
- [x] **Task 3 完成**（commit 95e951e，review CLEAN——两 Scenario 达成（bridge 断言 + none 行为不变）、None 传参裁决复核正确（execute_with_files 裸透传 SDK，None=默认 bridge 反而开网）、两个调用点传递、RED 亲验（旧代码缺陷真实性确认）；**双门禁 sandbox_tool.py 超出声明范围但同 kill-switch 安全语义（R17 记账），修复了 LLM 自授网络的反向缺陷**；F1 Important 转后续任务：**sandbox_http/VulnerabilityVerifyTool 经 execute_http_request 无条件 bridge 且变异共享配置——kill-switch 全入口绕过未收口**；F4 建议后续对齐 AND 判定；F5 死配置核实为真（前端 sandboxNetworkEnabled 开关无后端消费者，仅 env 生效——后续接线或隐藏））
- Files: `backend/app/services/agent/agents/verification.py`（:2502-2507）
- Interfaces: Consumes 模板 `network_enabled` 与 `settings.SANDBOX_NETWORK_ENABLED`；Produces `execute_with_files(..., network_mode=...)` 实参
- TDD: 失败测试（network_enabled=True 且开关开 → execute_with_files 收到 bridge；开关关 → 收到 None/none）→ 实现 → 通过 → commit

### Task 4: 三个无证据模板补确认输出
- [x] **Task 4 完成**（commit 977f5a2，review CLEAN——双 Scenario 达成（三模板有 sink → 确认标记 → static_confirmed / 无 sink → not_reproducible 不回归）、真实执行 heredoc PoC 测试（非字符串断言）、两轮变异有效；**行外裁决复核属实：VULNERABILITY_STATIC_ONLY 此前零消费点（xss 输出一直空转 not_reproducible），扩展识别后 xss 首次生效且终态上限 static_confirmed 方向合理、提示词零暴露反伪造面不扩大**；2 Minor 记账：演示 base 目录不存在无影响（字符串运算）、pickle demo 无数据流因果（诚实措辞 STATIC_ONLY））
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
