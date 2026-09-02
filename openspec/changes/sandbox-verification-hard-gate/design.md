# Design: sandbox-verification-hard-gate

## Context

B 变更（fix-audit-observability-time-governance）解决"系统对自身失明"；本变更解决"策略层"两类问题：(1) 漏洞产出下限——Analysis 不得因保守而零产出；(2) 沙箱硬门禁——每个 finding 终态必须可追溯沙箱证据或显式豁免。依赖 B 的沙箱真就绪检查与 observations 基础设施。

## Architecture

### 1. infra_error 状态分离（先做，是硬门禁的地基）

- `verification.py:_record_sandbox_attempt`（1606-1696）：识别错误签名集合 `INFRA_ERROR_SIGNATURES = ["docker not available", "沙箱环境不可用", "imagenotfound", "no such image", "pull access denied", "error while creating mount source path"]`（小写匹配 attempt 的 error/stdout/evidence_summary）→ `attempt["infra_error"]=True`
- `compute_verification_status`（148-207）：分支 4（196-200）前插入：`real = [a for a in attempts if not a.get("fabricated")]`；若 `real and all(a.get("infra_error") for a in real)` → 返回 `("needs_context", "infra_error")` 附诊断
- 前端（第 5 节）展示"沙箱环境故障"徽章
- **顺手修次级 bug**：`execute_tool_command` 异常返回 dict（sandbox_tool.py:378-385）补 `"stdout": ""` 键（与 :220-228 对齐），消除 `:860` KeyError

### 2. 沙箱硬门禁（十条封堵，逐条）

| # | 位置 | 改法 |
|---|---|---|
| a | verification.py:2271-2281 | 静态短路前对该 finding 执行确定性 PoC（存在对应模板），attempt 参与状态推导；无模板类型（weak_crypto/xxe）保持现行为但标记 `sandbox_skip_reason="no_poc_template"` |
| b | :2296-2320 | 前置 `len([a for a in attempts if not a.get("infra_error")])>0`；否则 `needs_context` |
| c | :1136-1140 | skip_reason 语义保留（合法豁免），但 orchestrator 收尾时逐 finding 呈现清单 |
| d | orchestrator.py:1137-1207 | R4 放行前：`for f in unverified: f["sandbox_skip_reason"]="gate_release_after_max_redispatch"`；报告生成（agent_tasks.py 报告段）MUST 含"未沙箱验证清单"段落 |
| f | verification.py:1142-1148 | 弹性退出放行时对剩余未验证 finding 写 `sandbox_skip_reason="elastic_exit"` |
| g | :1046-1048 / 1329-1350 | 预算耗尽 break 前调用 `_run_deterministic_sandbox_commands(剩余未跑的 commands)`（与 cancel 路径 :1334 的收口对齐） |
| h | :1404-1474 | 兜底循环 `for cmd in sandbox_commands`（复用 :2485 写法），不再只跑 [0] |
| i | :1059-1090 | 第 3/8 轮强制引导文案改为"如你未主动执行，本轮结束后系统将程序化执行剩余模板"（配合 h 的兜底语义） |

### 3. 确定性 PoC 修复

- **SSRF network_mode**：`_run_deterministic_sandbox_commands`（2502-2507）改调 `execute_with_files(command, host_project_dir, timeout, network_mode=("bridge" if sc.get("network_enabled") and settings.SANDBOX_NETWORK_ENABLED else None))`；execute_with_files 已支持 network_mode 参数（:393 默认 none）→ 传 None 保持默认
- **三模板补确认输出**（2632-3171 内）：
  - path_traversal（2792-2816）：grep 确认 sink + `os.path` 拼接模式 ≥1 → `VULNERABILITY_CONFIRMED(STATIC)`；仅存在 sink → `VULNERABILITY_STATIC_ONLY`
  - hardcoded_secret（3071-3098）：`Secret pattern found: N` 且 N>0 → `VULNERABILITY_STATIC_ONLY`（密钥类动态确认无意义，static 档即可）
  - deserialization（3099-3124）：grep 到危险 load + 可达输入 → `VULNERABILITY_STATIC_ONLY`
  - 统一走既有 `static_evidence` 识别（1647/2172），终态上限 static_confirmed（与其它模板一致）

### 4. 产出下限（分层候选制）

- **提示词**（analysis.py:29-260 + system_prompts.py:402）：
  - 替换"宁可漏报，不可误报"为分级表述："高置信（≥0.7）直接报告；低置信（0.1-0.7）作为候选输出并标 `needs_verification=true`，由沙箱验证证实或证伪；仅报告实际读取代码中看到的模式（防幻觉规则保留）"
  - Final Answer JSON 格式说明增加 `needs_verification` 字段语义
- **强制总结**（:419-467）：提示词追加"每个未覆盖维度至少 1 个候选或书面豁免"；解析后校验：候选+豁免数为 0 → 追加重试提示一次；仍为 0 → data 标 `output_floor_violated=true`
- **orchestrator**（:2091-2107）：`max_dispatch` 达到时不再无条件"自动放行 finish"——若 `output_floor_violated` 则记 `_gate_observations {gate:"output_floor"}` 后按覆盖不足收口；否则维持放行
- **Semgrep 兜底**（orchestrator 主循环收尾、`_all_findings` 为空时）：`_semgrep_findings` 去重（file_path+rule_id）→ 映射候选 `{title: rule message, vulnerability_type: 规则映射, severity: 规则默认, confidence: 0.5, needs_verification: true, source: "semgrep_fallback"}` → 进 `_all_findings` 走既有归一化与验证派发
- **门禁口径**（orchestrator.py:1133-1145、1262-1267）：`needs_verification=true` 的候选计入 has_findings/未验证集合；verification 的 `findings_to_verify`（832-837）已接受该字段，验证收尾对候选的 `not_reproducible` 结果不升级不误报
- **归一化注意**：候选 confidence<0.7 会被 `_normalize_finding`（2939-2955）与 strict_finding 闸丢弃——`needs_verification=true` 的候选 SHALL 豁免 0.7 硬阈值（改为 ≥0.1），且 `_save_findings` 的 `is_strict_finding`（1887-1890）对 `source in ("semgrep_fallback")` 或 `needs_verification=true` 放行（仍要求 title/file_path 非空）

### 5. audit_trace 闭环

- **持久化**：docker-compose.yml backend volumes 追加 `./audit_traces:/app/audit_traces`；路径常量支持 `AUDIT_TRACE_DIR` env 覆盖（audit_trace.py:50-52）
- **写点补全**：base.py `execute_tool` 收尾调 `add_tool_call`；`stream_llm_call` done 后调 `add_llm_call`（agent 名/token 数）；verification 收尾对每 finding 调 `add_verification_result`——三者全部 try/except 非致命
- **读侧接线**：orchestrator 主循环每轮开头 `summary = trace.get_summary_for_agent()[:2000]`，注入 system 提示段"此前执行轨迹摘要"；子 agent 经 `sub_input["trace_summary"]` 传递；异常 → warning + 跳过
- **API**：`AgentTaskResponse` 增 `audit_trace_path: str | None`（任务完成时从 AuditTraceManager 取相对路径）

### 6. 前端

- **创建表单**（CreateAgentTaskDialog.tsx + CreateTaskDialog.tsx 两处）：新增"超时时间（分钟）"（默认 120，范围 1-120，秒=x60 传 timeout_seconds）与"最大迭代次数"（默认 50，范围 1-200）两项，agent 模式提交体带上；后端零改动
- **StatsPanel.tsx**：新增"时间预算"格——`started_at + timeout_seconds` 与当前时间差（运行中）/ `duration`（完成态）；`AgentTaskSummary` 类型补 `timeout_seconds`
- **FindingSandboxEvidence.tsx**：attempt 卡片新增三标记徽章（fabricated=红"伪造证据已排除"、static_evidence=蓝"演示性静态确认"、poc_error=琥珀"验证器崩溃"）；finding 级 `verification_status=needs_context 且 infra_error` 时显示"沙箱环境故障"提示
- **报告**：报告生成段增加"未沙箱验证清单"（skip_reason 非空或 needs_context 的 finding 列表）

### 7. 台账治理

- 核实 `fix-sandbox-evidence-and-recovery` 33 任务中已实施的（T1-T5 沙箱Attempts列、Bug A/C/D 部分）逐项补勾，未实施的标注移交本变更或废弃
- `fix-verification-evidence-root`（18/18 complete）走 openspec-archive-change 归档
- 本变更任务随实施逐项勾选（R24/R25：全量绿测后才允许最后勾选与归档）

## Data Flow

候选链：Analysis 候选 / Semgrep 兜底 → `_all_findings`（归一化豁免 0.7）→ verification `findings_to_verify` → 确定性 PoC（全量）→ `sandbox_attempts`（infra_error 标记）→ `compute_verification_status`（分支前置 infra 判定）→ 终态（confirmed/static_confirmed/not_reproducible/needs_context/false_positive）→ 报告含未验证清单。
Trace 链：写点（工具/LLM/验证）→ `audit_traces/`（volume）→ 每轮 `get_summary_for_agent()` → orchestrator/子 agent 上下文。

## Error Handling

- 全部新逻辑非致命：兜底落库失败 → warning + 保持 0 findings 现状；trace 注入失败 → 跳过；模板补全的确认输出不影响既有标记识别
- 兼容：无 skip_reason 的既有历史行为仅在终态语义上收紧（needs_context 增多、not_reproducible 减少）——报告需向使用者说明该变化

## Testing Strategy

- Phase 1（infra/network/模板）与 Phase 2（封堵）纯后端可单测：每个封堵点一条失败测试（构造 attempts 场景断言终态）
- Phase 3（产出下限）提示词行为用"结构化输出断言"测试：mock LLM 返回含候选的 Final Answer → 断言不被 0.7 阈值丢弃；Semgrep 兜底用假 _semgrep_findings 断言落库
- Phase 4-5（trace/前端）前后端各自单测 + Playwright e2e 一条（创建带预算参数的任务 → 详情页显示剩余时间）
- 全量回归 + 同一项目新旧行为对照任务（验收：findings>0、每 finding 终态可解释、无 not_reproducible 型基础设施伪装）
