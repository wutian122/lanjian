# Design: structured-output-protocol

## Context

老板定案（2026-09-03）：自定义结构化输出，多后端支持（SGLang/vLLM/ollama）。服务端实测：SGLang（10.129.2.101，`--tool-call-parser qwen3_coder`）原生 function calling 0.7s 完美返回、guided_json（response_format json_schema）输出严格合法 JSON。调用侧三断点已勘察确认（probe-stream 报告）。本变更治"协议层"——模型不再需要背自由文本格式；C 变更（sandbox-verification-hard-gate）治"策略层"（产出下限/沙箱门禁），并行独立。

## Architecture

### 层次 1：能力探测层（structured_output.py 死代码改造）

- 新增 `BackendCapabilities`：`{tools: bool, guided_json: bool, guided_style: "response_format_json_schema"|"format_json_schema"|None}`（SGLang/vLLM 用 response_format；ollama 用 format 字段）
- `probe_backend_capabilities(base_url, api_key, model)`：发两个轻量探测请求（①假 tools 定义看是否返回 tool_calls；②最小 json_schema 看是否严格遵循），各 5s 超时；结果按 `(base_url, model)` 进程内缓存
- 探测失败/超时 → 全 False → 全链路降级 ReAct 文本协议（零破坏）；探测结果发射 info 事件（后端类型+能力+探测耗时，满足 llm-call-params 可追溯）
- `is_supported` 从静态枚举白名单改为读能力字典

### 层次 2：请求链三断点修复

1. `types.py LLMRequest`（:49-55）加 `tools: Optional[List[dict]] = None`、`response_format: Optional[dict] = None`、`extra_params: Optional[dict] = None`（repetition_penalty 等 provider 特有参数）——消除"传 tools 必 TypeError"的既有硬伤（service.py:459-461）
2. `service.py`：`chat_completion_stream`/`chat_completion` 签名扩展三参数并透传进 LLMRequest
3. `litellm_adapter.py`：
   - `_send_request` kwargs 构造 tools/response_format；`extra_params` 经 litellm `extra_body` 传递（repetition_penalty 到 SGLang 实测接受）
   - **`_native_openai_call`（:232-260，SGLang 实际走的路径）同步扩展**：现只传 model/messages/temperature/max_tokens，加 tools/response_format/extra_params（repetition_penalty 直接入 body）
4. 请求构造层新增**关思考护栏** `_assert_no_thinking_off(kwargs)`：检测 `enable_thinking`/`<|think_off|>`/`chat_template_kwargs` 中的思考开关 → 拦截 + warning 日志（spec thinking-stream-separation 第三个 Requirement）

### 层次 3：流式三字段分流

- `litellm_adapter.py:508-522`：chunk dict 增加 `kind` 字段——`reasoning_content`/`thinking` 来源 → `kind="reasoning"`，`content` → `kind="content"`（两者同现时各自独立 yield，不再 `or` 链合并）；`accumulated` 相应拆分（accumulated_content 仅累计 content，accumulated_reasoning 累计思考）
- `base.py stream_llm_call`（:1080-1245）：`kind=="reasoning"` 的 token 走 `emit_thinking_token`（现有前端"思考"区）；`kind=="content"` 的 token 走新增 `emit_content_token`（新事件类型）；`kind` 缺失的 chunk（旧后端/文本协议）保持现状全部走 thinking_token（兼容）
- done 块的 `accumulated = chunk["content"]` 语义拆分：正文判定只认 content 类累计（Final Answer 解析输入不再混入思考流）

### 层次 4：前端分流渲染

- `agentStream.ts`：`StreamEventData` 增加 `kind?: "reasoning" | "content"`；新增 content_token 事件类型
- `useResilientStream.ts`（:274-281 生产路径）：`kind==="content"` 的 token 进正文回调；`thinking_token`（reasoning）保持现有 thinkingBuffer
- `index.tsx`（:667-690）：正文 token 新增正文流式日志（区别于"思考"标签）；`cleanThinkingContent` 正则补丁保留（兼容旧后端混合流）
- 思考区保持可折叠视觉（现有紫色标签已区分），正文区永不混入 reasoning 来源

### 层次 5：ReAct → 原生工具调用协议

- **Final Answer 即工具调用**（设计核心）：Analysis/Verification 注册 `submit_findings` 工具（参数 = findings JSON Schema，与现有 Final Answer 契约字段一致）；模型调用它 = 宣布分析完成，`function.arguments` 由服务端 tool-call-parser 保证合法 JSON——不再依赖文本 Final Answer + json-repair
- **Orchestrator 调度轮**：tools = `[dispatch_agent, finish, summarize]` 三个函数定义（参数即现有 Action Input schema）；`tool_calls` 响应直接映射到现有 action 分发（`execute_tool`/`_dispatch_agent`/finish 逻辑），文本 Action 解析路径保留（降级共存，spec llm-structured-output 第三 Requirement）
- 六个流式调用点（probe-stream C10 清单：recon.py:441/607、analysis.py:709/467、verification.py:1094、orchestrator.py:961）按 agent 类型注入对应 tools 定义（能力探测可用时）；`base.py get_tool_descriptions`（:1015-1037，已有 OpenAI function 格式生成能力）作为定义来源改造复用
- 强制总结轮（`_run_forced_summary`）在 guided_json 可用时注入 findings schema 的 response_format（降级时维持现状提示词约束）

### 层次 6：参数治理

- `core/config.py` 新增 `LLM_REPETITION_PENALTY: float = 1.15`（env 可覆盖）；用户配置 `llmConfig.repetitionPenalty` 优先；经 extra_params → extra_body/native body 到端点
- 用户配置 temperature 调整 0.3 → **0.6**（Qwen3 thinking 推荐区间，老板实测建议 0.6-0.7）——两台生产 user_configs 落库（老板已定方向）
- 任务启动时发射参数摘要 info 事件（temperature/repetition_penalty/max_tokens/后端能力探测结果）

## Data Flow

请求链：user_config → LLMConfig → LLMRequest(+tools/response_format/extra_params) → adapter（native_openai_call 或 litellm）→ SGLang/vLLM/ollama。
响应链：delta.reasoning_content → kind=reasoning → thinking_token 事件 → 前端思考区；delta.content → kind=content → content_token 事件 → 前端正文区；tool_calls → execute_tool/_dispatch_agent 分发。
探测链：任务启动 → probe_backend_capabilities → 缓存 + info 事件 → Agent 循环按能力选协议 → 失败自动降级文本协议。

## Error Handling

- 探测失败/超时 → 降级，任务继续；guided 注入失败 → 静默降级文本 Final Answer（json-repair 兜底保留）；tools 调用报错 → 单轮重试一次后降级该轮为文本协议
- 关思考护栏拦截 → warning + 继续正常请求
- 全部新逻辑非致命，旧后端（无 kind chunk）行为完全不变

## Testing Strategy

- 每项 TDD（RED→GREEN→回归），基线 763 passed / 4 failed 不得恶化
- 探测层：mock HTTP（可用/不可用/超时三态）；分流层：构造混合 chunk 断言 kind 分离与事件类型；协议层：mock tool_calls 响应断言 execute_tool 分发等价性；护栏：注入尝试必被拦
- 多后端：SGLang 实测（真实端点 smoke：tools + guided + repetition_penalty 三合一）；vLLM/ollama 以 mock 响应形态覆盖（探测样式差异）
- 端到端：真实审计任务核对——乱码消失（正文区无思考噪声）、无格式错误熔断、findings 产出、事件流三字段形态
