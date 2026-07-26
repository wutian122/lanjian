# llm-adapter Spec

## Purpose

lanjian LiteLLM 适配器层的流式错误诊断与超时配置规范。确保 LLM 流式调用错误可被诊断，且超时配置回退链一致。

## Requirements

### Requirement: LLM 流式错误必须可诊断

LiteLLM 适配器的流式 `except Exception` 分支 SHALL 在异常消息为空时补全诊断信息，包括异常类型名（`type(e).__name__`）。当发生认证、网络或未知错误时，日志 MUST 包含足够信息以区分错误类别，不得输出空消息的 `Stream error: `。

诊断信息 MUST NOT 直接拼接 `e.args` 原文到会返回给前端的 `error` 字段（避免 API Key、URL 等敏感数据通过 SSE 泄露）。`e.args` 仅用于日志记录，返回前端的 `error` 字段仅含异常类型名与 args 数量。

#### Scenario: 异常消息为空时补全诊断
- **WHEN** LiteLLM 流式调用抛出 `Exception` 且 `str(e)` 为空字符串
- **THEN** 日志记录含异常类型名，而非 `Stream error: `

#### Scenario: 异常消息非空时保持原样
- **WHEN** LiteLLM 流式调用抛出 `Exception` 且 `str(e)` 非空
- **THEN** 日志记录原异常消息，行为不退化

#### Scenario: 敏感数据不泄露到前端
- **WHEN** 异常 `e.args` 含 API Key 或内部 URL
- **THEN** 返回前端的 `error` 字段仅含异常类型名与 args 数量，不包含 args 原文

### Requirement: 流式超时配置回退链一致

`LLMService.get_agent_timeout_config()` SHALL 以全局 `settings.LLM_FIRST_TOKEN_TIMEOUT` 与 `settings.LLM_STREAM_TIMEOUT` 作为回退默认值。当用户未在 DB 配置自定义超时时，BaseAgent 的流式首 Token 超时与 Stream 超时 MUST 取全局配置值（120s），不得回退到硬编码 60s。

#### Scenario: 无用户自定义超时时取全局配置
- **WHEN** 用户未在 DB 配置 LLM 超时，全局 `LLM_FIRST_TOKEN_TIMEOUT=120`
- **THEN** `get_agent_timeout_config()['llm_first_token_timeout']` 返回 120

#### Scenario: 用户自定义超时优先
- **WHEN** 用户在 DB 配置了 LLM 首 Token 超时为 90s
- **THEN** `get_agent_timeout_config()['llm_first_token_timeout']` 返回 90，全局配置不覆盖用户配置
