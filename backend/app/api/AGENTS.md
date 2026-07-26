# API 层 — FastAPI 端点与路由

19 文件、~8,775 行、13 个端点模块。deps/中间件/端点三层结构，路由聚合 → 依赖注入 → 端点处理。

## 路由聚合链

```
main.py                  → app.include_router(api_router, prefix="/api/v1")
  v1/api.py              → api_router = APIRouter() + 13× include_router()
    endpoints/auth.py        /auth         登录/注册/验证码/登出
    endpoints/users.py       /users        用户 CRUD + RBAC 过滤
    endpoints/projects.py    /projects     项目管理 + ZIP + 文件树
    endpoints/members.py     /projects     ⚠️ 与 projects.py 共享前缀（嵌套资源）
    endpoints/tasks.py       /tasks        传统审计任务
    endpoints/scan.py        /scan         即时代码扫描
    endpoints/config.py      /config       LLM 运行时配置 + 沙箱管理
    endpoints/database.py    /database     数据库连接测试
    endpoints/prompts.py     /prompts      提示词模板（中英双语）
    endpoints/rules.py       /rules        审计规则集 CRUD + 导入导出
    endpoints/agent-tasks    /agent-tasks  ⭐ SSE 流式 + Agent 审计（3441 行）
    endpoints/embedding      /embedding    Embedding 模型配置
    endpoints/ssh-keys       /ssh-keys     SSH 密钥管理
```

## 目录结构

```
api/
├── __init__.py              # 空（0 行）— 仅标记包边界
├── deps.py                  # 依赖注入中心（50 行）
├── middleware.py            # AppNameMiddleware（13 行）
└── v1/
    ├── __init__.py          # 空（0 行）
    ├── api.py               # 路由聚合器（17 行）
    └── endpoints/
        ├── __init__.py      # 空（0 行）
        ├── agent_tasks.py   # ⚠️ 3441 行 — 39% 代码集中在此文件
        ├── rules.py         # 705 行
        ├── projects.py      # 686 行
        ├── database.py      # 672 行
        ├── config.py        # 620 行（含 sandbox 管理 API）
        ├── scan.py          # 547 行
        ├── prompts.py       # 415 行
        ├── users.py         # 338 行
        ├── embedding_config.py  # 307 行
        ├── tasks.py         # 286 行
        ├── auth.py          # 283 行
        ├── ssh_keys.py      # 206 行
        └── members.py       # 189 行（与 projects 共享前缀）
```

## 认证与权限 — 双模式（⚠️ 不一致风险）

### 模式 A（主流）：内联检查（100+ 处，所有 13 个端点使用）
```python
current_user: User = Depends(deps.get_current_user)
# 函数体内：
if not has_permission(current_user, Permission.XXX):
    raise HTTPException(status_code=403)
```

### 模式 B（少数）：声明式依赖（仅 5 个文件导入 rbac）
```python
Depends(require_permission(Permission.XXX))
Depends(require_role([Role.XXX]))
```

### RBAC 引擎（`core/rbac.py`，246 行，不在 api/ 目录）
- `Permission` 常量（19 个权限）+ `ROLE_PERMISSIONS` 矩阵（3 角色）
- 声明式 deps：`require_permission()`、`require_role()`
- **数据范围过滤**（行级隔离）：`build_user_filter`、`build_project_filter`、`build_task_filter`、`build_prompt_filter`、`build_agent_task_filter`
  - super_admin → 全部数据
  - admin → 自己 + 下级
  - user → 仅自己
- 便利 deps：`require_super_admin`、`require_admin_or_above`、`require_any_role`

### ⚠️ 死代码
`deps.get_current_active_superuser`（deps.py:43）— 定义了但在端点中 **0 次** 使用 `Depends(get_current_active_superuser)`。

## 共享约定

### 分页（未封装为共享依赖，6 个文件重复声明）
```python
skip: int = Query(0, ge=0)
limit: int = Query(20, ge=1, le=100)
# 返回格式: {"items": [...], "total": N}
```

### 错误处理（212 处 HTTPException，全部内联 raise）
无自定义异常类或全局异常处理器。

### 响应格式（混合）
- 类型化：`response_model=Schema`（少数）
- 非类型化：`-> Any` 返回 ad-hoc dict（主流模式）

## SSE 流式（仅 agent_tasks.py）

`/agent-tasks/{task_id}/events` 和 `/stream` 使用 `StreamingResponse`。
其他 12 个端点均不涉及流式。

## agent_tasks.py — 超大文件（3,441 行）

包含 22 个路由处理器：任务 CRUD、SSE 流式、聊天、Agent 树、检查点、findings、报告导出。
建议拆分为子模块包（`agent_tasks/lifecycle.py`、`streaming.py`、`findings.py`、`chat.py`）。

## 反模式

- 禁止跳过 `db` 依赖回滚（异常路径必须调用 `db.rollback()`）
- 新端点必须在 `v1/api.py` 注册，否则路由不可达
- 敏感操作必须检查权限（不可仅依赖 `get_current_user`）
- `member.py` 的 `/projects` 前缀共享是特殊约定 — 新嵌套资源应遵循此模式

## 新增端点 Checklist

1. 在 `endpoints/` 创建模块
2. 实现路由处理器（`APIRouter`）
3. 在 `v1/api.py` 注册 `include_router`（含 prefix + tags）
4. 如需权限：选择模式 A（内联检查）或模式 B（声明式 require_permission）
5. 如需数据范围过滤：调用 rbac 的 `build_*_filter`
