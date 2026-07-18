# E2E 测试问题修复变更说明

**日期**: 2026-07-14 ~ 2026-07-18
**范围**: Agent 审计引擎核心流程 + SSE 数据流

---

## 一、问题背景

E2E 全流程测试报告（`e2e_test_report.md`）暴露了两类阻塞问题：

1. **Agent 任务执行无法收敛**：新任务在 analysis 阶段运行 30-45 分钟以上未完成，覆盖率门禁反复拦截导致无效循环
2. **SSE 数据流不同步**：前端重连后事件丢失，Activity Log 和 Agent Tree 显示不完整

---

## 二、修改清单

### 第一批：Agent 执行收敛修复（6 个文件）

#### 1. 覆盖率类型映射统一

**文件**: `backend/app/services/agent/coverage.py` + `backend/app/services/agent/core/coverage.py`

**问题**: Analysis Agent 实际产出的 `supply_chain`、`config` 等漏洞类型，两套覆盖率系统都不认识，导致覆盖率永远停留在 3/10，门禁反复拦截。

**改动**:
- `coverage.py` 的 `COVERAGE_DIMENSIONS`：D8 补充 `config`/`security_misconfiguration`/`misconfiguration`；D10 补充 `supply_chain`/`outdated_dependency`/`vulnerable_dependency`
- `core/coverage.py` 的 `_VULN_TYPE_MAP`：新增 `supply_chain`->D10、`config`->D8、`misconfiguration`->D8、`prototype_pollution`->D1、`graphql_injection`->D1 等映射

**效果**: Analysis Agent 产出的漏洞能被正确识别到对应覆盖率维度，减少无效门禁拦截。

#### 2. Orchestrator 总体超时保护

**文件**: `backend/app/api/v1/endpoints/agent_tasks.py`

**问题**: `orchestrator.run()` 没有 `asyncio.wait_for` 超时包裹，`task.timeout_seconds` 存在数据库但从未执行，导致任务能运行 5 小时甚至 4 天。

**改动**:
- 新增 `from app.services.agent.agents.base import AgentResult` 导入
- `orchestrator.run()` 调用处包裹 `asyncio.wait_for(run_task, timeout=task_timeout)`
- 超时后取消任务、保存已有发现、标记为 `COMPLETED_WITH_GAPS`

**效果**: 任务最长运行时间受 `timeout_seconds`（默认 1800 秒）约束，不再无限运行。

#### 3. 覆盖率门禁拦截次数降低

**文件**: `backend/app/services/agent/agents/orchestrator.py`

**问题**: 覆盖率门禁硬拦截 5 次后才安全阀放行，每次拦截都触发完整子 Agent 调度（30 轮 ReAct 迭代），总 LLM 调用量极高。

**改动**:
- 硬拦截上限从 5 次降到 3 次（`< 5` -> `< 3`、`>= 5` -> `>= 3`）
- `max_dispatch` 从条件 `4 if >= 3 else 3` 改为固定 `3`
- auto-bypass 阈值从 5 降到 3

**效果**: 减少约 40% 的无效 LLM 调用，缩短任务总执行时间。

#### 4. Verification 沙箱门禁弹性退出

**文件**: `backend/app/services/agent/agents/verification.py`

**问题**: 沙箱验证门禁要求所有 finding 都成功验证才允许 finish，即使已尝试很多次也强制重试，导致 Verification Agent 循环。

**改动**:
- 新增 `elastic_exhausted` 条件：当 `sandbox_exec` 总尝试次数 >= `max(finding数 * 3, 10)` 时放行退出
- 弹性退出时记录日志并推送 `info` 事件通知前端

**效果**: 沙箱验证在合理尝试次数后退出，避免无限重试。

#### 5. tool_output 截断对齐

**文件**: `backend/app/services/agent/agents/base.py`

**问题**: `emit_tool_result` 存储 10000 字符但 `execute_tool` 只返回 6000 给 LLM，导致 LLM 看不到完整沙箱输出。

**改动**: `execute_tool` 返回截断从 6000 提升到 10000，与 `emit_tool_result` 保持一致。

---

### 第二批：SSE 数据流修复（2 个文件）

#### 6. SSE 重连 DB 回补

**文件**: `backend/app/services/agent/event_manager.py`

**问题**: 前端重连时 `stream_events` 的 drain 阶段从内存队列取事件，但队列中的旧事件 `sequence <= after_sequence` 全部被跳过（实测 `skipped 1571`），重连后进入实时循环时队列为空，断连期间产生的事件永久丢失。

**改动**:
- 在内存队列 drain 完成后、进入实时循环前，新增 DB 回补逻辑
- 调用 `get_events(task_id, after_sequence, limit=500)` 从数据库查询断连期间未送达的事件
- 按序补发给前端，同时更新 `after_sequence` 防止实时循环重复发送
- 回补期间检测到终端事件（`task_complete`/`task_error`/`task_cancel`）时立即退出

**效果**: 前端重连后能收到断连期间产生的所有入库事件，Activity Log 和 Agent Tree 不再丢失历史。

#### 7. 工具执行心跳推送

**文件**: `backend/app/services/agent/agents/base.py`

**问题**: `execute_tool` 在工具执行期间不推送任何事件，Semgrep（120s）、Kunlun-M（600s）等长操作期间前端心跳超时（45s/180s）后误判断连，触发不必要的重连。

**改动**:
- 在 `execute_with_cancel_check` 的等待循环中新增 `_heartbeat_counter`
- 每 15 秒（0.5s * 30 次循环）推送一次 `info` 事件：`"⏳ {tool_name} 执行中... ({elapsed}s)"`

**效果**: 长操作期间前端持续收到心跳，不再误判断连，减少不必要的 SSE 重连。

---

## 三、改进总结

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 任务最长运行时间 | 无上限（实测 5 小时~4 天） | 受 `timeout_seconds` 约束（默认 30 分钟） |
| 覆盖率识别 | `supply_chain`/`config` 等类型不识别，覆盖率卡在 3/10 | 正确映射到 D10/D8，覆盖率能正常提升 |
| 门禁拦截次数 | 5 次硬拦截 + 4 次 dispatch | 3 次硬拦截 + 3 次 dispatch |
| 沙箱验证退出 | 所有 finding 必须成功验证 | 尝试 3 倍 finding 数后弹性退出 |
| LLM 可见输出 | 6000 字符截断 | 10000 字符，与存储一致 |
| SSE 重连事件 | 内存队列跳过后永久丢失 | 从 DB 回补最多 500 条事件 |
| 长操作期间 SSE | 无事件推送，前端误判断连 | 每 15 秒推送工具执行心跳 |

---

## 四、未修改的已知问题

以下问题在 E2E 报告中提及但本次未修改（需老板决定是否处理）：

1. **LLM 速率限制 `llmRatePerMinute: 5`**：用户级配置，需在前端系统设置中调高到 15-30
2. **LLM API Key 间歇性认证失败**：讯飞 MaaS 平台的 API Key 稳定性问题，需排查平台侧
3. **P4 finding_id 注入不完整**：已有 `_resolve_finding_id_from_command` 回退方法，但极端情况下仍可能为 null
4. **P5 正常 pause/resume 路径的 resume_state 一致性**：re-audit 路径已修复，正常 pause 路径可能仍有问题

---

## 五、验证状态

- [x] 全部 8 个文件通过 `py_compile` 语法检查
- [x] 覆盖率映射测试 5 项全部 PASS（新类型映射正确 + 旧类型无回归）
- [x] 修复已部署到服务器 `192.168.238.11`，容器重启后 `Application startup complete`
- [x] 容器内 `grep` 确认所有修改代码已生效
- [x] API 登录 + Tasks API 功能正常
- [ ] 完整 E2E 重跑验证（需老板手动触发新审计任务观察）
