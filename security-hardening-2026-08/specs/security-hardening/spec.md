# 规格：security-hardening（2026-08 安全加固）

## ADDED Requirements

### Requirement: /config/defaults 需认证且敏感字段脱敏
- 系统 SHALL 拒绝未认证请求 `GET /api/v1/config/defaults`（401），且路由依赖树中 SHALL 包含 get_current_user。
- 认证后敏感字段（LLM 密钥、Git 令牌）一律返回空串 + `{field}Set` 布尔标记，非敏感默认值保留。

#### Scenario: 未认证访问被拒绝
- **When** 未携带 Bearer token 请求 `/api/v1/config/defaults`
- **Then** 返回 401
- **And** 路由的依赖树中包含 get_current_user

#### Scenario: 认证后敏感字段脱敏
- **When** 已认证用户请求 `/api/v1/config/defaults`
- **Then** llmApiKey 与全部平台密钥、github/gitlabToken 均为空串且带 `*Set` 标记
- **And** llmProvider / llmBaseUrl 等非敏感默认值正常返回

### Requirement: 刷新令牌不可当访问令牌且登出立即失效
- 系统 SHALL 仅接受 `type=access` 令牌作为访问令牌；access token SHALL 携带 `jti`。
- access token 带 `jti`；登出将 access/refresh 的 jti 一并拉黑；refresh 端点校验黑名单。

#### Scenario: 刷新令牌被拒绝作为访问令牌
- **When** 用 refresh token 调用受保护端点
- **Then** 返回 401

#### Scenario: 登出后令牌立即失效
- **When** 登出（携带 access 与 refresh token）
- **Then** 两个 jti 均写入 `logout:blacklist:*`
- **And** 之后用该 refresh 调 refresh 端点返回 401、用该 access 调受保护端点返回 401

### Requirement: 项目成员列表需项目访问权限
- 系统 SHALL 在返回项目成员列表前执行 `assert_can_access_project` 访问控制。

#### Scenario: 非成员被拒绝
- **When** 非项目成员请求成员列表
- **Then** 返回 404（不泄露资源存在性）

#### Scenario: 项目成员/创建者可读
- **When** 项目 owner 请求成员列表
- **Then** 正常返回成员数据

### Requirement: 系统规则集仅 super_admin 可修改
- 系统 SHALL 仅允许 super_admin 修改系统规则集的启用状态或切换其规则，其余角色 SHALL 收到 403。

#### Scenario: 普通用户被拒绝
- **When** user/admin 角色修改系统规则集启用状态或切换系统规则
- **Then** 返回 403

#### Scenario: 超管可操作
- **When** super_admin 切换系统规则
- **Then** 正常翻转启用状态

### Requirement: Agent 任务现场选择的新 ZIP 先上传再创建
- 系统 SHALL 在用户现场选择新 ZIP 时，先上传 ZIP 再创建 Agent 审计任务。

#### Scenario: 新 ZIP 先上传
- **When** 用户选择新 ZIP 并点击创建 Agent 审计
- **Then** 先调用 `POST /projects/{id}/zip` 上传，成功后调用 createAgentTask

### Requirement: 仓库扫描透传规则集与提示词模板
- 仓库扫描请求 SHALL 携带 rule_set_id / prompt_template_id，并 SHALL 注入 scan_repo_task 的 user_config['scan_config']。

#### Scenario: 扫描请求携带规则配置
- **When** 提交带 rule_set_id/prompt_template_id 的仓库扫描请求
- **Then** ScanRequest 接受该字段
- **And** 注入 scan_repo_task 的 user_config['scan_config'] 含这两个字段

### Requirement: ZIP 快扫新上传分支保留文件范围
- 新上传 ZIP 分支 SHALL 传递 filePaths；切换上传模式时 SHALL 清空已选文件范围。

#### Scenario: 新上传分支带文件范围
- **When** 新上传 ZIP 且用户已选文件范围
- **Then** scanZipFile 请求携带 filePaths

### Requirement: finding 级 PoC 重跑端点
- 系统 SHALL 提供 finding 级 PoC 重跑端点（POST /agent-tasks/{task_id}/findings/{finding_id}/reverify），直接沙箱重放 PoC 并更新验证状态。

#### Scenario: 重跑成功
- **When** 对带 PoC 的 finding 发起 reverify 且沙箱执行成功
- **Then** verification_status 置 confirmed、is_verified=True、sandbox_attempts 增加一条

#### Scenario: 重跑未复现
- **When** 沙箱执行失败
- **Then** verification_status 置 not_reproducible

#### Scenario: 无 PoC 拒绝
- **When** finding 无 poc_code
- **Then** 返回 400

### Requirement: Agent 注册表按任务隔离
- 系统 SHALL 按任务隔离 Agent 注册表作用域，并发任务的启动/取消/树展示互不影响；finish_tool 根校验 SHALL 接受任务绑定根。

#### Scenario: 清任务 A 不影响任务 B
- **When** 注册两个任务的树后 clear_task(task-a)
- **Then** 任务 A 节点全部移除、任务 B 节点保留

#### Scenario: 树与统计按任务隔离
- **When** 查询任务 A 的树/统计
- **Then** 只含任务 A 的节点

#### Scenario: finish 校验接受任务根
- **When** 任务 B（非全局根）的 finish 工具做根校验
- **Then** 校验通过（不再被全局根误拒）

### Requirement: LLM 连通性测试防 SSRF
- 系统 SHALL 校验用户提供的 LLM baseUrl（仅 http/https、拒绝回环/内网/保留地址），失败响应 SHALL 不含 traceback 全文。

#### Scenario: 拒绝内网地址
- **When** baseUrl 为 127.0.0.1/192.168.x/10.x/容器名
- **Then** 返回 400

#### Scenario: 放行公网地址
- **When** baseUrl 为公网 https 地址或 allowlist 中的内网代理
- **Then** 校验通过

#### Scenario: 错误响应不含 traceback
- **When** LLM 测试失败
- **Then** 响应 debug 中不含 traceback 键

### Requirement: 前端用户管理与后端 RBAC 对齐
- 前端 SHALL 允许 admin 角色访问用户管理，数据范围由后端下辖过滤保证。

#### Scenario: admin 可见用户管理
- **When** admin 角色打开管理页
- **Then** 用户管理 Tab 可用（后端按 parent_admin_id 过滤下辖用户）
