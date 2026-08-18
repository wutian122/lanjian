# audit-engine Spec

## Purpose

lanjian Multi-Agent 审计引擎的跨轮上下文构建与工具参数契约规范。确保 R2 增量补漏机制正确传递已覆盖维度与缺口信息，且 `search_code` 工具参数契约明确，避免 LLM 误传参数。
## Requirements
### Requirement: 跨轮上下文构建不得抛出属性调用异常

Orchestrator 在调度子 Agent 前构建 `CrossRoundContext` 时，访问覆盖率报告的 `gaps` SHALL 使用属性访问（`coverage_report.gaps`），不得以方法调用形式（`coverage_report.gaps()`）触发 `TypeError: 'list' object is not callable`。该构建失败 MUST 不被静默吞掉为 non-fatal 而丢失 R2 增量补漏能力。

覆盖状态判断 SHALL 直接比较 `CoverageStatus` 枚举值（`status_info == CoverageStatus.COVERED`），不得用 `isinstance(status_info, dict)`（`CoverageStatus` 是 `str, Enum` 不是 dict，否则 `cross_round.covered` 永不填充，R2 收不到已覆盖维度信息）。

#### Scenario: 构建 CrossRoundContext 时访问 gaps 属性
- **WHEN** Orchestrator 已有 findings 或正在调度 analysis Agent，执行 `coverage_report.gaps` 访问
- **THEN** 返回未覆盖维度列表，不抛出 `'list' object is not callable` 异常

#### Scenario: gaps 属性被正确迭代填充
- **WHEN** `CrossRoundContext` 构建过程中遍历 `coverage_report.gaps`
- **THEN** 每个未覆盖维度被追加到 `cross_round.gaps`，且 `to_prompt()` 输出包含「未覆盖维度（R2 必须补充）」段落

#### Scenario: COVERED 维度填充到跨轮上下文
- **WHEN** R1 审计发现某维度漏洞（如 D1 注入），`coverage_report.statuses[dim] == CoverageStatus.COVERED`
- **THEN** `cross_round.covered[dim]` 被赋值为「✅ 已覆盖」，`to_prompt()` 输出「已覆盖维度」段落含该维度，R2 不会重复审计该维度

### Requirement: search_code 工具参数契约明确

`FileSearchTool`（工具名 `search_code`）的 description SHALL 明确声明必填参数名为 `keyword`，并显式提示不得使用 `query`、`pattern` 等别名。当 LLM 以正确参数名 `keyword` 调用时，工具 MUST 正常执行搜索；当缺失 `keyword` 时，MUST 返回结构化错误而非抛出 `TypeError: missing 1 required positional argument`。

#### Scenario: LLM 以正确参数名调用 search_code
- **WHEN** LLM 调用 `search_code` 并传入 `{"keyword": "eval("}`
- **THEN** 工具在项目内搜索该关键字并返回匹配行与上下文

#### Scenario: 缺失 keyword 参数时返回结构化错误
- **WHEN** LLM 调用 `search_code` 但未传入 `keyword`
- **THEN** 工具返回 `ToolResult(success=False, error=...)`，不抛出未捕获的 `TypeError`

### Requirement: 任务统计计数器与 findings 表保持一致

`agent_tasks.py` 完成回调中，`critical_count`/`high_count`/`medium_count`/`low_count`/`files_with_findings` SHALL 通过对已落库的 `AgentFinding` 执行 SQL 聚合查询（`GROUP BY severity` / `COUNT(DISTINCT file_path)`）赋值，不得遍历含幻觉 finding 的原始列表做 `+=` 累加。`verified_count` SHALL 仅统计 `verification_status in (confirmed/verified/true_positive) AND is_verified=True` 的 finding，`not_reproducible`/`false_positive` 一律不计入。

#### Scenario: 严重度计数器与 findings 表一致
- **WHEN** 任务完成回调执行计数器赋值
- **THEN** `task.critical_count + high_count + medium_count + low_count` 等于 `SELECT count(*) FROM agent_findings WHERE task_id=:id`，且各 severity 计数等于对应 `GROUP BY severity` 结果

#### Scenario: files_with_findings 等于去重文件数
- **WHEN** 任务完成回调执行 files_with_findings 赋值
- **THEN** `task.files_with_findings` 等于 `SELECT count(DISTINCT file_path) FROM agent_findings WHERE task_id=:id AND file_path IS NOT NULL`

#### Scenario: not_reproducible 不计入 verified
- **WHEN** 某条 finding 的 `verification_status=not_reproducible` 但 `is_verified=True`
- **THEN** 该 finding 不计入 `task.verified_count`

#### Scenario: 空路径回填
- **WHEN** SaveFindings 落库前某 finding 的 `file_path` 为空但 `source`/`code_snippet` 非空
- **THEN** 从 source/snippet 解析回填 file_path；仍为空则不计入 files_with_findings

### Requirement: token 预算硬门禁

Orchestrator 主循环每轮 + 子 Agent 每轮 SHALL 检查 `total_tokens >= config.token_budget`，超限时 SHALL 设置 `status=COMPLETED_WITH_GAPS`、`coverage_bypass_info.reason=token_budget_exhausted` 并退出循环，不得继续消耗 token。`token_budget` 字段 SHALL 在 agent 代码中被实际读取，不得为陈列字段。

#### Scenario: token 达预算退出
- **WHEN** 累计 token 消耗达到 `config.token_budget`
- **THEN** 任务退出循环，status=COMPLETED_WITH_GAPS，coverage_bypass_info.reason=token_budget_exhausted

#### Scenario: token 未达预算不退出
- **WHEN** 累计 token 消耗未达 `config.token_budget`
- **THEN** 任务正常继续执行，不被 token 门禁中断

### Requirement: 覆盖率放行携带完整信息

Orchestrator 所有覆盖率放行分支（5 次拦截放行、analysis 重复调度放行、主迭代耗尽放行、**任务总耗时超时放行**）SHALL 在 `coverage_bypass_info` 中携带 `gaps`、`block_count`、`reason`、`covered_count`、`total_dimensions`，并通过 `result.metadata` 传到完成回调，使 `COMPLETED_WITH_GAPS` 状态可追溯。

#### Scenario: 放行分支携带完整 coverage_info
- **WHEN** 任一覆盖率放行分支触发（含新增的 task_timeout 分支）
- **THEN** `result.metadata.coverage_info` 含 gaps、block_count、reason、covered_count、total_dimensions 五个字段

#### Scenario: 超时放行 reason 为 task_timeout
- **WHEN** `asyncio.wait_for(run_task, timeout=task_timeout)` 抛 `TimeoutError`
- **THEN** 构造的 `AgentResult.metadata.coverage_info.reason` 等于字符串 `"task_timeout"`

### Requirement: 审计阶段完整收尾

reporting 阶段 SHALL 在 `emit_task_complete` 前显式调用 `emit_phase_complete("reporting", ...)`，使 `phase_complete` 事件数与 `phase_start` 事件数对齐。

#### Scenario: reporting 阶段完整收尾
- **WHEN** 任务进入 reporting 阶段生成报告
- **THEN** 先 emit_phase_complete("reporting") 再 emit_task_complete，phase_complete 数 ≥ phase_start 数 - 1

### Requirement: agent 审计任务质量评分

`agent_tasks.py` 完成回调 SHALL 调用 `_calculate_quality_score` 基于验证覆盖率、误报率、finding 平均置信度计算 `task.quality_score` 并赋值，不得恒为 0。

#### Scenario: agent 任务质量分非零
- **WHEN** agent 审计任务完成且有 finding
- **THEN** `task.quality_score` 为 (0, 100] 区间的非零值

### Requirement: 任务响应包含验证状态分布

任务详情/列表 response SHALL 包含 `verification_status_breakdown` 字段，聚合该任务下所有 `AgentFinding` 的 `verification_status` 分布，含 `confirmed`/`not_reproducible`/`needs_context`/`false_positive` 四类计数。`verified_count` 严格语义（仅 confirmed 且 is_verified=True）SHALL 保持不变。前端 SHALL 展示完整分布而非仅 verified_count。

#### Scenario: breakdown 四字段正确聚合
- **WHEN** 任务有 15 个 finding（confirmed 2 / not_reproducible 9 / needs_context 3 / false_positive 1）
- **THEN** response.verification_status_breakdown = {confirmed:2, not_reproducible:9, needs_context:3, false_positive:1}，verified_count 仍为 2

#### Scenario: 前端展示完整分布
- **WHEN** 用户查看 AI 页面 TaskReferencePanel
- **THEN** 展示"已验证 2 / 不可复现 9 / 待确认 3 / 误报 1"四类，而非仅"已验证 2"

### Requirement: verification agent 支持浏览器验证

verification agent SHALL 注册 `sandbox_browser` 工具，封装 playwright 在沙箱内驱动 chromium 执行浏览器自动化验证，支持 navigate/screenshot/eval/click/get_text 五个 action。verification prompt SHALL 引导在 XSS（反射型/DOM型）、开放重定向、SSRF 场景使用浏览器验证。工具调用失败 SHALL 优雅降级返回 ToolResult(success=False)，不阻断验证流程。

#### Scenario: sandbox_browser navigate 成功
- **WHEN** verification agent 调用 sandbox_browser(action="navigate", url="http://target/xss?payload=<script>alert(1)</script>")
- **THEN** 工具在沙箱内启动 chromium（--no-sandbox --headless）导航到 URL，返回页面标题/状态

#### Scenario: 浏览器调用失败优雅降级
- **WHEN** sandbox_browser 调用超时或 chromium 启动失败
- **THEN** 返回 ToolResult(success=False, error=...)，verification 流程继续不中断

#### Scenario: prompt 引导浏览器验证
- **WHEN** verification prompt 构建时
- **THEN** 工具描述含 XSS/重定向/SSRF 的浏览器验证引导文案

### Requirement: 沙箱项目挂载路径统一

`SandboxManager.execute_tool_command` SHALL 将项目目录挂载到 `/workspace/src`（与 `execute_with_files` 一致），working_dir 设为 `/tmp`。`execute_python` SHALL 将 PoC 脚本写入 `/tmp/__poc.py`（tmpfs 可写）。`_command_needs_project_mount` SHALL 优先判断命令（含 heredoc 写入的脚本内容）是否含 `/workspace/` 路径引用：含则返回 True（需挂载项目以读取文件），否则对 heredoc（`<<`）写入命令返回 False（不挂载）。

#### Scenario: execute_tool_command 挂载到 /workspace/src
- **WHEN** SandboxTool 调用 execute_tool_command 执行含项目文件引用的命令
- **THEN** 项目目录挂载到容器 /workspace/src，PoC 脚本通过 /workspace/src/{file_path} 能找到项目文件

#### Scenario: 含 /workspace/ 的命令仍挂载项目
- **WHEN** 命令含 `/workspace/src/xxx.py` 路径引用
- **THEN** `_command_needs_project_mount` 返回 True，项目被挂载，而非误判为不挂载

#### Scenario: heredoc 写入脚本含 /workspace/ 引用仍挂载项目
- **WHEN** 命令使用 heredoc（`<<`）写入 PoC 脚本，且脚本内容含 `/workspace/src/{file_path}` 引用
- **THEN** `_command_needs_project_mount` 返回 True，项目被挂载（`/workspace/` 检查优先于 heredoc 短路）

#### Scenario: heredoc 命令不挂载
- **WHEN** 命令使用 heredoc（`<<`）写入临时文件，且不含 `/workspace/` 路径引用
- **THEN** `_command_needs_project_mount` 返回 False，不挂载项目

### Requirement: verification verdict 判定标准明确

verification agent prompt 的"验证判定标准" SHALL 包含明确的 confirmed 判定示例：SSTI/模板注入（`{{7*7}}`→49）、XSS（payload 进入输出/DOM）、命令注入（观察到命令执行）均判 confirmed。仅当沙箱无法验证且代码分析也无法确认可利用性时判 not_reproducible，信息不足判 needs_context。SHALL 明确提示"不要把已成功复现的漏洞标为 not_reproducible"。

#### Scenario: prompt 含 SSTI confirmed 示例
- **WHEN** verification prompt 构建
- **THEN** 含 `{{7*7}}`→49 判 confirmed 的明确示例文案

### Requirement: 验证门禁按成功次数与逐 finding 覆盖

verification agent SHALL 维护 `_sandbox_exec_attempts`（调用次数）、`_sandbox_exec_success`（成功次数）、`_verified_finding_indices`（已成功验证的 finding 索引集合）。失败的 sandbox_exec SHALL 不计入 `_sandbox_exec_success`。finish 门禁 SHALL 要求所有 finding 已成功验证或显式标记无法沙箱验证，仅靠失败调用凑数 SHALL 被拒绝。

#### Scenario: 失败调用不计数 success
- **WHEN** sandbox_exec 调用返回 success=False
- **THEN** `_sandbox_exec_success` 不增加，该 finding 不加入 `_verified_finding_indices`

#### Scenario: 未全覆盖 finding 时拒绝 finish
- **WHEN** LLM 输出 Final Answer 但存在 finding 未成功验证且未标记跳过
- **THEN** 系统拒绝 finish，要求继续验证剩余 finding

#### Scenario: 全验证成功时放行 finish
- **WHEN** 所有 finding 都已成功验证（加入 `_verified_finding_indices`）
- **THEN** finish 门禁放行，允许输出 Final Answer

### Requirement: Orchestrator 长耗时同步操作不得冻结事件循环

Orchestrator 的 Semgrep prescan 及任何执行时间可能超过 15 秒的外部子进程调用 SHALL 使用 `asyncio.create_subprocess_exec` 或 `asyncio.to_thread` 等异步机制，绝不得直接使用同步 `subprocess.run`/`subprocess.check_output`。事件循环 MUST 在长耗时操作期间保持响应能力：SSE 心跳（10-15 秒间隔）能正常发送、`request.is_disconnected()` 能被检测、其他并发任务能正常调度。

Semgrep prescan 中每个规则集 SHALL 在开始前发出 `tool_call_start` 事件、结束后发出 `tool_call_end`（或 `tool_call_error`）事件，使前端 `useResilientStream` 进入长操作心跳窗口（默认 180 秒）。

#### Scenario: Semgrep prescan 期间事件循环保持响应
- **WHEN** Orchestrator 执行 `_run_semgrep_prescan()`，单个规则集耗时 45 秒以上
- **THEN** 期间 SSE 心跳事件仍按周期发送到客户端，客户端不因心跳超时而断连

#### Scenario: 每个规则集包裹 tool_call 事件
- **WHEN** Semgrep prescan 开始扫描规则集 `p/security-audit`
- **THEN** 事件流中先出现 `tool_call_start`（tool_name=`semgrep_prescan_p_security_audit`），扫描结束后出现 `tool_call_end`

#### Scenario: 长耗时期间前端进入长操作心跳窗口
- **WHEN** 前端收到 `tool_call_start` 事件（工具名以 `semgrep_prescan_` 开头或任何 `tool_call`/`tool_call_start` 事件）
- **THEN** 前端 `useResilientStream` 心跳超时切换到 `longOperationHeartbeatTimeout` (180 秒)，直到收到对应 `tool_call_end`/`tool_result` 事件后恢复默认 (45 秒)

### Requirement: 任务取消路径发出终态事件

`cancel_agent_task` 端点和 `_execute_agent_task` 的 `asyncio.CancelledError` 分支 SHALL 在更新 `task.status = CANCELLED` 之后调用 `event_emitter.emit_task_cancelled(...)`，使 SSE 流的 `stream_events` 能通过 `task_cancel` 终端事件类型正常退出，前端立即感知取消而不用等心跳超时。

#### Scenario: 手动取消任务发出 task_cancel
- **WHEN** 客户端调用 `POST /agent-tasks/{id}/cancel`
- **THEN** 后端在更新 DB 状态后发出 `task_cancel` SSE 事件，前端在下一次事件循环即感知任务已取消（无需等 15 秒心跳）

#### Scenario: CancelledError 分支也发出 task_cancel
- **WHEN** `_execute_agent_task` 内部触发 `asyncio.CancelledError`（如 taskrunner.cancel）
- **THEN** `except CancelledError` 分支调用 `emit_task_cancelled` 后再 raise，SSE 流正常关闭

### Requirement: 超时保护路径使用正确的 emitter 方法

`_execute_agent_task` 中 `asyncio.wait_for(run_task, timeout=task_timeout)` 的 `TimeoutError` 分支 SHALL 使用 `event_emitter.emit_warning(...)` 而不是不存在的 `event_emitter.emit_event('warning', ...)`。前者在 `AgentEventEmitter` 类上定义，后者会抛 `AttributeError` 导致任务被误判为 FAILED 且不发终态事件。

#### Scenario: 任务超时正常降级为 COMPLETED_WITH_GAPS
- **WHEN** 任务运行超过 `task.timeout_seconds`（默认 1800 秒）
- **THEN** `emit_warning` 成功发出，任务 status 被设置为 `COMPLETED_WITH_GAPS`（`coverage_bypass_info.reason=task_timeout`），`emit_task_complete` 正常发出，SSE 流正常关闭

