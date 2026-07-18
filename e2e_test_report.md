# 蓝鉴 E2E 全流程测试报告

**测试时间**: 2026-07-13
**测试环境**: http://192.168.238.11
**测试账号**: admin / 123456789

---

## 环境基线（✅ 通过）

| 检查项 | 结果 |
|--------|------|
| 4 个容器运行 | ✅ lanjian-frontend-1, lanjian-backend-1, lanjian-db-1, lanjian-redis-1 |
| 后端启动状态 | ✅ Application startup complete. |
| Alembic 版本 | ✅ 021_sba |
| agent_findings.sandbox_attempts 列 | ✅ 存在 |

---

## 阶段二：代码审计核心流程（⚠️ 部分通过）

对 completed 任务 ea11e1b0 分析：
- ✅ Orchestrator 调度了 recon / analysis / verification Agent
- ✅ Semgrep 预扫描执行
- ⚠️ verification 阶段只有 phase_start/phase_complete，无 tool_call/tool_result 事件
- ❌ 无 sandbox_exec 工具调用记录

---

## 阶段三：沙箱验证证据持久化（⚠️ 部分通过）

对 completed_with_gaps 任务 28a39be5：
- ✅ API 响应包含 sandbox_attempts 字段
- ⚠️ finding_id 大多为 null（Opt-1 未生效，LLM 自写 PoC 无 # FINDING_ID 注释）

修复后新任务 fcf914bf 事件分析：
- ✅ tool_output 最大长度达到 **10513**，Opt-2 的 10000 字符截断已生效
- ✅ 任务进入 verification 阶段，沙箱成功挂载项目目录
- ❌ 新任务在 analysis 阶段运行超过 45 分钟未完成，疑似陷入循环

---

## 阶段四：合并逻辑与门禁（❌ 未通过）

- ❌ API 响应缺少 verification_status 字段
- ❌ 无法验证 Bug B/C/D 的门禁逻辑
- ⚠️ 部分 finding 有 success=true/exit_code=0 的 sandbox_attempts 但 is_verified=false

---

## 阶段五：Re-audit API（⚠️ API 通过，执行未完成）

修复前：
- ✅ POST /re-audit 对 completed_with_gaps 返回 200：`{message: "re-audit started", unverified_count: 3}`
- ✅ 对 completed 任务返回 400
- ❌ re-audit 启动后任务状态变为 failed（根因：AgentEventEmitter 缺少 emit_event 方法）

修复后：
- ✅ POST /re-audit 对 008729c7 返回 200：`{message: "re-audit started", unverified_count: 8}`
- ✅ re-audit 任务成功启动并推进（findings_count=4, total_iterations=20, tokens_used=31101）
- ⚠️ re-audit 运行超过 30 分钟仍未完成，仍在 analysis 阶段

---

## 阶段六：Recover API（✅ 通过）

- ✅ POST /recover 对 running 任务返回 200：`{message: "task recovered to paused state, can resume"}`
- ✅ 任务状态变为 paused，pause_reason=stale_running_recovered
- ✅ 对 completed_with_gaps 调用 recover 返回 400
- ❌ resume 后任务失败（同样因为 AgentEventEmitter.emit_event 缺失）

---

## 阶段七/八：前端 UI 验证（⚠️ 部分通过）

修复前：
- ✅ completed_with_gaps 任务页面显示"补充审计"按钮（蓝色提示条）
- ✅ 点击按钮后显示 Toast "re-audit started"
- ✅ 未出现 "re is not defined" 错误

修复后：
- ✅ 新任务页面能正常显示运行中状态（Agent Tree、Activity Log、审计进度）
- ⚠️ InitProgress 组件未截到静态截图（初始化 1 秒内完成，事件日志证实 init_step 正常推送）
- ⚠️ 未验证"恢复任务"按钮显示（无 running 状态任务可用）
- ⚠️ 未验证沙箱证据卡片（任务未完成，无 sandbox_attempts 可展示）

---

## 阶段九：回归测试（✅ 通过）

- ✅ 历史任务 findings API 包含 sandbox_attempts 字段
- ✅ 旧数据 sandbox_attempts 为 null
- ✅ 数据库迁移已应用
- ✅ 容器健康

---

## 发现的关键 Bug

### P0：AgentEventEmitter 缺少 emit_event 方法 ✅ 已修复
- **影响**：所有新任务、re-audit、resume 在初始化阶段直接失败
- **位置**：backend/app/api/v1/endpoints/agent_tasks.py:430,556,557,630,631,673 等
- **错误**：`'AgentEventEmitter' object has no attribute 'emit_event'`
- **验证**：修复后新任务 `fcf914bf` 能正常启动，事件队列增长到 4000+，进入 verification 阶段

### 新发现：Agent 任务执行异常缓慢 / 可能陷入循环
- **现象1**：新任务 `fcf914bf` 在 analysis 阶段运行超过 45 分钟，findings_count 始终为 0
- **现象2**：re-audit 任务 `008729c7` 运行超过 30 分钟，仍在 analysis 阶段
- **现象3**：任务 `67a71f4a` 因 `llm_error` 自动暂停
- **可能原因**：Analysis Agent 在 Recon/Analysis 阶段循环探索，无法收敛到 findings；或 LLM 调用异常导致任务无法推进

### P1：前端 TypeScript 语法错误
- **位置**：frontend/src/pages/AgentAudit/types.ts:78
- **错误**：`export export interface InitStep`
- **影响**：TypeScript 编译失败，前端构建无法通过

### P2：API 缺少 verification_status
- **位置**：backend/app/api/v1/endpoints/agent_tasks.py AgentFindingResponse schema；backend/app/models/agent_task.py AgentFinding.to_dict
- **影响**：测试计划阶段四无法验证 Bug B/C/D

### P3：tool_output 截断未生效
- **位置**：backend/app/services/agent/agents/base.py:906,1264
- **现象**：emit_tool_result 支持 10000，但 call_tool 传 [:500]，execute_tool 传 [:200]

### P4：finding_id 未精确注入
- **位置**：backend/app/services/agent/agents/verification.py
- **现象**：LLM 自写 PoC 无 # FINDING_ID 注释，导致 sandbox_attempts.finding_id 为 null

### P5：re-audit resume_state 读取位置错误
- **位置**：backend/app/api/v1/endpoints/agent_tasks.py:302-354 vs :432-439
- **现象**：resume_state 存到 state_data，但从 checkpoint_metadata 读取，导致恢复状态丢失

---

## 测试影响的数据变更

| 任务 ID | 原状态 | 现状态 | 操作 |
|---------|--------|--------|------|
| 28a39be5 | completed_with_gaps | failed | 修复前 re-audit 测试 |
| bea4be71 | completed_with_gaps | failed | 修复前浏览器按钮点击测试 |
| ea0afd75 | running | failed | 修复前 recover + resume 测试 |
| fcf914bf | （新建） | cancelled | 修复后新任务测试（运行 45+ 分钟未收敛） |
| 67a71f4a | （新建） | cancelled | 修复后 InitProgress 测试（因 llm_error 暂停后取消） |
| 008729c7 | completed_with_gaps | cancelled | 修复后 re-audit 测试（运行 30+ 分钟未收敛） |

---

## 结论

### 已验证通过
- ✅ P0 Bug `AgentEventEmitter.emit_event` 已修复，新任务能正常启动
- ✅ InitProgress 事件链路正常（Docker sandbox → Extracting project → Indexing code → Preparing agents → Starting audit）
- ✅ Opt-2 tool_output 截断生效（实测 10513 字符）
- ✅ 前端"补充审计"按钮展示与点击正常
- ✅ Recover API 行为正确
- ✅ Re-audit API 能成功启动任务

### 仍阻塞的问题
- ❌ Agent 任务执行异常缓慢/无法收敛：新任务和 re-audit 都在 analysis 阶段运行 30-45 分钟以上未完成，无法获得 findings 来验证 sandbox_attempts、verification_status、门禁逻辑
- ❌ API 仍缺少 `verification_status` 字段
- ❌ 前端 `types.ts` 存在 `export export` 语法错误

### 建议
当前最大阻塞是 **Agent 执行无法收敛**。建议优先排查 Analysis Agent 的循环探索问题（为何 Recon/Analysis 阶段长时间无 findings 产出），然后再 rerun 完整 E2E。
