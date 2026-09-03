# Tasks: structured-output-protocol

## Phase 1: 请求链修复（三断点）

### Task 1: LLMRequest/service/adapter 参数链打通
- Files: `backend/app/services/llm/types.py`（LLMRequest 加 tools/response_format/extra_params）、`backend/app/services/llm/service.py`（chat_completion/stream 签名与透传）、`backend/app/services/llm/adapters/litellm_adapter.py`（_send_request kwargs + _native_openai_call 扩展 + extra_body）
- Interfaces: Consumes tools/response_format/extra_params；Produces 三层透传（native_openai_call 为 SGLang 实际路径必须覆盖）
- TDD: 失败测试（传 tools 不再 TypeError；native_openai_call 构造的 body 含 tools/response_format/repetition_penalty；litellm 路径 kwargs 同）→ 实现 → 通过 → commit

### Task 2: 关思考护栏
- Files: `backend/app/services/llm/adapters/litellm_adapter.py`（_assert_no_thinking_off）
- Interfaces: 拦截 enable_thinking/<|think_off|>/chat_template_kwargs 思考开关 → warning + 剥除
- TDD: 失败测试（三种注入形态必被拦 + 正常请求不受影响）→ 实现 → 通过 → commit

## Phase 2: 流式三字段分流

### Task 3: 适配器 chunk 分流（kind 字段）
- Files: `backend/app/services/llm/adapters/litellm_adapter.py`（:508-522 拆 or 链，yield 带 kind）
- Interfaces: chunk 增加 kind: "reasoning"|"content"；accumulated 拆分（正文累计不含思考）
- TDD: 失败测试（混合 delta → 两种 kind 独立 yield；done.content 仅含正文）→ 实现 → 通过 → commit

### Task 4: Agent 循环分流 + 正文事件
- Files: `backend/app/services/agent/agents/base.py`（stream_llm_call 按 kind 分流；emit_content_token 新增）
- Interfaces: kind=reasoning → thinking_token（现有前端思考区）；kind=content → content_token；无 kind（旧后端）→ 现状不变
- TDD: 失败测试（混合 chunk → 思考/正文事件分离；done 累计正文不含思考噪声）→ 实现 → 通过 → commit

### Task 5: 前端分流渲染
- Files: `frontend/src/shared/api/agentStream.ts`（kind 字段 + content_token 类型）、`frontend/src/pages/AgentAudit/hooks/useResilientStream.ts`（:274-281 分流）、`frontend/src/pages/AgentAudit/index.tsx`（:667-690 正文流式日志）
- Interfaces: content token → 正文流式日志；reasoning token → 现有"思考"区；tool_calls 事件 → 工具逻辑（现状保留）
- TDD: 组件/hook 测试（分流断言 + 旧格式兼容）→ 实现 → 通过 → commit

## Phase 3: 能力探测与协议选择

### Task 6: 多后端能力探测层
- Files: `backend/app/services/agent/structured_output.py`（BackendCapabilities + probe_backend_capabilities + 缓存）、`backend/app/core/config.py`（LLM_REPETITION_PENALTY: float = 1.15）
- Interfaces: 按 (base_url, model) 缓存；失败降级全 False；探测结果 info 事件（含参数摘要，满足 llm-call-params 可追溯）
- TDD: 失败测试（mock HTTP 三态：双可用/部分/失败降级 + 缓存命中）→ 实现 → 通过 → commit

## Phase 4: 原生工具调用协议

### Task 7: Orchestrator 调度轮 tools 协议
- Files: `backend/app/services/agent/agents/orchestrator.py`（:961 调用点 + tool_calls 分发映射 + finish/summarize 语义保持）
- Interfaces: tools=[dispatch_agent, finish, summarize]（get_tool_descriptions 改造复用）；tool_calls → 现有 action 分发等价映射；文本路径保留
- TDD: 失败测试（mock tool_calls 响应 → dispatch/finish/summarize 三种分发等价于文本协议；探测不可用 → 文本路径回归）→ 实现 → 通过 → commit

### Task 8: Analysis/Verification tools 协议 + submit_findings 工具
- Files: `backend/app/services/agent/agents/analysis.py`（:709/:467）、`backend/app/services/agent/agents/verification.py`（:1094）、`backend/app/services/agent/agents/base.py`（submit_findings 注册与 tool_calls→Final Answer 语义）
- Interfaces: submit_findings 工具参数=findings JSON Schema；模型调用它即 is_final（arguments 直接 json.loads 进 all_findings 流程）；强制总结轮 guided_json 注入（能力可用时）
- TDD: 失败测试（submit_findings tool_calls → findings 流程触发且 arguments 合法 JSON；guided 注入仅强制总结轮；降级路径文本协议回归）→ 实现 → 通过 → commit

## Phase 5: 参数治理与端到端

### Task 9: repetition_penalty/temperature 落地
- Files: 两台生产 user_configs（llmTemperature 0.3→0.6、新增 repetitionPenalty=1.15）、`backend/app/services/llm/service.py`（config 构建读 repetitionPenalty）
- Interfaces: 端点请求体实测含两参数（SGLang smoke）；vLLM/ollama 不支持的参数由后端忽略（OpenAI 兼容行为）
- TDD: config 构建测试 + SGLang 真实端点 smoke（tools+guided+params 三合一）→ commit

### Task 10: 端到端验证
- Files: 无代码（验证任务）
- 验收：真实审计任务——①正文区无思考噪声（乱码消失）②无格式错误熔断（协议化后模型不再背文本格式）③findings 产出④事件流三字段形态正确⑤参数摘要事件可见；结果记 observations 并汇报老板
