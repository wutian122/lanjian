# Changelog

本项目所有重要变更将记录在此文件。版本遵循 [Semantic Versioning](https://semver.org/)。

## [5.1.0] - 2026-08-13

### 安全 / 鉴权（重点）
- **#3-A token type 校验**：`get_current_user` 拒绝 refresh token 作为 Bearer 访问受保护端点（access 30min / refresh 7 天，避免 refresh 直接当 access 用的越权）。
- **#3-B 登出生效闭环**：access 增加 `jti`；`/logout` 接受 `access_token`（保留 refresh 兼容）；`get_current_user` 与 `/refresh` 均查询 Redis 黑名单；前端 `AuthContext.logout` 先调后端再清 storage。**登出后 access 立即失效**。

### RBAC 数据完整性
- **#5-C parent_admin_id 数据约束**：在 `create_user` / `update_user` 入口处，禁止 `role=admin` 用户设置上级（parent 强制 None）、校验 `parent` 只能指向 admin 或 super_admin。**从源头杜绝 admin→admin 多层链**（配合 `get_subordinate_user_ids` 单层查询，避免孙级数据对上级不可见）。

### AI / 扫描器
- **#4a frequency_penalty=1.2 → 0.0**：消除对 LiteLLM 流式路径的结构化输出毒化（避免 LLM 回避已用字段名导致 JSON key 被替换/截断/幻觉）。
- **#4c TEXT_EXTENSIONS 统一**：新建 `app/core/scan_constants.py`（29 种 frozenset），`scanner.py` / `rag/indexer.py` / `scan.py` 三处统一引用；补齐 `.html` / `.vue` / `.svelte` / `.xml` / `.css` / `.md`（前端 XSS 高发区与配置泄露面）。
- **#4b 移除 doubao 原生适配器**：doubao 协议完全 OpenAI 兼容，删除 `adapters/doubao_adapter.py`（-94 行），改走 LiteLLM volcengine 模式；`factory.py` `NATIVE_ONLY_PROVIDERS` 精简为 `{BAIDU, MINIMAX}`。

### 审计
- **#2 审计最小闭环**：新建 `GET /api/v1/audit-logs`（仅 super_admin，支持分页与 action / target_type 过滤）；补 3 类高价值安全审计写入：`login_success` / `login_failed` / `change_password`；`task_deleted` 沿用原有写入。**audit_logs 从"只写不读 + 仅覆盖删任务"升级为"可查询 + 覆盖 4 类关键操作"**。

### 工程
- **#5-A docstring 诚实化**：`get_subordinate_user_ids` 明确"单层直接下级，不递归"（与 #5-C 数据约束配套）。

### 部署
- 两台部署机（10.129.7.87 / 192.168.238.11）已同步全部 6 项修复。
- `wutian449/lanjian-{backend,frontend}:v5.1.0` 镜像已通过 `docker commit` 固化（基于已修复的容器层，arm64 多架构）。
- 旧 `v5.0.0` 镜像已删；`v5.0.0-backup` 保留 7 天作为回滚网。

## [5.0.0] - 2026-07-30

- 首次正式发布镜像（v5.0.0，多架构 amd64 + arm64）。
- 完成 P0-P3 安全加固（`docs/security-hardening-2026-07.md`）。
- 完成 SSE 实时流修复（`openspec/changes/archive/2026-07-18-fix-sse-realtime-stream`）。
- 部署到两台生产服务器（`docs/security-hardening-2026-07-DELIVERY.md`）。

[5.0.0]: https://github.com/wutian122/lanjian/releases/tag/v5.0.0
[5.1.0]: https://github.com/wutian122/lanjian/releases/tag/v5.1.0
