# 设计：security-hardening-2026-08

## 关键设计决策

### A2 令牌生命周期（jti 黑名单）
- access token 增加 `jti`（security.py），与 refresh token 一致。
- `get_current_user` 校验 `type=access` + 查 `logout:blacklist:{jti}`（Redis 不可用时 fail-open，仅降级登出强失效）。
- 登出端点不要求登录态，将请求携带的 access/refresh jti 一并拉黑（ttl=剩余最大有效期）。
- 前端登出改为调用后端（keepalive fetch）+ 登录存储 refresh_token。

### A1 /config/defaults
- 端点加认证依赖；复用现有 `mask_config` 对敏感字段脱敏（空串 + Set 标记），双保险。

### B4 finding 级 PoC 重跑
- 不复用 re-audit 的完整编排（含 LLM，重），直接 `SandboxManager.execute_poc` 重放 finding.poc_code。
- 结果按沙箱成功与否置 `confirmed` / `not_reproducible`，记录 sandbox_attempts 与 verification_result。

### C1 Agent 注册表任务作用域
- 注册表增加 `task_id → root_agent_id` 映射；节点树天然按根可分。
- 新增 `clear_task / get_task_tree / get_task_statistics / get_agent_tree_subtree / is_bound_root / stop_task_agents`。
- 任务启动：`clear_task(task_id)` + `bind_task(task_id, orchestrator._agent_id)`；取消：`stop_task_agents(task_id)`；
  任务清理：`clear_task(task_id)`；树读取：`get_task_tree(task_id)`；finish_tool：按根取子树 + 任务根校验。

### C2 SSRF 校验
- `_validate_llm_base_url`：协议白名单 → 内部服务名黑名单 → IP 字面量（loopback/private/link-local/reserved/multicast 拒绝）
  → 域名解析后逐 IP 校验（防 DNS rebinding）；`LLM_TEST_ALLOWED_HOSTS` 放行。
- 应用于 test-llm 与 PUT /me 的 baseUrl；traceback 不回传客户端。

### 测试策略
- 后端：pytest 单元/集成（新增 3 个测试文件 37 用例）；存量 13 个失败测试按根因修复（测试侧过期/构造）。
- 前端：无单测框架，以 type-check + build + 既有静态断言脚本验证。
