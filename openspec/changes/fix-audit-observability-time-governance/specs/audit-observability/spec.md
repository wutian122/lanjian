# Delta Spec: audit-observability

## ADDED Requirements

### Requirement: LLM 输出截断 MUST 可观测且可归因

Agent 流式 LLM 调用消费端（`base.py` `stream_llm_call` 的 done 块处理）SHALL 读取 adapter 已返回的 `finish_reason`；当 `finish_reason == "length"` 时 MUST：发射 `warning` 事件（消息含"输出被 max_tokens 截断"、agent 名与当轮迭代号）；将该信息写入本轮 conversation_history 的系统提示（供下一轮 LLM 知晓）；Final Answer JSON 解析失败时，失败原因 SHALL 包含"疑似截断"线索，不得静默回落空 findings。

#### Scenario: Final Answer 被 max_tokens 截断时告警
- **WHEN** Analysis 输出 Final Answer 途中触发 `finish_reason=length`
- **THEN** 事件流出现 warning"输出被截断"，该轮解析若失败，warning 消息与容器日志均可归因到截断

#### Scenario: 非 Final Answer 轮被截断
- **WHEN** 某中间轮 Thought+Action 输出触发 length 截断
- **THEN** 发射 warning 事件，下一轮对话历史含截断提示，LLM 可重试输出

### Requirement: Analysis 循环 observation 注入历史前 MUST 截断

Analysis ReAct 循环将工具 observation 追加到 `_conversation_history` 前 SHALL 执行保头尾截断（默认 4000 字符，头 1500 + 尾 1500 + 省略标注，与 verification 的 `_truncate_observation_for_history` 策略一致，常量复用 `observation_history_max_chars` 配置）。截断 MUST 不影响工具结果向 orchestrator 的完整上报。

#### Scenario: 超长工具输出不再撑爆上下文
- **WHEN** `read_file` 返回 50000 字符的文件内容
- **THEN** 追加进 conversation_history 的 observation ≤ 4000 字符（保头尾），后续每轮 prefill 显著下降

#### Scenario: 短输出不受影响
- **WHEN** 工具输出 500 字符
- **THEN** 原样追加，无截断标注

### Requirement: Analysis 执行状态 MUST 上报以推进跨轮去重

Analysis Agent 完成时（无论 findings 是否为 0），结果 data SHALL 包含本轮实际执行过的 `files_read`（read_file 的文件路径去重列表）与 `grep_patterns`（search_code/semgrep_scan 的查询模式列表）；orchestrator 收到结果后 SHALL 将其并入 `_search_registry` 并反映到下一轮 `CrossRoundContext`（已读文件/已执行搜索的"禁止重复"约束随之更新）。

#### Scenario: 0 findings 时第二轮上下文仍推进
- **WHEN** 第一轮 analysis 返回 0 findings 但 files_read 含 15 个文件、grep_patterns 含 8 个模式
- **THEN** 第二轮派发的 cross_round_context 包含这些文件与模式为"禁止重复"，两轮 prompt 决定性成分不再相同

#### Scenario: 上报与实际执行一致
- **WHEN** analysis 本轮调用了 15 次 read_file
- **THEN** files_read 恰为这 15 次调用的路径去重结果，不多报不漏报

### Requirement: 沙箱就绪状态 MUST 真实可检

任务启动时（`agent_tasks.py` preparation 阶段）SHALL 在发射沙箱就绪事件前完成真实检查：docker daemon 可达（`SandboxManager.is_available`）**且** `SANDBOX_IMAGE` 镜像在本地存在（`images.get()`）。两项全过才发射 `init_status="done"`；任一不过 SHALL 发射 `init_status="failed"` 并附 `get_diagnosis()` 内容（含 _init_error 与镜像名），任务继续执行但 verification Agent 的沙箱执行 SHALL 被预判为不可用（避免任务中途才发现）。`SandboxManager.initialize()` SHALL 幂等地包含镜像存在性检查。

#### Scenario: 镜像缺失时明确告警
- **WHEN** docker daemon 正常但 `wutian449/lanjian-sandbox:v6.1.0` 本地不存在
- **THEN** 事件流出现"沙箱镜像缺失"的 failed 状态事件（含镜像名与诊断），不再出现虚假的 "Docker sandbox ready"

#### Scenario: 全部就绪时正常放行
- **WHEN** daemon 可达且镜像存在
- **THEN** 发射 "Docker sandbox ready"（done），行为与现状一致
