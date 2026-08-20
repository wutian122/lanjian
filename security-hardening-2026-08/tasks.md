# 任务清单：security-hardening-2026-08

## 第一批：认证与越权（A1-A4）

- [x] **A1** `/config/defaults` 加认证 + 敏感字段脱敏（config.py）
- [x] **A2** 令牌类型校验 + access jti + 登出双令牌黑名单 + refresh 黑名单校验 + 前端登出调后端/存储 refresh（deps/auth/security/AuthContext/Login）
- [x] **A3** 项目成员列表补 assert_can_access_project（members.py）
- [x] **A4** 系统规则集仅 super_admin 可修改（rules.py）

## 第二批：功能正确性（B1-B4）

- [x] **B1** Agent 任务新 ZIP 先上传再创建（CreateAgentTaskDialog.tsx）
- [x] **B2** 仓库扫描透传规则集/提示词模板（database.ts + projects.py ScanRequest/注入点）
- [x] **B3** ZIP 快扫新上传分支补传 filePaths + 切换清空选择（CreateTaskDialog.tsx）
- [x] **B4** finding 级 PoC 重跑端点 + 前端「重跑 PoC」「继续审计」接真 API（agent_tasks.py + agentTasks.ts + AgentAudit/index.tsx）

## 第三批：并发/SSRF/RBAC（C1-C3）

- [x] **C1** Agent 注册表按任务隔离（registry.py / graph_controller.py / agent_tasks.py 5 处调用点）
- [x] **C2** LLM 连通性测试防 SSRF + traceback 脱敏（core.config + endpoints/config.py）
- [x] **C3** 前端用户管理对 admin 开放（AdminDashboard.tsx）

## 存量问题

- [x] **T1** 13 个失败测试修复（7 个测试文件：compose 路径 / token_budget 60M / semgrep config / finish_gates 语义 / cancel 窗口 / pause_resume name kwarg / sandbox fake）
- [x] **T2** finish_tool 树展示与根校验按任务隔离

## 验证

- [x] **V1** 新增测试：batch1 16 + batch2 5 + batch3 16 = 37 全部通过
- [x] **V2** 全量套件：528 passed / 2 skipped / 0 failed
- [x] **V3** 前端 type-check + build 通过
- [x] **V4** ruff 对改动文件自动修复存量空白/排序（剩余项为存量 B904/E712 等既有模式）
