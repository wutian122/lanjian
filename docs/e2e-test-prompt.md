# 蓝鉴 E2E 端到端测试提示词（v1.0）

> 使用方式：将本文档整体作为提示词交给 AI 测试执行者，配合浏览器自动化工具
> （browser-use / chrome-cdp-automation）与远程执行能力（remote-shell）执行。
> 要求执行者严格按第五、六节规范**记录问题并做深度分析**，不得只报"通过/失败"。

---

## 一、角色与目标

你是一名资深 E2E 测试工程师，对"蓝鉴"（AI 驱动的本地化代码安全审计平台）执行
**全链路端到端测试**。你的目标：

1. 覆盖五层：**后端 API → 前端页面 → 沙箱执行 → 网页端到端 → 代码审计全流程**。
2. 每层给出 PASS/FAIL + 证据（命令输出、日志、DB 查询、截图）。
3. **每个 FAIL 必须产出深度分析报告**（见第六节），不是简单罗列。
4. 最终产出 `docs/e2e-test-report-<YYYY-MM-DD>.md`。

## 二、测试环境

| 项 | 值 |
|---|---|
| **本次测试目标** | **服务器 A：`10.129.7.87`**（arm64，SSH 62222），前端 `http://10.129.7.87/`，backend `:8000` |
| **本次测试凭证** | **admin / qxj@2026** |
| **本次测试项目** | **nginx**（ZIP 导入）与 **tomcat**（ZIP 导入）两个项目各跑一轮完整审计 |
| 服务器 B（amd64） | `192.168.238.11`，SSH 22，前端 `http://192.168.238.11/`（备用对比） |
| 当前版本 | backend/frontend **v6.2.4**，sandbox **v6.1.0** |
| LLM 端点 | `10.129.2.101:8001`（SGLang Qwen3.8-27B，与嵌入共用；测试前先探测可达性） |
| 数据库 | `docker exec lanjian-db-1 psql -U postgres -d lanjian` |

**测试前自检**（远端只读）：

```bash
# 1. 容器状态：db/redis 必须 healthy，backend/frontend Up
docker ps --format '{{.Names}} {{.Status}}' | grep -E 'backend|frontend|db|redis'
# 2. /tmp（tmpfs 16G）不得高于 50%，/tmp/lanjian 不应有历史任务目录堆积
df -h /tmp | tail -1; ls /tmp/lanjian/ | wc -l
# 3. backend health
curl -s -o /dev/null -w 'health=%{http_code}' http://localhost:8000/health
```

## 三、分层测试用例

### 第 1 层：后端 API（curl，对 A、B 各跑一遍）

| # | 用例 | 命令/操作 | 预期 |
|---|---|---|---|
| 1.1 | 健康检查 | `GET /api/v1/health` | 200 |
| 1.2 | 登录 | `POST /api/v1/auth/login`（form: username/password） | 200 + access_token |
| 1.3 | 登录后跳转行为 | 前端访问 `/` 未登录 → 登录 → 应落到 `/dashboard` | 固定跳 dashboard（REQ-LR-1），不回 state.from |
| 1.4 | 项目列表 | `GET /api/v1/projects`（Bearer） | 200 + 列表；RBAC 行级隔离（user 只见授权项目） |
| 1.5 | 创建 Agent 审计任务 | `POST /api/v1/agent-tasks/`（**注意尾斜杠**，否则 307）body=`{"project_id": "<id>"}` | 200 + task id；DB status 由 pending → running |
| 1.6 | 任务查询 | `GET /api/v1/agent-tasks/<id>` | 状态机字段齐全（status/current_phase/resume_count…） |
| 1.7 | findings 查询 | `GET /api/v1/agent-tasks/<id>/findings` | 200；理论风险 finding 允许 file_path 为空但需落库 |
| 1.8 | reverify | `POST /api/v1/agent-tasks/<id>/findings/<fid>/reverify` | 有 PoC：200；目录已清理时 ZIP 项目自动重解压（不报 Project dir not found）；仓库项目返回 409 |
| 1.9 | 任务删除 | `DELETE /api/v1/agent-tasks/<id>` | 200；DB 关联表级联清理 + `/tmp/lanjian/<id>` 目录清理 |
| 1.10 | 取消/暂停/恢复 | cancel / pause / resume 端点 | 状态机正确转换；暂停任务 resume 后重新下载源码并续跑 |
| 1.11 | 版本一致性 | 容器镜像 tag 与页面/API 版本 | backend/frontend 容器均为 v6.2.4、sandbox v6.1.0 |

### 第 2 层：前端页面（浏览器自动化，逐页）

| # | 用例 | 操作 | 预期 |
|---|---|---|---|
| 2.1 | 页面版本号 | 登录后看页脚/关于区域 | 显示 **v6.2.4**（前端版本烘焙进 bundle） |
| 2.2 | 登录页 | 错误密码 / 空表单 | 明确错误提示，不 500 |
| 2.3 | 仪表盘 | `/dashboard` 图表渲染 | 数据加载正常，无 console error |
| 2.4 | 项目页 | 列表/新建（ZIP 上传）/详情/文件树 | 功能可用，文件树正常渲染 |
| 2.5 | Agent 审计页 | 新建任务后**保持页面打开** | SSE 实时日志滚动；任务状态徽标随事件变化 |
| 2.6 | SSE 弹性流 | 断网/切后台 30s 后恢复 | Last-Event-ID + after_sequence 续传，不丢事件；重连 ≤5 次 |
| 2.7 | sandbox 事件可见 | 审计中看日志 | `sandbox_start`/`sandbox_result` 事件在页面可见（REQ-VP-2），含 PoC 命令/exit_code |
| 2.8 | findings 页 | 四档状态标签渲染 | confirmed/static_confirmed/not_reproducible/needs_context 各档颜色文案正确 |
| 2.9 | 报告导出 | 下载 markdown/json 报告 | 文件可下载且内容完整 |
| 2.10 | 用户管理 | admin 建用户/禁用/改角色 | 操作生效；RBAC 菜单可见性正确 |

### 第 3 层：沙箱执行（远端命令 + 任务日志）

| # | 用例 | 操作 | 预期 |
|---|---|---|---|
| 3.1 | 动态起容器 | 审计中 `docker ps` 观察 | backend 经 docker.sock 动态起 PoC 容器（基底 sandbox:v6.1.0） |
| 3.2 | 安全配置 | `docker inspect <poc容器>` | read_only=true、cap_drop=ALL、network=none、60s 超时 |
| 3.3 | 挂载 | 容器内 `ls /workspace/src`（源码只读） | 源码可见；`/workspace/poc` 可写 |
| 3.4 | bind mount 链路 | 宿主 `/tmp/lanjian` 与容器内一致 | 源文件一致（否则沙箱空跑——历史坑） |
| 3.5 | PoC 执行 | 观察 sandbox_result 事件 | exit_code/evidence_summary 回传；异常也不静默吞 |
| 3.6 | 超时行为 | 构造长时间 PoC（如 sleep 120） | 60s 超时 kill，返回 Timeout 而非挂死 |
| 3.7 | 任务终态清理 | 任务完成后查宿主 | `/tmp/lanjian/<task_id>` 被 finally 删除（REQ-CLEAN-1，v6.2.4 核心修复） |

### 第 4 层：网页端到端（一条完整用户旅程）

用浏览器自动化走完整旅程，全程截图留证：

```
登录 → 上传 ZIP（nginx）→ 新建 Agent 审计任务 → SSE 实时观看
→ 任务完成（completed/completed_with_gaps）→ 查看 findings
→ 对一条 finding 点 reverify → 导出报告 → 删除任务 → 退出
```

关键断言：
- 每个步骤 UI 状态与 API 状态一致（页面任务状态 = DB status）
- 审计过程 SSE 无长时间静默（心跳 45s/长操作 180s 内必有事件）
- 任务终态后页面有完整结束点（task_complete 事件）
- reverify 在"任务目录已被清理"后仍可用（ZIP 自动重解压）

### 第 5 层：代码审计全流程（深度断言）

对一个**已知漏洞样本**（ZIP 内放 SQLi/XSS/命令注入样例代码）跑完整审计：

| 阶段 | 断言 |
|---|---|
| 导入解压 | `/tmp/lanjian/<task_id>` 下源码完整解压；RAG 分块进度消息出现（CHUNK_PROGRESS/EMBED_PROGRESS 文案） |
| 静态扫描 | Semgrep/Bandit 预扫描结果进入 Recon 上下文（DB agent_events 有工具调用记录） |
| Multi-Agent | Orchestrator → Recon/Analysis/Verification 阶段事件齐全；27 种 SSE 事件中出现 phase_start/phase_complete/dispatch 系列 |
| 发现落库 | findings 落库；**理论风险 finding（缺 file_path 但 confidence≥0.7）不整条消失**（REQ-VP-3） |
| 沙箱验证 | 每条需验证 finding 有 sandbox_attempts；证据按 finding_id 绑定（不为 null，REQ-VP-4）；伪造标记（Simulated 等）被排除 |
| 验证状态 | 四档状态由运行时证据代码化推导：有铁证=confirmed；仅静态演示=static_confirmed（不得判 confirmed）；沙箱失败=not_reproducible；无沙箱证据/崩溃=needs_context（Bug D：不得算已验证） |
| 覆盖率门禁 | D1-D10 门禁评估；门禁连续拒绝 3 次后确定性终止（R4） |
| 任务收尾 | 终态后 observations 落库；`/tmp/lanjian/<task_id>` 已清理；db/redis 保持 healthy |

## 四、重点回归清单（历史事故，必测）

1. **tmpfs 塞满事故**（2026-08-27，v6.2.4 修复）：连续跑 ≥2 个任务，确认每个任务结束后目录被清理，`/tmp` 占用不累积；期间 db/redis 不得出现 unhealthy。
2. **reverify 兼容**：任务结束后（目录已清理）reverify ZIP 项目 → 自动重解压成功；仓库项目 → 409 明确提示。
3. **SSE 断线横幅**：RAG 索引阶段（>10min）页面不得误报"任务已断开"（early heartbeat 兜底）。
4. **验证状态误判**：Java 反序列化样本不得误判 not_reproducible（语言分流）；nginx 配置类理论风险 finding 必须可见。
5. **部署重启恢复**：backend 容器重启后，原 running 任务按 stale 处理（recover 或失败），不得永久 running。

## 五、问题记录规范（每个 FAIL 必须产出）

在报告中为每个问题填写：

```markdown
### 问题 #N：<一句话标题>
- 层级：后端/前端/沙箱/网页/审计流程
- 严重级：🔴致命 / 🟠严重 / 🟡一般 / 🟢优化
- 现象：实际行为（附日志/截图/DB 证据行号）
- 复现步骤：最小步骤
- 预期：应是什么行为
- 证据：命令输出原文、页面截图、psql 查询结果、docker logs 片段
```

## 六、深度分析规范（对每个 🟠 及以上问题必须产出）

```markdown
### 深度分析：问题 #N
1. 现象复现：触发场景/输入/预期/实际
2. 根因定位：具体模块、函数、调用链、文件:行号，解释"为何该实现导致此问题"
3. 影响范围：哪些功能/数据/上下游模块受影响
4. 严重分级理由
5. 修复方案（≥2 套）：各含思路/改动范围/风险/工作量/测试策略，推荐一套并说明理由
   （唯一方案例外需声明"经客观分析仅 1 个可行方案，原因：…"）
```

## 七、产出物

1. `docs/e2e-test-report-<YYYY-MM-DD>.md`：五层用例逐项 PASS/FAIL + 证据 + 问题清单
2. 每个 🟠 以上问题的深度分析（并入报告）
3. 截图/日志存档路径清单
4. 最终结论：整体通过 / 有条件通过（列遗留）/ 阻塞

## 八、纪律

- **禁止伪造**：任何 PASS 必须附真实证据；无法执行的用例标 BLOCK 并说明原因。
- **禁止只报结论**：FAIL 无深度分析视为未完成。
- **测试隔离**：E2E 期间不修改被测代码；发现问题只记录，不动手修（修复另走变更流程）。
- **清理**：测试产生的项目/任务/上传 ZIP 在报告完成后清理或列出，不污染生产数据。
