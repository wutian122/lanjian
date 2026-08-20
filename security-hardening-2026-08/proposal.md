# 提案：security-hardening-2026-08

## Why
2026-08-19 对蓝鉴做全量深度梳理并核验外部安全审查结论后，确认 11 项认证/越权/功能缺陷（详见 specs/security-hardening/spec.md），
另有 13 个存量失败测试（全部为测试侧过期/构造问题，无生产缺陷）与 finish_tool 并发树展示问题。
所有缺陷均经代码核验；其中 P0 `/config/defaults` 未认证泄露为条件性（当前生产未配置相关环境变量，未实际泄露）。

## What Changes
- 修复 11 项已核验缺陷（A1-A4 认证与越权、B1-B4 功能正确性、C1-C3 并发/SSRF/RBAC）。
- 修复 13 个存量失败测试，恢复回归保护力；finish_tool 树展示与根校验按任务隔离。
- 按 TDD 补充回归测试；前端经 type-check/build 验证。

## 范围

- 后端（14 个文件）：config / auth / deps / members / rules / projects / agent_tasks / core.config / core.security /
  core.registry / core.graph_controller / core.__init__ / tools.finish_tool / core.config.py
- 前端（9 个文件）：AuthContext / Login / CreateAgentTaskDialog / CreateTaskDialog / AdminDashboard / AgentAudit.index /
  agentTasks / database
- 新增 3 个后端测试文件（batch1 16 用例 + batch2 5 用例 + batch3 16 用例 = 37）；修复 7 个存量测试文件（13 处）

## 非目标

- 不重做 2026-07 已完成的安全加固（P0-P4）。
- 不改数据库结构（无新增 Alembic 迁移）。
- 不做生产镜像构建与两台服务器部署（另行执行，依赖代理/现场 override）。
- 不处理其余存量技术债（uv.lock 与 pyproject 版本分叉、全仓库 6128 个 ruff 存量错误）。
