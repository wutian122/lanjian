# Agent 核心执行链路 — 代码级分析

日期：2026-06-22
范围：backend/app/services/agent/（base.py + orchestrator.py + core/executor.py）
类型：只读分析产出

> ⚠️ **2026-08-19 复核**：本文为 2026-06-22 代码级分析，行号有漂移；**熔断器与令牌桶限流现已接线**（`core/circuit_breaker.py`、`core/rate_limiter.py` 有实际调用点）、SSE 事件已 27 种、覆盖门禁 D1-D10 已含核心三角；仅作架构参考。

## 1. 架构总览

```
OrchestratorAgent.run(input_data)          # 编排层，ReAct 循环 max 20 轮
  ├── Phase 0: _run_semgrep_prescan()      # Semgrep 预扫描（subprocess）
  └── for iteration in range(max_iterations):
        ├── is_cancelled 检查              # 取消传播
        ├── check_messages()               # 用户实时协同指令
        ├── stream_llm_call(history)       # 流式 LLM 调用（base.py:960）
        │     └── compress_messages_if_needed()  # 上下文压缩
        ├── _parse_llm_response(output)    # 解析 Thought/Action/Action Input
        └── 执行 Action:
              ├── finish  → 三层门禁（沙箱证据/软覆盖率/硬覆盖率）→ break
              ├── dispatch_agent → _dispatch_agent() → sub_agent.run()
              └── summarize → _summarize_findings()
```

**关键事实**：base.py 的 `run()` 是 `@abstractmethod`（base.py:502-513，仅 `pass`），ReAct 循环由各子类各自实现。orchestrator/recon/analysis/verification 各有自己的 `run()`，共用 base.py 的 `stream_llm_call`/`execute_tool`/`emit_*` 基础设施。

## 2. ReAct 循环主体（以 orchestrator.py 为例）

### 2.1 循环结构（orchestrator.py:349-551）

```
L349  for iteration in range(self.config.max_iterations):  # max_iterations=20
L350    if self.is_cancelled: break                        # 取消检查（每轮开头）
L355    pending_messages = self.check_messages()           # 拉取用户协同指令
L373    if self.is_cancelled: break                        # 二次取消检查（LLM 前）
L379    llm_output, tokens = await self.stream_llm_call(   # 流式 LLM 调用
            self._conversation_history)
L390    if not llm_output:                                 # 空响应处理
          _empty_retry_count++ → 5 次后 break
L425    step = self._parse_llm_response(llm_output)        # 解析 Thought/Action
L470    if step.action == "finish":                        # → 三层门禁（P0 已恢复）
L481    elif step.action == "dispatch_agent":              # → 调度子 Agent
L502    elif step.action == "summarize":                   # → 汇总当前发现
L513    history.append({role:user, content:Observation})   # 观察结果回写历史
```

### 2.2 LLM 响应解析（_parse_llm_response）

解析 ReAct 格式文本：
- `Thought:` → step.thought
- `Action:` → step.action（dispatch_agent/summarize/finish）
- `Action Input:` → step.action_input（JSON 解析，经 AgentJsonParser）

空响应/格式错误 → 注入重试提示 continue。

## 3. LLM 调用链路

### 3.1 流式调用（base.py:960 stream_llm_call）

```
stream_llm_call(messages)
  ├── L982  compress_messages_if_needed(messages)   # 上下文压缩
  ├── L989  if self.is_cancelled: return "",0       # 取消检查
  ├── L999  stream = llm_service.chat_completion_stream(messages)
  └── while True:                                    # 流式消费循环
        ├── is_cancelled 检查                        # 流中取消
        ├── 首 Token 超时检测（LLM_FIRST_TOKEN_TIMEOUT=120s）
        ├── Token 间隔超时检测（LLM_STREAM_TIMEOUT=60s）
        └── emit_thinking_token(token, accumulated)  # SSE 推送
```

### 3.2 LLM Service → Adapter（service.py）

```
llm_service.chat_completion_stream(messages)
  └── adapter = factory.get_adapter(provider)       # 11 提供商工厂
  └── async for chunk in adapter.stream_complete(request)
```

### 3.3 Adapter 重试（base_adapter.py:195 retry）

```
adapter._send_request(request)
  └── self.retry(fn, max_attempts=3, delay=1.0)
        ├── 4xx 错误 → 不重试，直接 raise
        ├── 其他错误 → 指数退避 sleep(delay * 2^attempt)
        └── max_attempts 次后 raise last_error
```

各适配器（litellm/baidu/doubao/minimax）在 `_send_request` 中调用 `self.retry()`。litellm 用 max_attempts=5, delay=2.0。

### 3.4 circuit_breaker（2026-08-20 复核：已接线）

> 本文 2026-06-22 初稿结论为"定义未调用"，**现已过时**：熔断器已接入 LLM 调用外层——`agents/base.py:1144` `await get_llm_circuit().call(_consume)`，熔断开启时返回 `[API_ERROR:circuit_open]` 拒绝调用（`base.py:1145-1147`）。配置见 `config.py`（`circuit_failure_threshold` 默认 10、`circuit_recovery_timeout_seconds` 默认 60、`circuit_half_open_max_calls` 默认 3）。

### 3.5 rate_limiter（2026-08-20 复核：已接线）

初稿"grep 未发现调用点"同样过时：令牌桶限流已接入——`agents/base.py:1142` `await self._get_llm_rate_limiter().acquire()` 在每次 LLM 调用前获取令牌。

## 4. 工具执行链路（base.py:1149 execute_tool）

```
execute_tool(tool_name, tool_input)
  ├── L1161  if self.is_cancelled: return "已取消"        # 执行前取消检查
  ├── L1170  self._tool_calls += 1
  ├── L1171  emit_tool_call(tool_name, tool_input)         # SSE 推送
  ├── L1177  tool_timeouts = {semgrep:120, sandbox:60,...} # 按工具类型超时
  ├── L1197  execute_with_cancel_check()                   # 包装执行
  │     └── asyncio.wait_for(tool.execute(**input), timeout)
  ├── L1201  emit_tool_result(tool_name, result, duration) # SSE 推送
  ├── L1204  if self.is_cancelled: return "已取消"         # 执行后取消检查
  ├── L1218  output 截断（>6000 字符截断）
  └── 异常处理：CancelledError/Exception → 返回错误字符串（不抛出）
```

**注意**：`call_tool`（base.py:899）是简化版，无超时/取消检查，直接 `tool.execute()`。`execute_tool` 是带超时取消的完整版。Agent 的 ReAct 循环通过 `_dispatch_agent` 间接调用子 Agent，子 Agent 内部用 `execute_tool`。

## 5. 并行执行（core/executor.py）

### 5.1 DynamicAgentExecutor.execute_parallel（L197）

```
execute_parallel(tasks, agent_factory)
  ├── L218  sorted(tasks, key=priority, reverse=True)     # 按优先级排序
  ├── L221  independent = [t for t if not t.dependencies] # 分离无依赖
  ├── L222  dependent = [t for t if t.dependencies]       # 分离有依赖
  ├── L234  _execute_task_batch(independent, factory)     # 并行执行无依赖批
  └── L238  for task in dependent:                        # 串行执行有依赖
        ├── _wait_for_dependencies(task)                  # 等待依赖完成
        └── _execute_single_task(task, factory)
```

### 5.2 依赖等待（_wait_for_dependencies, L383）

```
for dep_id in task.dependencies:
  while dep_task.status in ["pending","running"]:
    if self._cancelled: return
    await asyncio.sleep(0.1)    # 轮询等待，100ms 间隔
```

**注意**：这是轮询等待（非事件驱动），有 100ms 延迟。最大并行 5 个（SubAgentExecutor 限制）。

### 5.3 SubAgentExecutor.create_and_run_sub_agent（L481）

动态创建子 Agent（analysis/verification），传递 handoff 上下文，执行后收集结果。

## 6. 取消传播机制

```
agent_tasks.py: set_cancel_callback(check_global_cancel)  # 外部回调注入
  → orchestrator.cancel()                                  # 传播到子 Agent
       → for agent in sub_agents: agent.cancel()           # 级联取消
  → is_cancelled property (base.py:528)
       → if self._cancelled: return True
       → if self._cancel_callback and self._callback(): return True  # 检查全局标志
```

取消检查点：循环开头（L350）、LLM 调用前（L373）、LLM 流中（L1013）、工具执行前（L1161）、工具执行后（L1204）。

**已知缺陷**：`_cancel_callback` 在 `__init__` 中初始化（base.py:521），但部分测试用 `__new__` 绕过 `__init__`，导致 `AttributeError`（P0 中确认的预先存在测试失败）。

## 7. Agent 合约机制（agent_contract.py）

合约通过 `inject_agent_contract()` 注入系统提示词：
- **Turn 预留**（turn_reserve=3）：剩余 ≤3 轮时强制提示 LLM 产出 Final Answer
- **截断防御**：`AGENT_HEADER_START` + `AGENT_OUTPUT_END` 哨兵标记
- **探索上界**（max_sink_categories=8）：每维度最多 8 类 Sink
- **调用链深度**（min_call_chain_depth=3）：高危漏洞必须追踪 3 层

## 8. 弹性机制现状汇总

| 机制 | AGENTS.md 声称 | 代码实际 | 状态 |
|------|:---:|:---:|:---:|
| 熔断器 | 所有 LLM 调用必须经过 | base.py:1144 get_llm_circuit().call() | ✓ 已接线（2026-08） |
| 重试 | 指数退避 max_attempts=3 | adapter.retry() 已实现 | ✓ 在适配器层 |
| 限流 | 令牌桶 rate=1.0/s | base.py:1142 rate_limiter.acquire() | ✓ 已接线（2026-08） |
| 降级 | 6 种 FallbackAction | fallback.py 定义 | 待核调用点 |
| 检查点 | 状态持久化 | persistence.py | 待核调用点 |

## 9. 关键代码位置索引

| 组件 | 位置 |
|------|------|
| ReAct 循环 | orchestrator.py:349 |
| LLM 流式调用 | base.py:960 |
| 工具执行（带超时） | base.py:1149 |
| 工具执行（简化） | base.py:899 |
| 取消检查 property | base.py:528 |
| 并行执行 | executor.py:197 |
| 依赖等待 | executor.py:383 |
| Adapter 重试 | base_adapter.py:195 |
| finish 三层门禁 | orchestrator.py:552（P0 恢复） |
| Semgrep 预扫描 | orchestrator.py:_run_semgrep_prescan |
