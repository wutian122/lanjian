# Tasks: structured-output-protocol

## Phase 1: 请求链修复（三断点）

- [x] **Task 1 完成**（实施 a81626a + 第 1 轮修复 7849651，review 初审 NEEDS_FIX（Critical：native 路径 extra_body 展开在真实 openai 2.12.0 SDK 下必 TypeError，mock 签名过宽未抓住）→ 重审 CLEAN（变异 RED + 真实 SDK 端到端 + 假端点 HTTP body 三重证据）；测试加固为真实签名 bind 校验（AsyncMock spec 不校验签名/create_autospec 与 @required_args 冲突，实施者实测后走 bind 方案，SDK 升级自动跟进）；litellm 两路径无同型坑核实闭合；记账：litellm extra_body 最终合并属库行为，Task 9 真实 SGLang smoke 仍保留端到端验收）

### Task 1: LLMRequest/service/adapter 参数链打通
- Files: `backend/app/services/llm/types.py`（LLMRequest 加 tools/response_format/extra_params）、`backend/app/services/llm/service.py`（chat_completion/stream 签名与透传）、`backend/app/services/llm/adapters/litellm_adapter.py`（_send_request kwargs + _native_openai_call 扩展 + extra_body）
- Interfaces: Consumes tools/response_format/extra_params；Produces 三层透传（native_openai_call 为 SGLang 实际路径必须覆盖）
- TDD: 失败测试（传 tools 不再 TypeError；native_openai_call 构造的 body 含 tools/response_format/repetition_penalty；litellm 路径 kwargs 同）→ 实现 → 通过 → commit

### Task 2: 关思考护栏
- [x] **Task 2 完成**（实施 7f1ed84 + 第 1 轮修复 2ef85c8，review 初审 NEEDS_FIX（Important：/no_think 无词边界误剥代码常量/URL/observation/工具描述——审查者实测四案例）→ 裁决选项 A（词边界+system/user 作用域收窄+tool/assistant 豁免）→ 重审 CLEAN（7 行为契约全成立+19 案例独立探针）；正则偏差记账：主控裁决单侧边界与 URL 案例自相矛盾，实施者前后双边界修正并被重审判定更优；Minor 记账：socks 代理下 4 个路径测试体 error（环境限制非回归，collection 已修复）、返回值 stripped 消费待 Task 4/6 事件链路补挂）

### Task 2: 关思考护栏
- Files: `backend/app/services/llm/adapters/litellm_adapter.py`（_assert_no_thinking_off）
- Interfaces: 拦截 enable_thinking/<|think_off|>/chat_template_kwargs 思考开关 → warning + 剥除
- TDD: 失败测试（三种注入形态必被拦 + 正常请求不受影响）→ 实现 → 通过 → commit

## Phase 2: 流式三字段分流

- [x] **Task 3 完成**（commit 45ecff0，review CLEAN——done.content 语义切换的消费端逐点核实为预期收益、双 done 守卫正确、纯 content 逐字节零破坏；Minor 记账：①**Task 7/8 必须显式承接 delta.tool_calls 流式聚合**（spec 三字段第三字段，现状不读不传，切 tools 协议后必须有 chunk 出口）；②reasoning_content or thinking 同 delta 同现时 thinking 被丢（Task 7/8 顺带改双 if）；③tests/llm 缺 conftest 统一环境（SECRET_KEY/代理），后续补）

- [x] **Task 4 完成**（commit 69a956f，review CLEAN——content_end 落库兜底设计成立（SSE 重连/回放正文不丢）、聚合缓冲拆分修复真实 bug（token 交替互相饿死，代码推演证实）、无 kind 兼容路径逐字一致、变异验证通过；Minor 记账：①**Task 5 前端 index.tsx:505-513 历史回放 switch 需补 content_end case**（否则回放出现"正文输出完成"噪声行）；②THINKING_TOKEN_COALESCE_* 常量命名待 Task 5 顺手改 TOKEN_COALESCE_*；③tests conftest 统一环境问题延续）

### Task 3: 适配器 chunk 分流（kind 字段）
- Files: `backend/app/services/llm/adapters/litellm_adapter.py`（:508-522 拆 or 链，yield 带 kind）
- Interfaces: chunk 增加 kind: "reasoning"|"content"；accumulated 拆分（正文累计不含思考）
- TDD: 失败测试（混合 delta → 两种 kind 独立 yield；done.content 仅含正文）→ 实现 → 通过 → commit

### Task 4: Agent 循环分流 + 正文事件
- Files: `backend/app/services/agent/agents/base.py`（stream_llm_call 按 kind 分流；emit_content_token 新增）
- Interfaces: kind=reasoning → thinking_token（现有前端思考区）；kind=content → content_token；无 kind（旧后端）→ 现状不变
- TDD: 失败测试（混合 chunk → 思考/正文事件分离；done 累计正文不含思考噪声）→ 实现 → 通过 → commit

### Task 5: 前端分流渲染
- [x] **Task 5 完成**（commit 6f1b8e0，review CLEAN——双路径正文成形推演成立（实时流式+回放兜底）、去重正确性推演、旧后端兼容逐字未动、Task 4 两个承接项落实；3 Minor 记账：①agentStream.ts 的 AgentStreamHandler 类是既有死代码（新增 case 无消费者，后续前端基建时清理）；②回放思考日志无 cleanThinkingContent 清洗（既有行为非本次引入）；③frontend/pnpm-workspace.yaml 为 pnpm10 副产物——主控裁决进 .gitignore）
- Files: `frontend/src/shared/api/agentStream.ts`（kind 字段 + content_token 类型）、`frontend/src/pages/AgentAudit/hooks/useResilientStream.ts`（:274-281 分流）、`frontend/src/pages/AgentAudit/index.tsx`（:667-690 正文流式日志）
- Interfaces: content token → 正文流式日志；reasoning token → 现有"思考"区；tool_calls 事件 → 工具逻辑（现状保留）
- TDD: 组件/hook 测试（分流断言 + 旧格式兼容）→ 实现 → 通过 → commit

## Phase 3: 能力探测与协议选择

### Task 6: 多后端能力探测层
- [x] **Task 6 完成**（commit ac73408，review CLEAN——三 Scenario 三重证据（代码+测试+真实 SGLang 亲探 1098ms）+ guided 强制力对照实验（纯文本要求下服务端仍强制 schema）+ 死后端亲测全 False 不抛出；实施者真实发现：thinking 模型 reasoning 前缀耗尽小探测预算致 guided 假阴性，预算调 tools=256/guided=512 并测试锁死；5 Minor 记账：①**Task 7 顺手修探测 AsyncOpenAI 加 max_retries=0**（死后端 15-30s 延迟）；②**Task 7 顺手补 caps 写回 llm_service 实例属性**（否则 Task 7/8 读 service.backend_capabilities 得 None）；③跨任务并发首探竞态（后果仅重复请求，后续 task 字典去重）；④极端长 reasoning 模型假阴性降级（设计内零破坏）；⑤LLM_REPETITION_PENALTY 提前落库（Task 9 消费））
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
