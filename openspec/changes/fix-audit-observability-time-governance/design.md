# Design: fix-audit-observability-time-governance

## Context

两次生产任务（nacos/nginx）0 findings 的技术性根因已定位到行号（见 proposal）。本变更只做防御性增强与观测补全，不改变审计策略与验证语义（策略类改动归 `sandbox-verification-hard-gate`）。

## Architecture

### 1. 任务级超时默认值短路修复

**现状**：`AgentTaskCreate.timeout_seconds = Field(1800)`（agent_tasks.py:140）→ 创建必落 1800（:2467）→ 执行时 :978 `task.timeout_seconds or 1800` 以最高优先级传入 → `_resolve_task_timeout`（orchestrator.py:574-588）第一优先级永远命中 → 全局 `agentTimeout` 死配置。

**设计**：
- `AgentTaskCreate.timeout_seconds: int | None = Field(None, ge=60, le=7200)`
- 创建落库：`timeout_seconds=request.timeout_seconds`（None 即 NULL）
- 执行入口（:978 与 watchdog :1024 附近）：`task.timeout_seconds` 为 NULL 时传 `None` 给 `task_timeout_seconds`；watchdog 的 `task_timeout` 改为从新提取的共享函数 `resolve_task_timeout_seconds(task, user_config)` 获取（逻辑与 `_resolve_task_timeout` 同源：显式任务级 > `llmConfig.agentTimeout` > `settings.AGENT_TIMEOUT_SECONDS` > 1800），保证两处时钟同源
- `_resolve_task_timeout` 不改（已支持 None 回退）
- **兼容**：历史任务 DB 中已有 1800 的行保持原行为（Scenario 已定义）

### 2. finish_reason 截断可见化

**现状**：adapter done 块带 `finish_reason`（litellm_adapter.py:526-548），消费端丢弃（base.py:1145-1149）。

**设计**：
- `BaseAgent.stream_llm_call` 的 done 块读取 `chunk.get("finish_reason")`；`=="length"` 时：`await self.emit_event("warning", ...)`（含 agent 名/迭代号/max_tokens 值）+ 在 `_conversation_history` 追加一条 system 提示（"上一轮输出被 max_tokens 截断，请压缩输出或分批输出"）
- 角色决策：追加进 `_conversation_history` 的提示消息 role 取 **user** 而非 system——各 Agent ReAct 循环中既有系统注入（空响应重试、强制总结指令）全部以 user 角色追加，保持一致且避免部分模型/网关对多 system 消息的兼容问题；提示正文以"（系统提示：…）"前缀标明其系统来源
- 归因入口：截断标志 `self._last_llm_truncated` 在 Analysis 的 is_final 分支统一消费（json-repair 会把半截 findings JSON 修成含部分/空 findings 的合法 dict 绕过"无 findings 键"分支，故不能只挂解析失败 else 分支）；`_run_forced_summary` 强制总结轮同样接入；归因走 emit_event warning + 容器日志双通道
- 返回签名不变（`(output, tokens)`），截断标志挂 `self._last_llm_truncated`（ Final Answer 解析失败路径可读取并在 warning 中归因"疑似截断"）

### 3. 类型化拒发新调度阈值

**现状**：`_budget_refusal`（orchestrator.py:622-629）统一 `min_dispatch=30s`——对单次 100-600s 的子任务无意义；派发后 `_maybe_request_soft_stop`（剩余<180s）立即触发软停（同一秒 dispatch+soft_stop，nacos 任务实证空转 137s）。

**设计**：
- 常量提升为配置：`TIME_BUDGET_MIN_EFFECTIVE = {"analysis": 300, "verification": 300, "recon": 120}`（core/config.py，可用 settings 覆盖）
- `_budget_refusal(agent_name)`：剩余 < 类型化最小有效时长 → 返回拒绝文案（含剩余秒数与所需时长）并 `_record_gate_observation("dispatch_budget", ...)`
- 拒发阈值（300s）> 软停止阈值（180s）→ "派发后立即软停"的矛盾自然消除，不改软停止逻辑本身
- 拒绝后主循环按"预算将尽"语义收口（复用现有 `prompt_suffix` 收口路径）

### 4. Analysis observation 截断

**设计**：
- 从 verification.py:1863-1883 提取 `_truncate_head_tail(text, max_chars)` 到 base.py（BaseAgent 静态方法），verification 改为调用共享实现（行为不变）
- analysis.py:835-838 追加 observation 前调用：`obs_for_history = self._truncate_head_tail(observation, get_agent_config().observation_history_max_chars)`
- 完整 observation 仍进入 `_steps`（给 orchestrator 的上报不受影响）

### 5. Analysis 执行状态上报

**现状**：analysis.py:925-945 返回 data 只含 findings/steps；orchestrator `_search_registry` 更新（2391-2404）与 CrossRoundContext 构建（2179-2187）已支持从 `agent_data["files_read"]/["grep_patterns"]` 读取——只是 analysis 从不提供。

**设计**：
- analysis run() 收尾时从 `self._steps` 聚合：`files_read = sorted({s.action_input.get("file_path") for s in steps if s.action=="read_file" and s.action_input})`、`grep_patterns = sorted({s.action_input.get("keyword") or s.action_input.get("pattern") for s in steps if s.action in ("search_code","semgrep_scan") and s.action_input})`
- data 增加两字段；orchestrator 侧零改动（已有消费逻辑）

### 6. 沙箱就绪真实检查

**设计**：
- `SandboxManager.initialize()`（sandbox_tool.py:60-84）docker 连接成功后追加：`self._docker_client.images.get(self.config.image)`（ImageNotFound/其余异常均捕获，`_init_error` 记录"镜像不存在: <image>"）；`is_available` 语义不变（daemon+镜像都 OK 才 True）
- agent_tasks.py:688：`if sandbox_manager.is_available: emit done` else `emit failed`（metadata: `{"init_status":"failed","diagnosis": sandbox_manager.get_diagnosis(),"image": settings.SANDBOX_IMAGE}`）；任务不中断（verification 内部已有 Docker 不可用降级）
- **注意**：`is_available` 语义变化会影响既有降级路径（sandbox_tool.py:402-406 等）——镜像缺失时这些路径提前生效，正是预期行为（C 变更将进一步把该场景标记 infra_error）

## Data Flow

配置链：`user_configs.llmConfig.agentTimeout(7200)` → `_get_user_config`(任务启动现查) → `resolve_task_timeout_seconds` → deadline/watchdog。
截断链：SGLang `finish_reason=length` → adapter done 块 → base.py 消费 → warning 事件 + 历史提示。

## Error Handling

- 全部新增逻辑失败时非致命：镜像检查异常 → `_init_error` 记录，任务继续；截断提示追加失败 → 仅日志；上报聚合失败 → data 缺省空列表
- 兼容性：历史任务（timeout_seconds=1800 已落库）行为不变；`is_available=False` 的新触发场景在 verification 侧走既有降级（记录失败 attempt）

## Testing Strategy

- 每项 TDD 五步（先失败测试），测试文件归位 backend/tests/
- Task 1 重点：创建 API 不传 timeout_seconds → DB NULL；传 3600 → 落 3600；watchdog 与 deadline 同源
- Task 6 重点：monkeypatch images.get 抛 ImageNotFound → 事件 failed + diagnosis；镜像在 → done
- 全量回归：一次性容器跑 pytest（需 -e SECRET_KEY -e POSTGRES_PASSWORD）
- 端到端：改完配置跑一个小项目真实审计，人工核对四指标（findings/格式重试/单轮耗时/沙箱事件）
