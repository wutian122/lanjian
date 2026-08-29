# 蓝鉴 E2E 端到端测试报告（2026-08-28）

- 测试日期：2026-08-28（北京时间）
- 测试目标：服务器 A `10.129.7.87`（arm64，SSH 62222），前端 `http://10.129.7.87/`，backend `:8000`
- 测试凭证：admin / qxj@2026
- 测试项目：nginx（真实开源源码 `nginx-release-1.31.4.zip`）、tomact（真实 `tomcat-10.1.59.zip`，项目名拼写为"tomact"）、e2e-vuln-sample（**合成漏洞样本**，本测试新建）
- 版本：backend/frontend **v6.2.4**，sandbox **v6.1.0**
- LLM 端点：`10.129.2.101:8001`（Qwen3.8-27B，测试全程可达）

## 测试前自检（服务器 A，只读）

| 检查项 | 结果 |
|---|---|
| 容器状态 | frontend Up / backend Up / **db healthy** / **redis healthy** |
| /tmp（16G tmpfs） | 261M（2%），`/tmp/lanjian` **0 个历史目录** |
| backend health | `{"status":"ok"}` HTTP=200（端点 `/health`） |
| LLM 端点 | 10.129.2.101:8001/v1/models HTTP=200 |

自检全部通过，符合测试前置条件。

---

## 第 1 层：后端 API（A 机主测 + B 机对比）

| # | 用例 | 结果 | 证据 |
|---|---|---|---|
| 1.1 | 健康检查 | **PASS** | `/api/v1/health` 返回 404（路径写错）；实际健康端点为 `/health` → 200 `{"status":"ok"}`。**提示词路径需修正**（🟢） |
| 1.2 | 登录 | **PASS** | `POST /api/v1/auth/login` → 200，access_token+refresh_token，role=super_admin，`is_first_login:false` |
| 1.3 | 登录跳转 | **PASS** | 浏览器未登录访问 `/` → 跳 `/login`；登录后**固定跳 `/dashboard`**（REQ-LR-1），未回 state.from |
| 1.4 | 项目列表 | **PASS** | `GET /api/v1/projects/`（**尾斜杠**）→ 200 + 列表（9 原始 + 新建 e2e-vuln-sample）；无尾斜杠 307（FastAPI 标准，前端 apiClient 带斜杠）。**RBAC 行级隔离实测**：新建 e2e-test-user（未授权任何项目）登录后 `GET /projects/` → **可见 0 项目**（admin 的项目被正确隔离） |
| 1.5 | 创建 Agent 任务 | **PASS** | `POST /api/v1/agent-tasks/`（尾斜杠）→ 200，任务 `07ec07e0`（nginx）pending；DB pending→running |
| 1.6 | 任务查询 | **PASS** | 状态机字段齐全（status/current_phase/resume_count/verification_status_breakdown/orchestrator_alive）；时间戳已按北京时间 +08:00（timeutil 生效） |
| 1.7 | findings 查询 | **PASS** | `GET /agent-tasks/16e44579/findings` → 200 + 5 条，含 sandbox_attempts 铁证 |
| 1.8 | reverify | **PASS（端点）/ 🟠（结果缺陷）** | 200 + ZIP 自动重解压（目录已清理时重建 src）；但 poc_code 存 URL 路径导致 PoC 重跑 exit=2 → confirmed 被误降 **not_reproducible**（详见问题 #2） |
| 1.9 | 任务删除 | **PASS** | 200 + `deleted:true, cleanedEvents=1716, cleanedFindings=5, cleanedCheckpoints=1, cleanedTreeNodes=4, cleanedFiles=[任务目录], cleanedIndexes=[RAG索引]`；DB 级联归零；/tmp 目录删除 |
| 1.10 | 暂停/恢复 | **PASS** | pause→200 checkpoint_id、status=paused；resume→200、resume_count+1、任务目录重建重新解压续跑（total_iterations 归零=阶段重跑，符合"重新下载源码续跑"）；nginx 任务同验证 |
| 1.11 | 版本一致性 | **PASS** | A/B 双机 backend/frontend 均 `wutian449/lanjian-*:v6.2.4`；sandbox 镜像 `v6.1.0` |

**B 机（192.168.238.11）对比**：health 200、login 200（admin/123456789）、镜像 v6.2.4 一致。

---

## 第 2 层：前端页面（浏览器自动化，IAB）

| # | 用例 | 结果 | 证据 |
|---|---|---|---|
| 2.1 | 页面版本号 | **PASS** | 登录页页脚与顶栏均显示 **v6.2.4** |
| 2.2 | 登录页 | **PASS** | 错误密码 → toast"用户名或密码错误"，无 500，停留登录页 |
| 2.3 | 仪表盘 | **PASS** | 统计（10 项目/18 任务/139 问题/均分 78.0）、图表标题（质量趋势/问题类型分布）、最近项目（含新建 e2e-vuln-sample）、最近任务渲染正常 |
| 2.4 | 项目页 | **PASS** | 列表 10 项目 + 统计；详情页 tab（概览/审计任务/问题管理/设置）+ 启动审计按钮。**注**：文件树 UI 前端未实际接线（`getProjectFiles` 定义但无页面使用）；`GET /projects/{id}/files` API 正常（返回 3 文件，**`.jsp` 被 TEXT_EXTENSIONS 过滤**，🟢） |
| 2.5 | SSE 实时日志 | **PASS** | nginx/vuln-sample 任务页"已连接"、日志实时滚动（进度/工具/思考/调度全可见）；RAG 分块/嵌入进度文案（CHUNK_PROGRESS/EMBED_PROGRESS）在位；Semgrep 预扫描、Orchestrator 决策、Recon 工具链全部可见；状态徽标随事件变化 |
| 2.6 | SSE 弹性流 | **PASS（代码级）** | `useResilientStream.ts`：`maxReconnectAttempts:5`、心跳 45s、重连携带 `Last-Event-ID`（latestSeenSequenceRef 高水位不清零）+ `after_sequence` 参数；实测页面 reload 后重连并完整续拉历史（"Connected to audit stream"） |
| 2.7 | sandbox 事件可见 | **PASS** | 页面日志渲染 `sandbox_exec 工具调用`、`沙箱执行完成 exit=0`、`确定性沙箱执行完成: N 条预生成 PoC 已运行`、`沙箱执行结果 exit=-1/1/2/0`（REQ-VP-2 生效） |
| 2.8 | findings 页四档状态 | **PASS** | 完成后页面显示 "Findings (5)" 列表 + 统计条 **"已验证 0 / 不可复现 1 / 待确认 4 / 误报 0"** + 严重程度分布；FindingDetailPanel 标签映射（已验证绿/静态确认蓝/不可复现琥珀/待确认黄/误报灰）。注：已验证 0 因 reverify 误降级（问题 #2） |
| 2.9 | 报告导出 | **PASS** | 导出对话框实时预览 Markdown 报告（报告信息/执行摘要/漏洞概览/审计指标）；API `GET /agent-tasks/{id}/report?format=markdown|json` → 200（格式值须为 `markdown`/`json`，`md` 会 422） |
| 2.10 | 用户管理 | **PASS** | UI 建用户（"用户创建成功"）、禁用（"操作成功"、状态变禁用）；改角色走 API（`PUT /users/{id}` role→admin 成功、toggle-status 重新启用成功）。**注**：UI 对已存在用户无改角色入口（仅创建时可选），🟢 |

---

## 第 3 层：沙箱执行

| # | 用例 | 结果 | 证据 |
|---|---|---|---|
| 3.1 | 动态起容器 | **PASS** | `docker events` 捕获 11 次容器创建，镜像全部 `wutian449/lanjian-sandbox:v6.1.0`（随机名 hungry_chaum 等） |
| 3.2 | 安全配置 | **PASS** | `ro=true`（只读根fs）、`net=none`（断网）、`cap_drop=[ALL]`、`security_opt=no-new-privileges:true`、mem=512MB、tmpfs /home/sandbox(512m)+/tmp(256m) |
| 3.3/3.4 | 挂载链路 | **PASS（🟡 差异）** | 两种模式：①确定性前置执行路径：`/tmp/lanjian/<task>/src → /workspace/src`（**rw=true**）；②模板 PoC 路径：`src→/workspace/src (rw=FALSE)` + 临时目录→`/workspace/poc (rw=true)`。**源码只读加固未覆盖全部路径**（问题 #3） |
| 3.5 | PoC 执行回传 | **PASS** | sandbox_result 事件带 exit code（exit=0/1/2/-1）；沙箱冒烟 `hello world` exit=0 验证沙箱本身可用 |
| 3.6 | 超时行为 | **BLOCK** | 未构造 sleep 长 PoC（审计引擎模板未触发）；沙箱层超时逻辑在 `sandbox_tool.py`（`asyncio.wait_for` + 超时 kill），未实测 60s 超时 |
| 3.7 | 任务终态清理 | **PASS** | vuln-sample 完成后 `/tmp/lanjian/16e44579...` 被删除（v6.2.4 REQ-CLEAN-1 生效）；`/tmp` 保持 2% |

---

## 第 4 层：网页端到端旅程

```
登录 ✓ → 上传 ZIP（e2e-vuln-sample，API multipart）✓ → 新建 Agent 审计任务 ✓
→ SSE 实时观看（日志/进度/sandbox 事件）✓ → 任务完成（completed_with_gaps）✓
→ 查看 findings（5 条 + 四档状态）✓ → reverify（端点 200，结果缺陷见 #2）✓
→ 导出报告（markdown/json）✓ → 删除任务（级联清理）✓
```

关键断言：
- 页面任务状态 = API/DB status（审计任务页"1 运行中"与 API 一致）✓
- 审计过程 SSE 持续有事件（有 EventQueue 丢弃告警，见问题 #1）⚠️
- 任务终态有完整结束点（completed_with_gaps 横幅 + 报告生成）✓
- reverify 在目录已清理后可用（ZIP 自动重解压）✓（但结果误降级）

---

## 第 5 层：代码审计全流程（e2e-vuln-sample 合成样本，任务 16e44579）

样本：app.py（SQLi/XSS/命令注入/路径穿越/SSRF）、vuln.js（命令注入/SQLi）、VulnServlet.java（反序列化/XSS）、vuln.jsp（SQLi/硬编码凭据）。

| 阶段 | 断言 | 结果 |
|---|---|---|
| 导入解压 | `/tmp/lanjian/<id>/src` 完整解压（4 文件）；RAG 分块/嵌入进度消息出现 | **PASS** |
| 静态扫描 | Semgrep 预扫描（security-audit/owasp-top-ten/secrets/xss/sql-injection）进入 Recon 上下文；owasp-top-ten 命中 9-10 条 | **PASS** |
| Multi-Agent | Orchestrator→Recon/Analysis/Verification 阶段事件齐全；phase_start/phase_complete/dispatch/dispatch_complete 系列在 DB 可见；**27 种事件类型中本任务出现 20+ 种** | **PASS** |
| 发现落库 | **15 个 finding_new 声明（含 Java 反序列化/XSS、vuln.js 命令注入/SQLi、vuln.jsp SQLi）→ 仅 5 条落库且全部为 app.py**；理论风险 finding（缺 file_path 但 confidence≥0.7）保留逻辑在（4 条 needs_context 保留） | **⚠️ 部分**（多文件 findings 未落库，问题 #4） |
| 沙箱验证 | 54 次 sandbox_exec/66 次 sandbox_result；SQLi finding 绑定 7 次尝试（末次 exit=0 VULNERABILITY_CONFIRMED）；**其余 4 条 0 次尝试** | **⚠️**（验证覆盖不足，问题 #5） |
| 验证状态 | SQLi=confirmed（is_verified=True，确定性模板 PoC 铁证）；command_injection/xss/path_traversal/ssrf=needs_context（**无证据不虚报**，Bug D 判定正确）；无 Simulated/伪造标记 | **PASS**（判定诚实） |
| 覆盖率门禁 | 多轮调度后"重复调度警告/覆盖率门禁自动放行"；R4 门禁机制在位 | **PASS**（机制在位，但验证覆盖不完整） |
| 任务收尾 | completed_with_gaps；observations 落库；任务目录已清理；db/redis healthy | **PASS** |

**关键结论**：合成样本的 SQLi 被**确定性模板 PoC 实证确认**（exit=0 + VULNERABILITY_CONFIRMED 铁证，证据绑定 finding_id）；其余 4 类漏洞因验证阶段未产生 PoC 证据被如实判为 needs_context（不虚报 confirmed）。但**验证覆盖不完整**（4/5 无沙箱尝试）导致 completed_with_gaps。

---

## 重点回归清单

| # | 回归项 | 结果 |
|---|---|---|
| 1 | tmpfs 清理（连续 ≥2 任务） | **PASS**：vuln-sample + nginx **连续 2 个任务**完成后任务目录均被 finally 清理（v6.2.4 REQ-CLEAN-1/2/3），`/tmp/lanjian` 归零、/tmp 全程保持 2%（261M）、db/redis 全程 healthy |
| 2 | reverify 兼容（ZIP 自动重解压 / 仓库 409） | **PASS（ZIP 部分）**：目录已清理时 reverify ZIP 项目自动重建 src 不报"Project dir not found"；仓库型 409 未实测（本次无仓库型 agent 任务） |
| 3 | SSE 断线横幅（RAG 长阶段不误报） | **BLOCK**：本次 RAG 阶段均 <1min（小项目），未触发 >10min 长 RAG 场景；代码级 heartbeat 45s + early heartbeat 兜底机制在位 |
| 4 | 验证状态误判（Java 反序列化/nginx 配置） | **⚠️ 部分**：VulnServlet.java 反序列化在验证日志中被 java_test 实证（VULNERABILITY_CONFIRMED），但**最终未落库**（问题 #4）；nginx 真实源码审计诚实（path_traversal=not_reproducible、ssrf=needs_context，**无虚报 confirmed**） |
| 5 | 部署重启恢复（backend 重启 stale 处理） | **PASS**：backend 容器重启后 health 恢复 200；原 running 任务 `orchestrator_alive=False`（Redis 存活判定正确识别死编排器）；`POST /{id}/recover` → "task recovered to paused state, can resume"（stale→paused 可续跑，**不会永久 running**）；前端有 stale 检测（`running && orchestrator_alive===false`）与"任务可能已断开，点击可继续执行"恢复横幅。注：无自动 stale 扫描，需手动 recover |

---

## 问题清单

### 问题 #1：EventQueue 队列饱和，事件批量丢弃 + 任务严重变慢
- 层级：后端（审计引擎）
- 严重级：🟠 严重
- 现象：backend 日志大量 `[EventQueue] Dropped NNNN thinking_token events ... (queue full, size=10000/10000)` 与 `[EventQueue] Timed out (5s) waiting to enqueue ... event dropped from queue but persisted in DB`；任务 analysis 阶段被 5s 入队超时拖慢（4 文件项目耗时 ~50 分钟）。**单任务运行时同样出现**（nginx 暂停后 vuln-sample 单独运行仍 102 条/3 分钟）。
- 复现：并发或单任务运行 Agent 审计，LLM 流式输出长文本时。
- 预期：事件不应因消费者慢而被丢弃；重要事件不丢。
- 证据：`docker logs lanjian-backend-1`（event_manager.py:345/379 告警）；详情见深度分析。

### 问题 #2：reverify 用 poc_code 直接执行，但 poc_code 存了 URL 路径 → confirmed 被误降 not_reproducible
- 层级：后端（reverify 端点）
- 严重级：🟠 严重
- 现象：SQLi finding（审计中 confirmed，确定性 PoC exit=0 铁证）reverify 后返回 `{"success":false,"verification_status":"not_reproducible","exit_code":2}`，DB 中 poc_code=`/login?username=admin' --&password=anything`（**URL 路径而非可执行 Python**）。`execute_poc` 把 poc_code base64 写入 `__poc.py` 用 python3 跑 → SyntaxError exit=2。前端 findings 统计从"已验证 1"变为"不可复现 1"。
- 复现：对带 PoC 的 HTTP 型 finding 点 reverify。
- 预期：reverify 应重跑审计时实际成功的 PoC 脚本并保持 confirmed。
- 证据：DB `agent_findings.poc_code`；`reverify_finding`（agent_tasks.py:2822）+ `execute_poc`（sandbox_tool.py:494）；详见深度分析。

### 问题 #3：沙箱源码只读加固未覆盖确定性前置执行路径
- 层级：沙箱
- 严重级：🟡 一般
- 现象：docker events 捕获两种挂载：模板 PoC 路径 `src→/workspace/src (rw=false)+poc(rw=true)`（符合设计）；确定性前置执行路径 `src→/workspace/src (rw=true)`（**源码可写**）。
- 证据：docker inspect 输出（见 3.3/3.4）。

### 问题 #4：多文件 findings 未落库（15 声明→仅 5 条 app.py 落库）
- 层级：审计引擎（merge-back）
- 严重级：🟡 一般
- 现象：15 个 finding_new 事件（含 VulnServlet.java 反序列化/XSS、vuln.js 命令注入/SQLi、vuln.jsp SQLi，部分在验证日志中已被 java_test/sandbox 实证）最终仅 5 条持久化且全部为 app.py。
- 预期：已声明的跨文件 findings 应完整落库。
- 证据：DB `agent_findings` vs `agent_events` finding_new；详见深度分析。

### 问题 #5：验证覆盖不足（4/5 finding 无沙箱尝试 → completed_with_gaps）
- 层级：审计引擎（验证调度）
- 严重级：🟡 一般
- 现象：仅 SQLi 绑定 7 次 sandbox 尝试并 confirmed；command_injection/xss/path_traversal/ssrf 均 0 次尝试 → needs_context；任务 completed_with_gaps。
- 注：与 R4 门禁/LLM 输出质量（多次"格式错误需要重新输出"）相关；needs_context 判定诚实（不虚报），但覆盖不完整。

### 问题 #6：仪表盘"规则与模板"区"运行中任务"统计与事实不符
- 层级：前端（Dashboard）
- 严重级：🟡 一般
- 现象：nginx 任务运行中时仪表盘显示"运行中任务: 0"，而 /audit-tasks 页正确显示"Agent 智能审计 1 运行中"。
- 根因推断：该统计只取传统 `/tasks/`（快速扫描）维度，不含 agent-tasks。

### 优化项（🟢）
1. 测试提示词 1.1 健康端点应为 `/health`（非 `/api/v1/health`）。
2. `TEXT_EXTENSIONS` 缺 `.jsp`/`.jspx`/`.html`/`.vue` 等 → `GET /projects/{id}/files` 不含 JSP 文件。
3. 用户管理 UI 无改角色入口（仅创建时可选）。
4. 报告格式参数为 `markdown|json`，提示词/文档可明确。
5. LLM 端点（Qwen3.8-27B）在长任务中输出发散/复读（Recon 思考出现大量重复词），并多次"格式错误需要重新输出"，显著拖慢审计——建议观察模型负载或切换模型。

---

## 深度分析（🟠 及以上问题）

### 深度分析：问题 #1（EventQueue 饱和）
1. **现象复现**：双任务并发（nginx + vuln-sample）时 backend 日志出现 `queue full size=10000/10000` 丢弃告警与 `Timed out (5s) waiting to enqueue` 告警；暂停 nginx 后单任务（vuln-sample）运行仍 102 条/3 分钟。事件丢弃使 SSE 前端流出现 token 级缺口；5s 入队超时累积导致任务大幅变慢（4 文件项目 ~50 分钟）。
2. **根因定位**：
   - `event_manager.py:345` `add_event`：队列满时直接 drop（thinking_token 等流式事件无 DB 兜底）。
   - `event_manager.py:379`：等待 5s 入队超时后 drop（重要事件有 DB 持久化兜底）。
   - 触发源：LLM 流式输出 `thinking_token` 事件洪峰。Qwen3.8-27B 对同一调用输出数千 token（且存在复读性长输出），每个 token 一个事件，远超消费者（SSE 订阅 + DB 写入）处理速率。
   - 队列容量 10000 固定，无背压/合并/丢弃策略分级。
3. **影响范围**：审计任务耗时显著放大（本项目直接观察）；SSE 实时流 token 级内容缺失；多任务并发时相互拖累。任务完成本身不受阻（关键事件 DB 兜底）。
4. **严重分级理由**：🟠 严重——不阻塞任务完成，但实时流体验降级 + 任务耗时倍增，且在**单任务**下即可复现，属引擎级鲁棒性缺陷。
5. **修复方案（≥2 套）**：
   - 方案 A（推荐）：thinking_token 流式事件**合并/降频**——按固定间隔或批量（如每 200ms 或每 64 token 合并一条）发往 SSE 队列；重要事件（tool/sandbox/phase）保持逐条并 DB 兜底。改动范围：`event_manager.py` + 前端 token 渲染兼容。风险：低；工作量：小；测试：并发压测 + SSE 完整性断言。
   - 方案 B：队列扩容 + 非阻塞写（`put_nowait`）分级：thinking_token 可丢（记录计数），关键事件 `await` 兜底 DB。改动小但治标。
   - 推荐 A：既解决饱和又减少不必要的事件量，同时保留关键事件完整性。

### 深度分析：问题 #2（reverify poc_code 误降级）
1. **现象复现**：输入=对 confirmed 的 SQLi finding 调 `POST /agent-tasks/<id>/findings/<fid>/reverify`（目录已清理，触发 ZIP 重解压成功）。预期=重跑审计时成功的 PoC 脚本并维持 confirmed。实际=返回 not_reproducible、exit_code=2，前端"已验证"计数归零。
2. **根因定位**：
   - `agent_tasks.py:2822` `reverify_finding`：直接取 `finding.poc_code` 调 `execute_poc`。
   - `sandbox_tool.py:494` `execute_poc`：`base64(poc_code) → /workspace/poc/__poc.py → python3`，即把 poc_code 当 **Python 源码**执行。
   - 而 DB 中该 finding 的 `poc_code = "/login?username=admin' --&password=anything"`——是 Verification Agent 的 LLM 输出的**注入 URL payload**，不是可执行脚本。审计期间的真实 PoC（sqlite 验证脚本，exit=0 VULNERABILITY_CONFIRMED）在 `sandbox_attempts` 里，并未写入 `poc_code`。
   - 即：**poc_code 字段的语义与消费方（execute_poc）不匹配，且无写入校验**。`execute_poc` 对非 Python 内容必然 SyntaxError → exit 2。
3. **影响范围**：所有 `poc_code` 非 Python 可执行脚本的 finding（HTTP 型/URL payload 型）reverify 必失败；**已 confirmed 的 finding 被错误降级为 not_reproducible**，污染验证状态与前端统计/报告。
4. **严重分级理由**：🟠 严重——功能性错误降级验证结论，且端点可被普通用户触发（权限内），影响验证可信度。因不阻断主流程且有绕行（重跑审计），不升 🔴。
5. **修复方案（≥2 套）**：
   - 方案 A（推荐）：reverify 优先使用**最后一次成功的 sandbox_attempt 的真实命令/脚本**重放（`finding.sandbox_attempts` 中 success=true 的 exit 0 记录），poc_code 仅作展示；并在 merge-back 时把确定性 PoC 的实际脚本写入 poc_code。改动范围：`agent_tasks.py` reverify + verification merge-back；风险：低；测试：HTTP 型 finding reverify 回归。
   - 方案 B：`execute_poc` 增加内容校验/分类（识别纯 URL/非 Python 时拒绝并提示，不降级状态），或 reverify 对非 Python poc_code 返回 400"该 PoC 不可重跑"。改动小但功能仍缺失。
   - 推荐 A：恢复 reverify 的本来语义（重放真实证据），并根治 poc_code 语义错位。

---

## 测试数据清理清单（已完成）

| 数据 | 处理 |
|---|---|
| e2e-vuln-sample 项目（c99b60a1） | **已删除**（"项目已删除"，RAG 索引随任务删除已清理） |
| e2e-test-user 用户 | **已删除**（"用户已删除"，服务器恢复仅 admin） |
| vuln-sample.zip（服务器 /tmp） | **已删除** |
| nginx 任务（07ec07e0） | **已删除**（cleanedEvents=1685/cleanedFindings=2） |
| 回归 5 测试任务（98160012） | **已删除** |
| 本地 e2e_tmp/ 目录 | **已删除** |

**服务器终态核验**：容器 frontend Up / backend Up / db healthy / redis healthy；项目恢复 9 个基线（无 e2e 残留）；用户仅 admin；`/tmp` 261M（2%）、`/tmp/lanjian` 0 目录。

## 最终结论

**有条件通过**（遗留问题见问题清单 #1-#6，其中 🟠 2 项建议尽快修复）。

- 五层主体功能、版本一致性、任务生命周期（建/查/暂停/恢复/删除级联）、沙箱安全配置（ro/net=none/cap_drop ALL）、终态目录清理、报告导出、SSE 机制、RBAC 行级隔离均实证通过。
- 🟠 **问题 #1（EventQueue 饱和）**与 🟠 **问题 #2（reverify poc_code 误降级）**建议进入变更流程修复（各推荐方案 A）。
- 🟡 问题 #3-#6 列入后续优化。
- **未完成项**：①tomact 全量审计未执行（5393 文件 + LLM 吞吐瓶颈 + EventQueue 饱和，估算数小时，建议单独安排批次）；②3.6 沙箱超时、回归 3（长 RAG SSE 断线横幅）、仓库型 409 reverify 未实测（BLOCK 已注明原因）。
