# Proposal: Structured Output Protocol（多后端结构化输出改造）

## Why

蓝鉴审计 0 findings 的行为根因已实锤收敛到 LLM 调用协议层：Qwen3.8-27B（thinking 模型）的响应含 `reasoning_content`（思考流）/`content`（正文）/`tool_calls`（工具调用）三个独立字段，但蓝鉴调用侧（1）前端把思考流当正文渲染——老板 tcpdump 抓包证实界面乱码与请求思考尾部字母堆砌一字不差，思考退化时（实测约 1/4 概率）出现同义词链/stopstopSTOP/UUU...LLL...III...；（2）无重复惩罚导致退化循环高发；（3）ReAct 自由文本协议（Thought:/Action:/Action Input:）对 27B 模型格式遵循负担过重——三个生产任务 Orchestrator 决策格式错误 10 连崩熔断。同时推理服务层已验证可用：SGLang 原生 function calling（`--tool-call-parser qwen3_coder`）实测 0.7s 完美返回结构化工具调用、`response_format json_schema`（guided）实测输出严格合法 JSON。老板定案：**自定义结构化输出，且不仅支持 SGLang，还要支持 vLLM、ollama 等多推理后端**。

## What Changes

- **多后端结构化输出能力层（新增）**：`structured_output.py` 死代码改造为多后端能力探测与适配层——探测 SGLang/vLLM/ollama 等推理后端对 function calling（tools）与结构化 JSON（response_format json_schema / format）的支持能力，按能力自动选择协议，不支持时降级到现有 ReAct 文本协议（零破坏）
- **原生工具调用协议接线**：Agent ReAct 循环的 LLM 调用传入 `tools` 参数（OpenAI tools 协议），模型走原生 `tool_calls` 响应；`Action` 分发逻辑兼容 tool_calls 与文本 Action 两种形态（降级共存）
- **Final Answer 结构化约束**：Analysis/Verification 的 Final Answer 轮注入 `response_format json_schema`（findings schema），从根上消灭 JSON 截断/破损/解析失败（4096 时代 0 findings 的直接死因）；后端不支持时降级为提示词约束
- **流式三字段分流（后端）**：适配器识别 `delta.reasoning_content` 并以独立事件类型（thinking 流）发射，`delta.content` 保持正文事件，`delta.tool_calls` 走工具聚合——思考流与正文在事件层分离
- **流式三字段分流（前端）**：SSE 处理按字段分流渲染——`content` 渲染正文、`reasoning_content` 进可折叠"思考中"区域或丢弃、`tool_calls` 走工具逻辑；乱码从用户视野消失
- **调用参数治理**：请求体注入 `repetition_penalty: 1.15`（可配置，1.1-1.2），用户配置 temperature 0.1→0.6（Qwen3 thinking 推荐区间，实测端点接受）；litellm extra_body 透传链打通
- **⚠️ 严禁关思考（护栏）**：代码层禁止任何 `enable_thinking=false`/`<|think_off|>` 类参数进入请求（服务端 reasoning-parser 会把正文整个吞进 reasoning_content 导致 content 恒空——老板实测确认），护栏以代码注释 + 配置项不提供 + 发射前断言实现
- **OpenSpec 台账**：本变更与 C（sandbox-verification-hard-gate）为并行独立变更，产出下限等策略改造仍归 C

## Capabilities

### New Capabilities

- `llm-structured-output`: 多推理后端（SGLang/vLLM/ollama）的能力探测、协议选择与降级规范；原生工具调用与 guided JSON 的注入语义
- `thinking-stream-separation`: reasoning_content/content/tool_calls 三字段在后端事件层与前端渲染层的分流规范；关思考护栏
- `llm-call-params`: repetition_penalty/temperature 等采样参数的配置与透传规范

### Modified Capabilities

<!-- 无既有能力的需求级变更；llm-adapter 既有 spec 的行为在本变更下细化，冲突时按 update-change 流程修订 -->
