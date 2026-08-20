# Agent 子系统 — lanjian Multi-Agent 核心

LLM 驱动的多智能体代码安全审计系统。*这是整个项目的核心引擎。*

## 架构概览

```
agent/
├── agents/                # Agent 实现层（4 个 Agent）
│   ├── base.py            # BaseAgent 基类（ReAct 循环、工具执行、事件发射、知识注入、TaskHandoff）
│   ├── orchestrator.py    # 编排 Agent（LLM 自主决策循环，调度子 Agent）
│   ├── recon.py           # 侦察 Agent（项目结构、技术栈、入口点、高风险区域）
│   ├── analysis.py        # 分析 Agent（外部工具扫描 + LLM 智能深度审计）
│   └── verification.py    # 验证 Agent（PoC 生成、Docker 沙箱执行、自修正）
├── core/                  # Agent 运行时基础设施（19 个文件）
│   ├── circuit_breaker.py # 熔断器（CLOSED→OPEN→HALF_OPEN，连续失败 10 次触发）
│   ├── rate_limiter.py    # 令牌桶限流
│   ├── retry.py           # 指数退避重试
│   ├── fallback.py        # Agent 失败降级策略
│   ├── registry.py        # Agent 注册表
│   ├── executor.py        # SubAgentExecutor（子 Agent 执行器，最大并行 5 个）
│   ├── context.py         # 上下文管理窗口
│   ├── state.py           # 分布式状态管理（Redis）
│   ├── persistence.py     # 审计结果持久化
│   ├── validation.py      # 输出校验
│   ├── message.py         # Agent 间消息协议（MessageType、MessagePriority）
│   ├── graph_controller.py # LangGraph 工作流编排
│   ├── orchestrator_registry.py # OrchestratorRegistry（Redis 存活心跳 `lanjian:orch:{task_id}`，5s 刷新/60s TTL，Redis 不可用降级进程内）
│   ├── coverage.py        # CoverageMatrix（D1-D10 覆盖率矩阵）
│   ├── cross_round.py     # CrossRoundContext（多轮审计上下文传递）
│   ├── attack_chain.py    # 攻击链分析
│   ├── logging.py         # 结构化审计日志
│   ├── errors.py          # Agent 异常类型
│   └── __init__.py
├── tools/                 # Agent 工具集（40+ 工具）
│   ├── base.py            # AgentTool 基类（name、description、_execute）
│   ├── agent_tools.py     # 工具注册 + 路由
│   ├── rag_tool.py        # RAG 工具（rag_query、security_search、function_context）
│   ├── file_tool.py       # 文件操作（read_file、search_code、list_files）
│   ├── code_analysis_tool.py # AST 代码分析（tree-sitter）
│   ├── pattern_tool.py    # 漏洞模式匹配
│   ├── smart_scan_tool.py # 智能批量安全扫描
│   ├── sandbox_tool.py    # Docker 沙箱执行（SandboxManager、SandboxHttpTool）
│   ├── sandbox_vuln.py    # 漏洞验证沙箱
│   ├── sandbox_language.py # 多语言沙箱支持（Python/Node/Java/Go/Ruby/PHP/Shell）
│   ├── run_code.py        # 代码执行
│   ├── external_tools.py  # 外部安全工具（Semgrep/Bandit/Gitleaks/npm audit/Safety/OSV/TruffleHog）
│   ├── reporting_tool.py  # 审计报告生成
│   ├── thinking_tool.py   # LLM 思考链工具（ThinkTool、ReflectTool）
│   ├── finish_tool.py     # Agent 任务终结
│   └── __init__.py
├── knowledge/             # 知识库系统
│   ├── base.py            # KnowledgeDocument 数据结构
│   ├── loader.py          # KnowledgeLoader（加载和搜索）
│   ├── rag_knowledge.py   # RAG 知识检索
│   ├── tools.py           # 知识查询工具
│   ├── frameworks/        # 框架安全知识（FastAPI、Django、Flask、Express、React、Supabase）
│   └── vulnerabilities/   # 漏洞类型知识（13 个文件）
│       ├── injection.py       # SQL/NoSQL/命令/代码注入
│       ├── xss.py             # 反射型/存储型/DOM XSS
│       ├── auth.py            # 认证绕过/IDOR/访问控制
│       ├── ssrf.py            # SSRF
│       ├── crypto.py          # 弱加密/硬编码密钥
│       ├── csrf.py            # CSRF
│       ├── deserialization.py # 不安全反序列化
│       ├── path_traversal.py  # 路径遍历
│       ├── xxe.py             # XXE
│       ├── race_condition.py  # 竞态条件
│       ├── business_logic.py  # 业务逻辑/速率限制
│       ├── open_redirect.py   # 开放重定向
│       └── ssti.py            # 服务端模板注入（SSTI）
├── prompts/               # 系统提示词模板
│   ├── system_prompts.py  # 多 Agent 规则、核心安全原则、工具使用指南
│   └── __init__.py
├── streaming/             # SSE 流式输出
│   ├── stream_handler.py  # LangGraph 事件转换
│   ├── token_streamer.py  # Token 流式输出
│   └── tool_stream.py     # 工具调用流
├── telemetry/             # 审计追踪
│   └── tracer.py          # Tracer（Agent 创建、工具执行、漏洞报告、持久化到文件）
├── agent_contract.py      # Agent 合约（Turn 预留、截断防御、探索上界）
├── config.py              # Agent 全局配置（AgentConfig，环境变量覆盖）
├── coverage.py            # D1-D10 安全维度覆盖率矩阵
├── event_manager.py       # SSE 事件管理器（asyncio.Queue + 心跳保活）
├── json_parser.py         # LLM JSON 输出修复解析器
├── round_strategy.py      # 增量补漏轮次策略
└── strict_finding.py      # 严格 Finding 过滤（排除无效 findings）
```

## 核心 Agent 职责

| Agent | 角色 | 输入 | 输出 | 最大轮次 | 超时 |
|-------|------|------|------|---------|------|
| **Orchestrator** | 编排决策 | 审计任务 + 项目上下文 | 子 Agent 调度指令 | 20 | 7200s |
| **Recon** | 信息收集 | 项目仓库 | 技术栈、入口点、攻击面、推荐工具 | 15 | 1800s |
| **Analysis** | 漏洞发现 | 代码 + 攻击面 | 漏洞列表（含置信度） | 45 | 1800s |
| **Verification** | PoC 验证 | 漏洞报告 | PoC 脚本 + 验证结果 | 20 | 1800s |

## 审计流程（ReAct 编排）

```
Task → Orchestrator（制定计划）
     → Recon（扫描项目，提取攻击面）
         输出：tech_stack, entry_points, recommended_tools, high_risk_areas
     → Orchestrator（评估结果，调度 Analysis）
     → Analysis（深度审计，发现漏洞）
         第二优先级：智能扫描工具（smart_scan、quick_audit）
         第三优先级：内置分析工具（pattern_match、dataflow_analysis）
         输出：findings[]（漏洞列表）
     → Orchestrator（评估漏洞，决定验证）
         **强制规则：有 findings 时必须调度 verification**
     → Verification（生成 PoC，沙箱执行）
         ├── 成功 → 确认漏洞（CONFIRMED）
         ├── 失败 → 自修正重试（最多 3 次）
         └── 无法复现 → NOT_REPRODUCIBLE
     → Orchestrator（汇总报告，判断完成）
         覆盖率门禁：D1-D10 covered >= 8 且 D1/D2/D3 必须覆盖
```

### Agent 间交互：TaskHandoff

Agent 间通过结构化 `TaskHandoff` 传递上下文：

```python
@dataclass
class TaskHandoff:
    from_agent: str          # 来源 Agent
    to_agent: str            # 目标 Agent
    summary: str             # 工作摘要
    work_completed: List[str]  # 已完成工作
    key_findings: List[Dict]   # 关键发现
    insights: List[str]        # 洞察
    suggested_actions: List[Dict]  # 建议行动
    attention_points: List[str]    # 关注点
    priority_areas: List[str]      # 优先区域
    context_data: Dict[str, Any]   # 上下文数据
    confidence: float              # 置信度
```

## 工具集分类

| 类别 | 工具 | 用途 |
|------|------|------|
| **RAG** | `RAGQueryTool`、`SecurityCodeSearchTool`、`FunctionContextTool` | 语义搜索、安全搜索、函数上下文 |
| **文件** | `FileReadTool`、`FileSearchTool`、`ListFilesTool` | 读文件、搜索代码、列目录 |
| **模式匹配** | `PatternMatchTool` | 危险模式匹配 |
| **代码分析** | `CodeAnalysisTool`、`DataFlowAnalysisTool`、`VulnerabilityValidationTool` | AST 分析、数据流追踪 |
| **外部工具** | `SemgrepTool`、`BanditTool`、`GitleaksTool`、`NpmAuditTool`、`SafetyTool`、`OSVScannerTool`、`TruffleHogTool` | 专业安全扫描 |
| **沙箱** | `SandboxTool`、`SandboxHttpTool`、`VulnerabilityVerifyTool` | Docker 沙箱执行 |
| **多语言测试** | `PythonTestTool`、`PhpTestTool`、`JavaScriptTestTool`、`JavaTestTool`、`GoTestTool`、`RubyTestTool`、`ShellTestTool` | 多语言代码测试 |
| **漏洞专用** | `CommandInjectionTestTool`、`SqlInjectionTestTool`、`XssTestTool`、`PathTraversalTestTool`、`SstiTestTool`、`DeserializationTestTool` | 漏洞类型测试 |
| **智能扫描** | `SmartScanTool`、`QuickAuditTool` | 批量扫描、快速审计 |
| **思维** | `ThinkTool`、`ReflectTool` | LLM 思考和反思 |
| **报告** | `CreateVulnerabilityReportTool`、`FinishScanTool` | 报告生成 |
| **Agent 协作** | `CreateSubAgentTool`、`SendMessageTool`、`ViewAgentGraphTool` 等 | 动态创建子 Agent、通信 |
| **代码执行** | `RunCodeTool`、`ExtractFunctionTool` | 通用代码执行、函数提取 |

## 沙箱机制

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `image` | `wutian449/lanjian-sandbox:latest` | 沙箱镜像（含 Python/Node/Java/Go/Ruby/PHP/C++） |
| `memory_limit` | `512m` | 内存限制 |
| `cpu_limit` | `1.0` | CPU 限制 |
| `timeout` | `60s` | 执行超时 |
| `network_mode` | `none` | 网络模式（默认禁用） |
| `read_only` | `True` | 只读文件系统 |
| `user` | `1000:1000` | 非 root 用户 |
| `seccomp` | 白名单 | 默认拒绝，仅允许 ~200 个安全系统调用 |

安全特性：
- 网络隔离：默认 `network_disabled = true`，需要时临时切换 `bridge` 模式
- 代理环境清理：自动清除 `HTTP_PROXY` 等环境变量
- 临时文件清理：使用 `tempfile.TemporaryDirectory` 自动清理

## SSE 事件流

```
Agent 执行 → AgentEventEmitter.emit(event_data)
  → EventManager.add_event(task_id, sequence, ...)
    → asyncio.Queue.put(event)
      → stream_events(task_id) → AsyncGenerator → SSE 推送到前端
```

事件类型（27 种）：
- `task_*`（4 种）— task_start/task_complete/task_error/task_cancel
- `phase_*`（2 种）— phase_start/phase_complete
- `thinking`/`planning`/`decision`（3 种）— LLM 思考、规划、决策
- `tool_*`（3 种）— tool_call/tool_result/tool_error
- `rag_*`（2 种）— rag_query/rag_result
- `finding_*`（4 种）— finding_new/finding_update/finding_verified/finding_false_positive
- `sandbox_*`（4 种）— sandbox_start/sandbox_exec/sandbox_result/sandbox_error
- `progress`（1 种）— 进度更新
- `info`/`warning`/`error`/`debug`（4 种）— 日志级别
- `heartbeat` — 心跳保活（15s）

推送策略：初始排空 → 快速消费（最多 1000 个）→ 实时推送 → 积压检测（>100 时批量消费）

## 查询索引

| 任务 | 位置 | 备注 |
|------|------|------|
| Agent 配置 | `config.py` | 超时、模型、工具配置 |
| 工具注册 | `tools/agent_tools.py` | 工具路由和注册 |
| 添加新工具 | `tools/base.py` → 继承 BaseTool | 实现 `_execute()` 方法 |
| 添加新 Agent | `agents/base.py` → 继承 BaseAgent | 实现系统提示和决策逻辑 |
| 扩展知识库 | `knowledge/vulnerabilities/` | 每个文件一个漏洞类型 |
| Sandbox 执行 | `tools/sandbox_tool.py` | Docker 隔离执行，网络禁用 |
| SSE 流式 | `streaming/` + `event_manager.py` | 前端实时审计日志 |
| 审计遥测 | `telemetry/tracer.py` | Tracer 审计追踪器、全局实例管理 |
| Agent 提示词 | `prompts/` | 系统提示和规则定义 |
| 覆盖率检查 | `coverage.py` + `core/coverage.py` | D1-D10 覆盖率矩阵 |
| Agent 合约 | `agent_contract.py` | Turn 预留、截断防御 |
| 严格过滤 | `strict_finding.py` | 排除无效 findings |
| 跨轮次上下文 | `core/cross_round.py` | 多轮审计上下文传递 |

## 弹性模式

- **熔断器**: 连续 LLM 失败达到阈值 → 暂停 Agent 调用 → 逐步恢复
- **重试**: 指数退避重试（LLM API 瞬时故障）
- **速度限制**: 令牌桶算法限制 LLM API 调用频率
- **降级**: Agent 失败时的降级策略（跳过验证、降低审计深度等）
- **检查点**: Agent 状态持久化，支持断点恢复

## 依赖

- **无 LangChain/LangGraph 依赖**: 代码零 import（仅注释提及；langgraph 仅存在于过期 uv.lock）
- **LiteLLM**: 多 LLM 提供商统一接口
- **Docker SDK**: 沙箱容器管理
- **Redis**: Agent 状态持久化和任务队列
- **tree-sitter**: 多语言 AST 解析

## 关键配置常量（config.py）

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `orchestrator_max_iterations` | 25 | 配置默认值；实际构造时 orchestrator.py:220 硬编码 `max_iterations=20`，运行时以 20 为准 |
| `recon_max_iterations` | 15 | Recon 最大迭代 |
| `analysis_max_iterations` | 45 | Analysis 最大迭代 |
| `verification_max_iterations` | 20 | Verification 最大迭代 |
| `orchestrator_timeout_seconds` | 7200 | Orchestrator 超时（2 小时） |
| `sub_agent_timeout_seconds` | 1800 | 子 Agent 超时（30 分钟） |
| `tool_timeout_seconds` | 60 | 工具调用默认超时 |
| `llm_max_retries` | 3 | LLM 调用最大重试 |
| `circuit_breaker.failure_threshold` | 10 | 熔断器失败阈值 |
| `circuit_breaker.recovery_timeout` | 60s | 熔断器恢复超时 |
| `circuit_breaker.half_open_max_calls` | 3 | 半开状态最大调用数 |
| `per_finding_budget` | 8 | 单发现弹性验证预算（迭代次数） |

## 反模式

- **禁止绕过熔断器**: 所有 LLM 调用必须经过 `circuit_breaker`
- **禁止跳过工具注册**: 新工具必须在 `agent_tools.py` 注册
- **禁止硬编码提示词**: 提示词统一管理在 `prompts/` 目录
- **禁止 LLM 输出未经 `json_parser` 处理**: 所有结构化输出必须经过修复和校验
- **沙箱工具必须隔离网络**: `sandbox_tool.py` 中 `network_disabled=True`
- **有 findings 必须调度 verification**: 不允许跳过验证直接 finish
