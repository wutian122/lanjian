# 审计数据流 — 端到端分析

日期：2026-06-22
范围：API 入口 → Agent 执行 → 事件管道 → SSE 推送 → 前端渲染
类型：只读分析产出

> ⚠️ **2026-08-19 复核**：本文为 2026-06-22 代码级分析，行号有漂移；**熔断器与令牌桶限流现已接线**（`core/circuit_breaker.py`、`core/rate_limiter.py` 有实际调用点）、SSE 事件已 27 种、覆盖门禁 D1-D10 已含核心三角；仅作架构参考。

## 1. 端到端数据流总览

```
前端 POST /api/v1/agent-tasks/execute
  → agent_tasks.py: _execute_agent_task(task_id)     # 后台协程
     ├── 1. 克隆/解压项目代码
     ├── 2. RAG 索引（tree-sitter → Embedding → ChromaDB）
     ├── 3. _initialize_tools() 构建工具集
     ├── 4. 创建 4 Agent + OrchestratorAgent
     ├── 5. event_emitter = event_manager.create_emitter(task_id)
     ├── 6. orchestrator.run(input_data)             # ReAct 编排循环
     │      └── Agent 内 emit_* → event_manager.add_event()
     │            ├── _save_event_to_db()             # DB 持久化
     │            └── queue.put(event)                # 内存队列
     └── 7. 结果写 DB

前端 GET /api/v1/agent-tasks/{task_id}/stream        # SSE 连接
  → stream_agent_with_thinking() (L1994)
     ├── event_manager.stream_events(task_id)         # 优先内存队列
     └── 回退：DB 轮询                                 # 任务结束/无队列时
  → SSE 推送 → 前端 useResilientStream 解析渲染
```

## 2. 请求阶段：任务创建与执行

### 2.1 任务创建（agent_tasks.py）

```
POST /execute
  → 创建 AgentTask 记录（status=pending）
  → asyncio.create_task(_execute_agent_task(task_id))  # 后台协程，立即返回 task_id
```

### 2.2 执行协程（_execute_agent_task, L449-548）

```
L449  构造子 Agent（recon/analysis/verification）
L463  orchestrator = OrchestratorAgent(llm_service, tools, event_emitter, sub_agents)
L479  orchestrator.set_cancel_callback(check_global_cancel)
L486  _running_orchestrators[task_id] = orchestrator
L487  _running_event_managers[task_id] = event_manager  # SSE 流用
L531  run_task = asyncio.create_task(orchestrator.run(input_data))
L534  result = await run_task                            # 阻塞等待完成
```

**异步边界**：任务执行在独立协程，SSE 端点在另一协程，通过 `EventManager` 的内存队列 + DB 解耦。

## 3. 事件产生阶段：Agent → EventManager

### 3.1 AgentEventEmitter（event_manager.py:68）

OrchestratorAgent 构造时注入 `event_emitter`。Agent 在 ReAct 循环中调用 `emit_*` 方法：

```
orchestrator.emit_thinking(msg)      → emitter.emit_thinking()
orchestrator.emit_llm_thought(...)   → emitter.emit_llm_thought()
orchestrator.emit_tool_call(...)     → emitter.emit_tool_call()
orchestrator.emit_finding(...)       → emitter.emit_finding()
orchestrator.emit_phase_start(...)   → emitter.emit_phase_start()
```

SSE 事件类型共 **27 种**（2026-08-20 复核：权威清单为 `backend/app/models/agent_task.py` 的 `AgentEventType` 枚举——task_* 4、phase_* 2、thinking/planning/decision 3、tool_* 3、rag_* 2、finding_* 4、sandbox_* 4、progress 1、日志 4；`heartbeat` 由 SSE 流单独发送，不在枚举内）。下方列表为其中常用子集：phase_start/complete、thinking、llm_thought/decision/action、tool_call/result、finding_new/verified、task_complete/error/cancel、heartbeat。

### 3.2 EventManager.add_event（event_manager.py:269）

每个 emit 方法最终调用 `add_event`：

```
add_event(task_id, event_type, sequence, ...)
  ├── L285  生成 event_id + timestamp
  ├── L306  if event_type not in {"thinking_token"}:  # 高频事件跳过 DB
  │     └── _save_event_to_db(event_data)             # DB 持久化
  └── queue.put_nowait(event_data)                    # 内存队列推送
```

**关键设计**：
- `thinking_token`（Token 级流）不落 DB（避免高频写），仅走队列
- 其他事件既落 DB 又入队列（DB 用于断线续传/历史回放）
- sequence 单调递增（用于续传偏移）

### 3.3 内存队列生命周期

```
create_queue(task_id)  → asyncio.Queue()           # 任务启动时创建
  → Agent 产生事件 → queue.put_nowait()
  → SSE 流消费 → queue.get_nowait()
remove_queue(task_id)  → 任务结束后清理
```

## 4. SSE 推送阶段：两个端点的不同机制

### 4.1 `/events` 端点（L1904, stream_agent_events）— DB 轮询

```
GET /{task_id}/events?after_sequence=N
  → event_generator():
       while True:
         ├── 查 DB: AgentEvent where sequence > last_sequence limit 50
         ├── 有事件 → yield "data: {json}\n\n"
         ├── 检查 task.status ∈ {completed,failed,cancelled} → yield task_end, break
         ├── idle_time >= 300s → yield timeout, break
         └── await asyncio.sleep(0.5)              # 500ms 轮询
```

特点：纯 DB 轮询，0.5s 间隔，5 分钟空闲超时。不支持 thinking_token（未落 DB）。适合历史回放。

### 4.2 `/stream` 端点（L1994, stream_agent_with_thinking）— 队列优先 + DB 回退

```
GET /{task_id}/stream?after_sequence=N&include_thinking=true
  → enhanced_event_generator():
       event_manager = _running_event_managers.get(task_id)
       if event_manager:                            # 任务运行中
         ├── event_manager.stream_events(task_id, after_sequence)
         │     ├── 排空缓冲队列（MAX_DRAIN=5000）     # 连接前积压事件
         │     │     └── 过滤 sequence <= after_sequence
         │     │     └── 遇终端事件(task_complete/error/cancel) → break
         │     ├── 实时推送：async for event in queue
         │     └── 积压检测（>100 批量消费）
         └── format_sse_event(event) → yield
       else:                                         # 任务未运行
         └── 回退 DB 轮询（同 /events 机制）
```

特点：
- 运行中任务用内存队列（低延迟，支持 thinking_token）
- 已结束任务回退 DB 轮询
- `after_sequence` 实现断线续传（重连后补齐序列号 > N 的事件）
- thinking_token 加微小延迟（L2063，避免前端渲染过载）

### 4.3 续传机制（after_sequence）

```
断线重连时：
  前端记录 last_sequence
  → 重连请求 ?after_sequence=last_sequence
  → stream_events 排空时过滤 sequence <= after_sequence（跳过已发）
  → 仅推送 sequence > after_sequence 的事件
```

DB 事件保证续传完整（已落库）。队列事件：连接前积压的会先排空（MAX_DRAIN=5000 安全上限），连接后的实时推送。**风险**：若断线期间队列积压超过 5000，超出部分在排空阶段被截断（安全上限保护，防无限循环）。

## 5. 前端接收阶段：useResilientStream

### 5.1 连接方式（fetch + ReadableStream，非 EventSource）

```
useResilientStream(taskId, options)
  ├── 用 fetch（非 EventSource）连接 /stream
  │     原因：EventSource 不支持自定义 header，需传 Bearer token
  ├── reader = response.body.getReader()
  ├── TextDecoder 解码 SSE 文本
  └── 解析 "event: {type}\ndata: {json}\n\n" 格式
```

### 5.2 状态机

```
disconnected → connecting → connected
                    ↓           ↓
              reconnecting ←── (心跳超时/连接错误)
                    ↓
                 failed (超过 maxReconnectAttempts=5)
```

### 5.3 弹性机制

| 机制 | 配置 | 实现 |
|------|------|------|
| 心跳超时 | 45s | setTimeout，无新事件触发重连 |
| 指数退避 | 1s→30s | delay = min(maxDelay, initial * 2^attempt) |
| 抖动 | 0.3 | jitter = cappedDelay * 0.3 * (random-0.5)*2 |
| 最大重连 | 5 次 | 达到后 onMaxRetriesReached → failed |
| 单实例锁 | activeStreams Map | 防同任务多连接 |
| AbortController | abortControllerRef | 主动断开取消请求 |

### 5.4 续传配合

前端记录 `lastSequence`，重连时带 `?after_sequence=lastSequence`，与后端 `stream_events` 的排空过滤配合，实现断线后不丢事件。

## 6. 异步边界与数据一致性

### 6.1 三个并发协程

```
协程1: _execute_agent_task        # 产生事件
协程2: SSE stream 端点            # 消费队列事件
协程3: (可能多个) SSE /events     # 轮询 DB 事件
```

通过 `EventManager` 的 `asyncio.Queue`（协程1→协程2）和 `AgentEvent` 表（协程1→协程3）解耦。

### 6.2 事件顺序保证

- sequence 单调递增（EventManager 维护）
- 队列：FIFO，顺序保证
- DB：按 sequence 排序查询，顺序保证
- **跨边界**：thinking_token 不落 DB，仅队列；若 SSE 从队列切到 DB 回退，thinking_token 丢失（设计如此，非 bug）

### 6.3 至少一次语义

- DB 持久化事件：至少一次（落库后即使队列丢失，DB 轮询可补）
- 队列实时事件：可能丢失（断线超 5000 积压，或任务结束队列移除后连接）
- thinking_token：最多一次（不落 DB，断线即丢，可接受——思考过程非关键数据）

## 7. 已知风险点

| 风险 | 位置 | 影响 |
|------|------|------|
| 队列积压超 5000 | stream_events L511 | 排空阶段截断，超出事件丢失 |
| 5 分钟空闲超时 | /events L1926 | 长审计任务可能被误断 |
| thinking_token 不落 DB | add_event L306 | 历史回放无思考过程 |
| 多端点并存 | /events vs /stream | 两套机制，维护成本高 |
| 任务结束队列移除 | remove_queue | 之后连接的 SSE 只能 DB 轮询 |

## 8. 关键代码位置索引

| 组件 | 位置 |
|------|------|
| 任务执行协程 | agent_tasks.py:449 |
| Orchestrator 构造 | agent_tasks.py:463 |
| 事件发射器创建 | event_manager.py:create_emitter |
| add_event（DB+队列） | event_manager.py:269 |
| stream_events（队列流） | event_manager.py:486 |
| /events 端点（DB 轮询） | agent_tasks.py:1904 |
| /stream 端点（队列优先） | agent_tasks.py:1994 |
| 前端弹性流 | useResilientStream.ts:57 |
| 前端 SSE 解析 | agentStream.ts:110 |
