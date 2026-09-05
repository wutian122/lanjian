# Delta Spec: thinking-stream-separation

## ADDED Requirements

### Requirement: 流式响应 SHALL 按 reasoning_content/content/tool_calls 三字段分流

后端 LLM 流式适配层 SHALL 识别推理后端返回的三个独立字段：`delta.reasoning_content`（思考流）、`delta.content`（正文）、`delta.tool_calls`（工具调用）。`reasoning_content` SHALL 以独立的思考流事件发射（与正文事件类型可区分），不得并入正文；`content` 保持现有正文事件语义；`tool_calls` 进入工具聚合路径。非流式响应 SHALL 同样区分三字段（reasoning_content 不得混入 content）。

#### Scenario: 思考流与正文在事件层分离
- **WHEN** SGLang 返回的流式 chunk 先后含 delta.reasoning_content 与 delta.content
- **THEN** 事件流出现两种可区分的事件（思考流事件/正文事件），正文内容不混入任何思考文本

#### Scenario: 思考退化的乱码不再进入正文
- **WHEN** 模型思考流发生退化（输出 stopstopSTOP/UUU...LLL...III... 类噪声）
- **THEN** 该噪声仅存在于思考流事件中，正文事件内容不受污染

### Requirement: 前端 SHALL 按字段分流渲染

前端任务详情页的事件流渲染 SHALL 区分思考流事件与正文事件：正文正常渲染；思考流 SHALL 渲染进可折叠的"思考中"区域或按配置丢弃，MUST NOT 以正文形态展示。思考流区域对用户可见时 SHALL 有明确标识（与正文视觉区分）。

#### Scenario: 界面不再把思考流当正文
- **WHEN** 任务执行中模型输出思考流与正文
- **THEN** 正文区仅显示 content 来源的事件，思考流在折叠区（或按配置不显示）

#### Scenario: 工具调用走向工具逻辑
- **WHEN** 模型响应含 tool_calls
- **THEN** 前端事件流展示工具调用卡片（现有形态），不把调用 JSON 当思考/正文渲染

### Requirement: 调用侧 SHALL 禁止任何关闭思考的参数

在推理服务端 reasoning-parser 行为修正前（正文会被吞进 reasoning_content 导致 content 恒空），蓝鉴调用侧 SHALL NOT 向端点传递任何关闭思考的参数（`enable_thinking=false`、`<|think_off|>` 提示词注入、chat_template_kwargs 控制等）。该护栏 SHALL 以请求构造层的显式拦截/断言实现（存在即拒绝并发 warning），且配置层 SHALL NOT 提供关思考开关项。

#### Scenario: 关思考参数被拦截
- **WHEN** 任何代码路径试图在请求中注入 enable_thinking=false 或 <|think_off|> 提示
- **THEN** 请求构造层拦截该参数，发射 warning 事件记录尝试，请求以思考模式发出

#### Scenario: 配置层无关思考开关
- **WHEN** 检查用户配置模型与前端设置页
- **THEN** 不存在任何"关闭思考"配置项
