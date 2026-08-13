# 蓝鉴安全加固交付文档（2026-07 P0~P3 全批次）

> 本文档汇总 2026-07-23~24 完成的 5 天安全加固计划（P0→P1→P2→P3）的所有改动、
> 部署前置条件、密钥轮换步骤、以及未修复的治理遗留项（P4 后续处理）。

---

## 1. 总览

| 批次 | 主题 | 状态 |
|---|---|---|
| P0 严重级 (4 项) | SECRET_KEY / Zip Slip / CORS / 超管默认密码 | ✅ 完成 |
| P1 Path Traversal (8 项) | resolve_safe_path 工具 + 7 个 Agent tool 文件替换 | ✅ 完成 |
| P2 高危 (8 项) | RBAC 助手 / 数据隔离 / encryption / SENSITIVE 单一源 / 后台任务 / count / tokenUrl / TTL | ✅ 完成 |
| P3 中低危 (6 项) | DB 密码 / logout / 2 处裸 except / captcha DoS / PoC 模板 | ✅ 完成 |
| **P4 治理** | 本文档 + 遗留清单 + 密钥轮换 SOP | ✅ 本文档 |

**改动统计**：39 个文件（backend + docker-compose），10 个新增测试文件。

---

## 2. 部署前置检查（必读）

任何环境（frontend-host-b.example.com / frontend-host-a.example.com）在部署本次交付前，**必须在 `backend/.env` 或宿主环境**设置以下强制变量，否则容器/后端拒绝启动：

```bash
# ---- P0-1: JWT 签名密钥（必填，≥32 位，非弱值） ----
# 生成方式（任选）：
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
#   openssl rand -hex 32
SECRET_KEY=<强密钥>

# ---- P0-3: CORS 白名单（未设置前端访问会被 CORS 拒绝） ----
CORS_ALLOWED_ORIGINS=http://frontend-host-a.example.com,http://frontend-host-b.example.com

# ---- P0-4: 超管密码（首次启动创建；已存在超管则不覆盖） ----
# 策略：≥12 位、大小写 + 数字 + 特殊字符
SUPERADMIN_PASSWORD=<强密码>

# ---- P3-1: PostgreSQL 密码（≥12 位，非 postgres/password/... 弱值） ----
POSTGRES_PASSWORD=<强密码>
```

**部署流程建议**：
1. 生成上述 4 个强值，写入 `backend/.env`（或 CI/CD secret store）
2. `docker compose -f docker-compose.prod.yml pull`
3. `docker compose -f docker-compose.prod.yml up -d`
4. 观察 backend 启动日志，确认 4 项校验都过（否则会有明确异常）
5. 首次登录用 `SUPERADMIN_PASSWORD`，进入即被强制改密（`is_first_login=True`）

---

## 3. 密钥轮换 SOP

### 3.1 SECRET_KEY 轮换（影响 JWT + 加密数据）

**背景**：`SECRET_KEY` 同时用于 JWT 签名和 Fernet 派生密钥。轮换会：
- 让所有 access/refresh token 失效 → 所有用户被踢下线（可接受）
- **让所有 `enc:v1:` 前缀的密文无法解密**（P2-3 会显式抛 `DecryptionError`，不会静默返明文）

**步骤**：
1. **停止后端**：`docker compose stop backend`
2. **导出旧密文数据**（数据库中所有 `enc:v1:` 开头字段）：
   ```sql
   SELECT id, llm_config, other_config FROM user_configs;
   ```
3. **用旧 SECRET_KEY 在离线脚本里解密** → 得到明文
4. **生成新 SECRET_KEY**：`python -c "import secrets; print(secrets.token_urlsafe(48))"`
5. **用新 SECRET_KEY 重新加密**明文 → 写回数据库
6. **更新 `.env` 里的 SECRET_KEY**
7. **启动后端**：`docker compose up -d backend`

**如果不重新加密就直接换 key，后果**：
- 所有 `enc:v1:` 密文调用 `decrypt_sensitive_data` 时抛 `DecryptionError`
- Agent 审计任务因拿不到 API Key 会报错停摆
- **P2-3 修复前是静默把损坏密文当明文送进 LLM，比现在更危险**

### 3.2 POSTGRES_PASSWORD 轮换

1. `docker exec -it lanjian-db-1 psql -U postgres -c "ALTER USER postgres WITH PASSWORD '<新密码>';"`
2. 更新 `.env` 的 `POSTGRES_PASSWORD`
3. `docker compose restart backend`

### 3.3 SUPERADMIN_PASSWORD 轮换

**不要**改 `.env`（`create_super_admin` 已改为**不覆盖**已存在密码，改 env 无效）。

正确做法：
- 从超管 UI 登录后自行改密码
- 或从数据库直接改：
  ```sql
  UPDATE users SET hashed_password = '<bcrypt hash>', is_first_login = false WHERE role = 'super_admin';
  ```

如需**重置整个超管账户**（含清除数据），设 `RESET_ALL_USERS=true` 后启动一次。

---

## 4. 攻击面对比表

| 攻击向量 | 修复前 | 修复后 |
|---|---|---|
| JWT 弱默认签名密钥 | ✅ SECRET_KEY="changethis..." → 全网可伪造 token | ❌ 启动即失败，强制注入 ≥32 位强值 |
| Zip Slip (`../etc/passwd`) | ✅ `extractall()` 跟随写入宿主 | ❌ `SafeExtractError` 拒绝 |
| Zip Bomb / 42.zip | ✅ 无上限 | ❌ 500MB 总/100MB 单/10k 条目上限 |
| CORS `*` + credentials | ✅ 任意站点携 Cookie 访问 | ❌ 白名单驱动，未配置即关 credentials |
| 超管默认密码 `123456789` | ✅ 已在默认部署被创建 | ❌ 未注入强密码不创建 |
| Path Traversal (`../../etc/passwd`) via Agent tool | ✅ 16 处 `os.path.join(root, user_input)` | ❌ `resolve_safe_path` 全部拒绝逃逸 |
| Symlink 欺骗 | ✅ 跟随 symlink 读宿主 | ❌ `resolve()` 后校验 target 位置 |
| `owner_id != user.id` 检查不一致 | ⚠️ 40+ 处散落逻辑差异 | ⚠️ 提供 `assert_can_access_project` 助手，尚未统一迁移 |
| list_agent_tasks 数据泄露/隔离 | ✅ 超管看不到别人任务；管理员看不到下辖 | ❌ 按角色 build_agent_task_filter 过滤 |
| Fernet 解密失败静默返明文 | ✅ 密文被当明文塞进 LLM | ❌ `DecryptionError` 显式抛 |
| 敏感字段散落多处定义 | ✅ giteaToken/sshPrivateKey 部分明文 | ❌ 单一真相源 `SENSITIVE_OTHER_FIELDS` |
| 后台任务异常静默丢失 | ✅ `Task exception was never retrieved` warning | ❌ `_launch_task_bg` done_callback 记 `logger.exception` |
| register O(N) 全表扫描 | ✅ `select(User).all()` 拉全表 | ❌ `SELECT COUNT(*) LIMIT 1` |
| OAuth2 tokenUrl `//` | ✅ `/api/v1//auth/login` | ❌ `rstrip('/')` |
| `_cancelled_tasks` 内存泄漏 | ✅ set() 永不清理 | ❌ TTL 24h 惰性清理 |
| PostgreSQL 弱默认密码 | ✅ `POSTGRES_PASSWORD=postgres` | ❌ 强制注入 ≥12 位，8 项黑名单 |
| logout `except: pass` 静默 | ✅ Redis 掉线登出无效仍返 200 | ❌ Redis 失败抛 503 |
| 裸 `except: pass` 吞异常 | ✅ 2 处严重位置（scan/scanner） | ❌ 显式异常类型 |
| Captcha 内存 DoS | ✅ 无上限，可无限刷 | ❌ 10k 上限 + 惰性清理 + 503 |
| PoC 模板 `except: pass` | ✅ 沙箱内吞 Ctrl+C | ❌ `except Exception: pass` |

---

## 5. 治理遗留清单（后续处理）

以下项目**未在本次 5 批次范围内**，建议后续独立立项：

### 5.1 P2-1 遗留：40+ 处单资源访问检查未统一
- 位置：`endpoints/agent_tasks.py` 20+ 处、`endpoints/projects.py` 6 处、`endpoints/scan.py` 2 处、`endpoints/members.py` 3 处
- 现状：各自写 `if project.owner_id != current_user.id`，逻辑略有差异（super_admin/is_superuser 处理不一致）
- 治理：全部迁移到 `assert_can_access_project(current_user, project)`
- 优先级：中（一致性问题，非新的攻击面）

### 5.2 P2-3 遗留：12+ 处 decrypt_sensitive_data 上游未加异常处理
- 位置：`endpoints/agent_tasks.py`、`endpoints/projects.py`、`endpoints/scan.py` 等
- 现状：调用点假设不会抛异常；迁移期数据都无 `enc:v1:` 前缀走明文分支，不受影响
- 触发条件：只有 SECRET_KEY 轮换后旧 `enc:v1:` 密文才会抛 `DecryptionError` → 请求 500
- 治理：调用处包裹 try/except，对损坏配置返回明确错误提示
- 优先级：低（有 SOP 前不会触发）

### 5.3 P3 遗留：15+ 处裸 `except: pass`
| 文件 | 行 |
|---|---|
| `endpoints/prompts.py` | 79, 136, 229, 277 |
| `endpoints/rules.py` | 89, 158, 326, 420 |
| `services/agent/knowledge/vulnerabilities/ssrf.py` | 110 |
| `services/agent/tools/external_tools.py` | 1113, 1240, 1351 |
| `services/agent/tools/sandbox_language.py` | 317 |
| `services/git_ssh_service.py` | 103 |
| `services/llm/adapters/litellm_adapter.py` | 167 |
| `services/rag/retriever.py` | 303 |
| 治理 | 全部改成显式异常类型 |

### 5.4 前端 hardcoded `allow_origins=["*"]`（教材）
- 位置：`app/services/agent/knowledge/frameworks/fastapi.py:88`
- 非运行代码（LLM 教材），不影响生产
- 治理：加注释说明是教材示例，避免后续被误抄

### 5.5 file_tool.py / pattern_tool.py 4 处 startswith 检查
- 位置：`app/services/agent/tools/file_tool.py:138/333/533`、`pattern_tool.py:353`
- 已有 `startswith(normpath(root))` 自检，理论可被 `/root_evil/x` 后缀绕过
- 治理：迁移到 P1-0 的 `resolve_safe_path`
- 优先级：中

### 5.6 静态扫描 / 依赖审计自动化
建议在 CI 加入：
- `bandit -r backend/app`（Python 安全静态分析）
- `semgrep --config=auto backend/`（模式匹配漏洞）
- `pip-audit`（依赖漏洞）
- 前端：`npm audit` / `snyk test`

### 5.7 端到端回归
建议部署完成后：
1. 用 Playwright 跑一遍 login → 创建项目 → 上传 ZIP → 发起 Agent 审计 → 查看结果全流程
2. 手工验证 4 个部署前置变量都生效
3. 手工验证 OWASP Top 10 中的 4 项已修复（用一份包含 Zip Slip / Path Traversal 载荷的测试 ZIP）

---

## 6. 新增测试用例清单

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_config_secret_key.py` | P0-1 SECRET_KEY 强制注入 + 4 类拒绝场景 |
| `tests/test_safe_extract.py` | P0-2 Zip Slip / Bomb / Symlink 10+ 类场景 |
| `tests/test_cors_config.py` | P0-3 CORS 白名单 + `*` 回归保护 |
| `tests/test_super_admin_bootstrap.py` | P0-4 超管未设置/弱密码/强密码/不覆盖已有 |
| `tests/test_path_safety.py` | P1-0 resolve_safe_path 15+ 类场景 |
| `tests/test_rbac_project_access.py` | P2-1 assert_can_access_project 9 类场景 |
| `tests/test_encryption.py` | P2-3 加密前缀 + 损坏抛异常 7 类场景 |

**运行方式**（在两台服务器容器内）：
```bash
docker exec -it lanjian-backend-1 sh -c ".venv/bin/pytest tests/ -v"
```

---

## 7. 版本与追溯

- 加固时间：2026-07-23 ~ 2026-07-24
- 修改文件数：39
- 新增测试文件：7
- 新增工具库：`safe_extract`、`resolve_safe_path`、`_launch_task_bg`、`_cancelled_tasks_add/discard/prune`、`assert_can_access_project`、`DecryptionError` / `enc:v1:` 前缀协议
- 相关 Memory 快照：`C:/Users/shen/.claude/projects/E--lanjian-main/memory/lanjian-security-hardening-progress.md`
