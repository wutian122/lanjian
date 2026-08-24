# agent-audit-init-progress

## Purpose

The agent-audit-init-progress capability documents the published behavior for users and maintainers.

## Requirements

### Requirement: REQ-IP-1 初始化界面必须展示索引进度
当 Agent 审计页处于初始化界面（`isInitializing`）时，前端 MUST 解析后端 `分块进度`/`嵌入进度` 类事件并在界面实时显示当前索引阶段与进度（`分块 N/M 文件 (X%)` 或 `嵌入 N/M (X%)`）；进度事件到达 MUST 更新界面显示（不得停留在静止的启动界面）。

#### Scenario: 索引进度实时显示
- **WHEN** 任务处于 initializing 阶段，后端持续发送 `📝 分块进度: 140/4205 文件 (3%)` 事件
- **THEN** InitProgress 界面显示"分块 140/4205 文件 (3%)"，且随事件推进更新，界面不再静止

### Requirement: REQ-IP-2 初始化界面必须有轮询兜底
当初始化界面在无 `phase_start`/`init_step` 事件推进超过 30 秒时，前端 MUST 周期性调用 `loadTask` 检查任务状态；`status` 变为 running 时 MUST 自动切换至主界面（无需用户手动刷新）。

#### Scenario: 无事件推进时自动切换
- **WHEN** 初始化界面持续 30 秒无任何推进事件且任务 `status` 已变更为 running
- **THEN** 前端轮询 `loadTask` 后自动从初始化界面切换至主界面

### Requirement: REQ-IP-3 构建与回归
前端修改 MUST 通过 `pnpm build` 与 `pnpm lint`；打开运行中任务页 MUST 正常渲染（初始化界面显示进度或直接进入主界面），无控制台错误与既有功能回归。

#### Scenario: 构建与页面验证
- **WHEN** 修改完成后运行 `pnpm build` + `pnpm lint`，并在浏览器打开运行中 Agent 审计任务页
- **THEN** 构建与 lint 通过；页面正常渲染，无回归
