# Tasks: fix-audit-observability-time-governance

## Phase 1: 时间预算短路修复

- [x] **Task 1 完成**（commit c60d26b，review CLEAN——3 Scenario 全 ✅，14 测试过，回归 730/4 与基线持平；Minor 记账：双份解析逻辑靠时钟同源测试锁定、service.py:49 负数 agentTimeout 穿透留待 Task 5/C 变更、字符串容忍为理论路径）

### Task 1: 任务级 timeout_seconds 默认值短路修复
- Files: `backend/app/api/v1/endpoints/agent_tasks.py`（AgentTaskCreate :140、创建落库 :2467、执行入口 :978、watchdog :1024 附近）
- Interfaces: Consumes `AgentTaskCreate.timeout_seconds`（改为 `int|None`）、全局 `llmConfig.agentTimeout`；Produces 共享函数 `resolve_task_timeout_seconds(task, user_config) -> float`
- TDD:
  1. 写失败测试：创建任务不传 timeout_seconds → DB 落 NULL；显式传 3600 → 落 3600
  2. 跑测试确认失败（现落 1800）
  3. 最小实现：Field 改 Optional、落库去默认、:978 传 None；提取 `resolve_task_timeout_seconds` 供 watchdog 与 orchestrator 同源使用
  4. 跑测试通过 + 全量回归
  5. commit
- 验收：全局 agentTimeout=7200 时新建任务 deadline=7200s（事件或日志可证）；历史 1800 任务行为不变

## Phase 2: 可观测性

- [ ] **Task 2 完成**
- [ ] **Task 3 完成**
- [ ] **Task 4 完成**

### Task 2: finish_reason 截断可见化
- Files: `backend/app/services/agent/agents/base.py`（stream_llm_call done 块 :1145-1149）
- Interfaces: Consumes adapter done 块 `finish_reason` 字段；Produces `self._last_llm_truncated` 标志 + warning 事件
- TDD:
  1. 写失败测试：mock 流式 chunk finish_reason="length" → 断言 emit warning 且 `_last_llm_truncated=True`
  2. 跑失败
  3. 实现：done 块读取 finish_reason，length 时 emit + 历史追加截断提示
  4. 通过 + 回归
  5. commit

### Task 3: Analysis observation 截断
- Files: `backend/app/services/agent/agents/base.py`（新增 `_truncate_head_tail` 静态方法）、`backend/app/services/agent/agents/analysis.py`（:835-838）、`backend/app/services/agent/agents/verification.py`（:1863-1883 改调共享实现）
- Interfaces: Consumes `observation_history_max_chars` 配置；Produces `BaseAgent._truncate_head_tail(text, max_chars) -> str`
- TDD:
  1. 写失败测试：50000 字符 observation 追加后 history 中该条 ≤ max_chars 且含头 1500+尾 1500；短文本原样
  2. 跑失败
  3. 实现：提取共享函数，analysis 追加前调用，verification 切换共享实现（行为不变）
  4. 通过 + 回归
  5. commit

### Task 4: Analysis 执行状态上报
- Files: `backend/app/services/agent/agents/analysis.py`（run() 收尾 data 组装 :925-945）
- Interfaces: Produces data 新增 `files_read: list[str]`、`grep_patterns: list[str]`；Consumes 无（orchestrator :2391-2404 / :2179-2187 已有消费逻辑，零改动）
- TDD:
  1. 写失败测试：模拟 steps 含 3 次 read_file + 2 次 search_code → data.files_read 去重 3 项、grep_patterns 2 项；0 工具调用 → 两字段为空列表
  2. 跑失败
  3. 实现：从 `self._steps` 聚合
  4. 通过 + 回归 + 手工验证 orchestrator CrossRoundContext 在 0 findings 时含"禁止重复"段落
  5. commit

## Phase 3: 时间治理与沙箱预检

- [ ] **Task 5 完成**
- [ ] **Task 6 完成**

### Task 5: 类型化拒发新调度阈值
- Files: `backend/app/core/config.py`（新增 `TIME_BUDGET_MIN_EFFECTIVE` 配置）、`backend/app/services/agent/agents/orchestrator.py`（`_budget_refusal` :622-629）
- Interfaces: Consumes 剩余预算；Produces 拒绝文案 + `_gate_observations` 记录 `{gate:"dispatch_budget", remaining_seconds, required_seconds, agent_name}`
- TDD:
  1. 写失败测试：剩余 120s 派发 analysis → 拒绝且 observations 新增记录；剩余 900s → 不拒绝；recon 类型阈值 120s 边界
  2. 跑失败
  3. 实现：类型化最小有效时长常量（analysis/verification 300、recon 120，settings 可覆盖）
  4. 通过 + 回归
  5. commit

### Task 6: 沙箱镜像预检与真就绪事件
- Files: `backend/app/services/agent/tools/sandbox_tool.py`（initialize :60-84）、`backend/app/api/v1/endpoints/agent_tasks.py`（:688）
- Interfaces: Consumes `settings.SANDBOX_IMAGE`；Produces `is_available`（语义扩展：daemon+镜像）、failed 事件 metadata `{init_status, diagnosis, image}`
- TDD:
  1. 写失败测试：images.get 抛 ImageNotFound → initialize 后 is_available=False、_init_error 含镜像名；事件流断言 failed + diagnosis；镜像存在 → done
  2. 跑失败
  3. 实现：initialize 追加镜像检查（异常捕获）；:688 按 is_available 分支
  4. 通过 + 回归（注意 verification 既有 Docker 不可用降级路径的测试同步修正）
  5. commit

## Phase 4: 端到端验证

- [ ] **Task 7 完成**

### Task 7: 端到端真实审计验证
- Files: 无代码改动（验证任务）
- Interfaces: 两台生产已生效配置（llmMaxTokens=16384/llmTemperature=0.3/llmTimeout=300000/agentTimeout=7200 + 本机镜像 v6.1.0）
- TDD（验证型任务）:
  1. 准备：两台配置写入确认（ARM 待老板执行）；本机镜像 docker images 确认
  2. 用小项目（lanjian backend 某子模块或历史 ZIP）创建审计任务，显式 timeout_seconds=3600
  3. 核对指标：findings>0？无"格式错误"重试？单轮耗时下降（对比 47s 基线）？沙箱事件出现（本机）？截断告警不再出现（16384 下）？
  4. 结果记录到本变更 tasks.md 勾选依据与 observations
  5. 输出验证报告供老板决策 C 变更实施优先级
- 验收：四指标全部改善或明确归因
