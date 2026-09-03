# Delta Spec: llm-structured-output

## ADDED Requirements

### Requirement: 多推理后端能力探测与协议自动选择

系统 SHALL 在任务启动（或 LLM 服务首次调用前）对当前推理后端进行能力探测（SGLang/vLLM/ollama 等 OpenAI 兼容端点），探测两项能力：原生工具调用（tools 参数 → tool_calls 响应）与结构化 JSON 输出（response_format json_schema / format）。探测结果（含探测方式与时间）SHALL 记录到任务事件流。能力可用时 Agent 循环 SHALL 优先使用原生协议；探测失败或调用报错时 SHALL 自动降级到现有 ReAct 文本协议，任务 MUST NOT 因此中断。探测结果按 LLM 服务地址缓存（同服务不重复探测）。

#### Scenario: SGLang 后端探测到双能力并走原生协议
- **WHEN** 推理后端为 SGLang（tool-call-parser 已配置，实测 tools/guided 双可用）
- **THEN** 探测记录两项能力为可用，Agent 循环 LLM 调用携带 tools 参数，模型响应为 tool_calls 形态并直接分发到工具执行

#### Scenario: 不支持 function calling 的后端自动降级
- **WHEN** 推理后端不支持 tools（探测请求报错或返回不支持）
- **THEN** 记录探测结果，Agent 循环保持现有 ReAct 文本协议（Thought:/Action:/Action Input:），审计流程行为与改造前一致

#### Scenario: 探测本身失败不阻塞任务
- **WHEN** 能力探测请求超时/异常
- **THEN** 按能力不可用降级处理并发射 warning 事件，任务继续

### Requirement: Final Answer 轮 SHALL 注入结构化 JSON 约束（能力可用时）

Analysis 与 Verification 的 Final Answer 产出轮，在后端支持结构化 JSON 时 SHALL 携带 findings JSON Schema 的 `response_format`（guided），使输出为严格合法 JSON；中间 ReAct 轮 MUST NOT 注入（保持工具调用/简短决策形态）。schema SHALL 与现有 Final Answer 契约字段一致（vulnerability_type/severity/title/description/file_path/line_start/code_snippet/source/sink/suggestion/confidence/needs_verification）。注入失败或后端不支持时 SHALL 静默降级为文本协议。

#### Scenario: Final Answer 输出为严格合法 JSON
- **WHEN** SGLang 后端 Analysis 进入 Final Answer 轮且 guided 能力可用
- **THEN** 该轮请求携带 findings schema 的 response_format，返回内容直接 `json.loads` 成功，不依赖 json-repair 修复

#### Scenario: 中间轮不受影响
- **WHEN** Analysis 处于中间 ReAct 轮（读文件/搜索）
- **THEN** 该轮请求不携带 response_format，正常输出工具调用

### Requirement: 原生工具调用的分发映射 SHALL 与文本协议共存

当模型响应为 `tool_calls` 形态时，Agent 循环 SHALL 将 `function.name` 映射为现有 `execute_tool` 的工具名、`function.arguments`（JSON 字符串）解析为 `action_input`，复用现有工具执行/事件发射/循环计数逻辑；文本协议（Action: xxx）路径 MUST 保留且行为不变。两种形态的轮次计数、token 统计、事件流形态 MUST 一致可观测。

#### Scenario: tool_calls 直接分发工具
- **WHEN** 模型返回 tool_calls=[{"function": {"name": "read_file", "arguments": "{\"file_path\": \"a.py\"}"}}]
- **THEN** execute_tool("read_file", {"file_path": "a.py"}) 被调用，observation 正常入历史，事件流含等价的 tool_call 记录

#### Scenario: 文本协议降级后仍工作
- **WHEN** 后端能力探测为不支持 tools，模型按提示词输出 Action: read_file 文本
- **THEN** 现有文本解析分发路径正常工作，行为与改造前一致
