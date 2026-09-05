# Delta Spec: llm-call-params

## ADDED Requirements

### Requirement: 采样参数 SHALL 可配置并透传至推理端点

LLM 请求 SHALL 支持 `repetition_penalty`（默认 1.15，可配置 1.1-1.2）与 `temperature`（用户配置，Qwen3 thinking 模型建议 0.6-0.7）的透传：参数经用户配置（user_configs.llmConfig）→ LLMService → 适配器 → 推理端点请求体，provider 特有参数（如 repetition_penalty）通过 litellm extra_body 或等价机制到达端点。参数 MUST NOT 被 litellm drop_params 静默丢弃（透传失败 SHALL 有 warning 日志）。推理后端不支持的参数 SHALL 由后端自行忽略（OpenAI 兼容行为），调用侧不因后端差异报错。

#### Scenario: repetition_penalty 到达端点
- **WHEN** 用户配置或默认值 repetition_penalty=1.15
- **THEN** 发往 SGLang 的请求体含 repetition_penalty=1.15（实测端点接受且输出正常），退化循环发生率显著下降

#### Scenario: temperature 走用户配置
- **WHEN** 用户配置 llmTemperature=0.6
- **THEN** 所有 Agent 调用的请求体 temperature=0.6（thinking 模型推荐区间），不再被代码层覆盖

#### Scenario: 参数透传失败可观测
- **WHEN** litellm 因适配器映射缺失丢弃 provider 特有参数
- **THEN** 日志出现 warning（含被丢参数名），可据此修补透传链

### Requirement: 调用参数变更 SHALL 全链路可追溯

采样参数（temperature/repetition_penalty/max_tokens）的实际生效值 SHALL 在任务启动时以 info 事件记录（后端类型+参数值），用户可从事件流确认当次任务用的参数组合；容器日志同步记录。

#### Scenario: 任务启动时参数可查
- **WHEN** 审计任务启动
- **THEN** 事件流出现参数摘要事件（含 temperature/repetition_penalty/max_tokens 与推理后端类型）
