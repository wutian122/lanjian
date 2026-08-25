# verification-poc-quality

## Purpose

确保验证引擎的预生成 PoC 按目标文件语言选择正确的危险 sink 检测 pattern、确定性沙箱执行向前端发射 sandbox_* 事件、理论风险 finding 不被整条丢弃、运行时沙箱证据能兜底绑定到 finding，从而让验证状态判定正确且过程可见。

## Requirements

### Requirement: REQ-VP-1 PoC 检测 pattern 按目标文件语言分流

预生成 PoC 生成器（`_gen_sandbox_command`）SHALL 根据目标 `file_path` 的扩展名为 deserialization/ssrf/path_traversal 等漏洞类型选择对应语言的危险 sink 检测 pattern，不得对 Java/PHP/Ruby 等非 Python 目标硬编码 Python pattern。

#### Scenario: Java 反序列化目标

- **WHEN** 一个 `vulnerability_type=deserialization` 且 `file_path` 以 `.java` 结尾的 finding 进入确定性预生成 PoC
- **THEN** 生成的 PoC 检测 pattern 集合含 `ObjectInputStream`/`readObject`/`XMLDecoder` 之一
- **AND** 不含 `pickle.load`/`yaml.load`/`marshal.load` 等 Python 专用 sink

#### Scenario: Python 反序列化目标

- **WHEN** 一个 `vulnerability_type=deserialization` 且 `file_path` 以 `.py` 结尾的 finding 进入确定性预生成 PoC
- **THEN** 生成的 PoC 检测 pattern 集合含 `pickle.load`/`yaml.load`/`marshal.load`/`eval`

#### Scenario: 未知扩展名回退

- **WHEN** finding 的 `file_path` 扩展名不在已知语言集合内
- **THEN** 生成器走 default 通用 PoC 模板，不得崩溃或产出空命令

#### Scenario: SSRF Java 目标

- **WHEN** 一个 `vulnerability_type=ssrf` 且 `file_path` 以 `.java` 结尾的 finding 进入预生成 PoC
- **THEN** 检测 pattern 含 `HttpURLConnection`/`OkHttp`/`RestTemplate`/`HttpClient` 之一
- **AND** 不只含 Python/JS 的 `requests.get`/`fetch`

### Requirement: REQ-VP-2 确定性沙箱执行发射 sandbox_* 事件

确定性预生成沙箱执行路径（`_run_deterministic_sandbox_commands`）与 LLM 调用的 `sandbox_exec` 工具 SHALL 在每次 PoC 执行前后发射 `sandbox_start`/`sandbox_exec`/`sandbox_result` 事件，携带 finding_id、command、exit_code、evidence_summary，使前端 SSE 可见验证过程。

#### Scenario: 预生成 PoC 执行事件

- **WHEN** `_run_deterministic_sandbox_commands` 执行一条预生成 PoC
- **THEN** 执行前发射 `sandbox_start` 事件，含 `finding_id` 与 `command`
- **AND** 执行后发射 `sandbox_result` 事件，含 `finding_id`、`exit_code`、`evidence_summary`

#### Scenario: LLM sandbox_exec 工具调用事件

- **WHEN** Verification Agent 通过 `sandbox_exec` AgentTool 执行 PoC
- **THEN** `sandbox_tool._execute` 发射 `sandbox_exec` 与 `sandbox_result` 事件

#### Scenario: 执行异常仍发事件

- **WHEN** 沙箱执行抛异常
- **THEN** 仍发射 `sandbox_result` 事件标记失败（exit_code 非 0 或 error），不得静默吞掉

### Requirement: REQ-VP-3 理论风险 finding 保留落库

`is_strict_finding` SHALL 保留缺精确 `file_path` 但 `confidence>=0.7` 且有 `title`+`description` 的 finding 进入落库流程，以 `verification_status=needs_context` 落库；仅过滤 `confidence<0.7` 或无 `title`/`vulnerability_type` 的低质量幻觉 finding。

#### Scenario: 理论风险 finding 保留

- **WHEN** 一个 finding 缺 `file_path`（或 `line_start<=0`）但 `confidence>=0.7` 且 `title` 与 `description` 非空
- **THEN** `is_strict_finding` 不阻断该 finding
- **AND** `_save_findings` 将其以 `verification_status=needs_context` 写入 `agent_findings` 表

#### Scenario: 低质量幻觉仍过滤

- **WHEN** 一个 finding `confidence<0.7` 或 `title` 为空或 `vulnerability_type` 为空
- **THEN** `is_strict_finding` 仍返回 False 并被 `_save_findings` 过滤跳过

#### Scenario: 理论风险 finding 可见于前端

- **WHEN** nginx 类"防御纵深缺失"finding 被 Analysis 产出且 confidence>=0.7
- **THEN** 任务 `findings_count>=1`，前端列表可见该 finding

### Requirement: REQ-VP-4 运行时沙箱证据兜底绑定

`_attach_runtime_sandbox_attempts` SHALL 在 finding_id 精确匹配与 `file_path+line_start` 精确匹配均失败时，按 `file_path` 路径后缀 + `vulnerability_type` 组合兜底匹配运行时沙箱证据索引，避免证据 null 导致误判 needs_context。

#### Scenario: finding_id 与精确位置均失配时兜底

- **WHEN** finding 的 `_sandbox_finding_id` 在运行时索引找不到，且 `file_path+line_start` 精确匹配也失败
- **THEN** 按 `file_path` 路径后缀段 + `vulnerability_type` 组合匹配运行时索引
- **AND** 匹配成功时该 finding 的 `sandbox_attempts` 非 null

#### Scenario: 兜底优先级

- **WHEN** 同时存在 finding_id 命中、精确位置命中、组合兜底命中的候选证据
- **THEN** 优先级为 finding_id > 精确位置 > 组合兜底，不得用兜底覆盖精确命中

#### Scenario: 三级匹配全失配

- **WHEN** finding_id、精确位置、组合兜底三级均无命中
- **THEN** `sandbox_attempts` 保持 null，`compute_verification_status` 据此判 `needs_context`（保留现有语义）
