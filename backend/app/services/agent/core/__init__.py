"""
蓝鉴 Agent 核心模块

包含Agent系统的基础组件：
- state: 增强的Agent状态管理
- registry: Agent注册表和动态Agent树管理
- message: Agent间通信机制
- executor: 动态Agent树执行器
- persistence: Agent状态持久化
- errors: 错误层级体系
- retry: 重试机制
- circuit_breaker: 熔断器
- context: 执行上下文和关联ID
- logging: 结构化日志
- validation: 输入验证
- rate_limiter: 速率限制
- fallback: 优雅降级
"""

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    CircuitStats,
    get_circuit,
    get_circuit_registry,
    get_llm_circuit,
    get_tool_circuit,
    with_circuit_breaker,
)
from .context import (
    ExecutionContext,
    ExecutionContextManager,
    create_context,
    get_correlation_id,
    get_current_agent,
    get_current_context,
    get_task_id,
    get_trace_path,
    pop_trace,
    push_trace,
    set_correlation_id,
    set_current_agent,
    set_task_id,
    traced,
    with_context,
)

# New production-grade modules
from .errors import (
    AgentCancelledError,
    AgentError,
    AgentIterationLimitError,
    AgentTimeoutError,
    CircuitOpenError,
    ErrorContext,
    ErrorSeverity,
    ExternalToolError,
    InputValidationError,
    InvalidStateTransitionError,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMContentFilterError,
    LLMContextLengthError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
    PathTraversalError,
    RecoveryStrategy,
    StateRecoveryError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
    get_recovery_strategy,
    get_retry_after,
    is_recoverable,
    wrap_exception,
)
from .executor import (
    DynamicAgentExecutor,
    ExecutionMode,
    ExecutionResult,
    ExecutionTask,
    SubAgentExecutor,
)
from .fallback import (
    FallbackAction,
    FallbackConfig,
    FallbackHandler,
    FallbackResult,
    configure_fallback,
    get_fallback_handler,
    with_fallback,
)
from .graph_controller import (
    AgentGraphController,
    agent_graph_controller,
    check_active_agents,
    cleanup_graph,
    collect_all_findings,
    get_agent_graph,
    send_user_message,
    stop_agent,
    stop_all_agents,
    stop_task_agents,
)
from .logging import (
    AgentLogger,
    LogLevel,
    configure_logging,
    get_logger,
    log_execution,
)
from .message import AgentMessage, MessageBus, MessagePriority, MessageType, message_bus
from .persistence import (
    AgentStatePersistence,
    CheckpointManager,
    agent_persistence,
    checkpoint_manager,
)
from .rate_limiter import (
    RateLimitContext,
    RateLimiterRegistry,
    SlidingWindowRateLimiter,
    TokenBucketRateLimiter,
    get_external_tool_rate_limiter,
    get_file_read_rate_limiter,
    get_llm_rate_limiter,
    get_rate_limiter,
    get_rate_limiter_registry,
    rate_limited,
)
from .registry import AgentRegistry, agent_registry
from .retry import (
    LLM_RETRY_CONFIG,
    NO_RETRY_CONFIG,
    TOOL_RETRY_CONFIG,
    BackoffStrategy,
    RetryConfig,
    RetryContext,
    RetryResult,
    retry_with_backoff,
    retry_with_result,
    with_retry,
)
from .state import AgentState, AgentStatus
from .validation import (
    AgentTaskInput,
    CodeAnalysisInput,
    ExternalToolInput,
    FileReadInput,
    FileSearchInput,
    PatternMatchInput,
    ToolInputValidator,
    sanitize_dict,
    sanitize_string,
    validate_file_extension,
    validate_file_size,
    validate_path,
)

__all__ = [
    # State
    "AgentState",
    "AgentStatus",
    # Registry
    "AgentRegistry",
    "agent_registry",
    # Message
    "AgentMessage",
    "MessageType",
    "MessagePriority",
    "MessageBus",
    "message_bus",
    # Executor
    "DynamicAgentExecutor",
    "SubAgentExecutor",
    "ExecutionTask",
    "ExecutionResult",
    "ExecutionMode",
    # Persistence
    "AgentStatePersistence",
    "CheckpointManager",
    "agent_persistence",
    "checkpoint_manager",
    # Graph Controller
    "AgentGraphController",
    "agent_graph_controller",
    "stop_agent",
    "stop_all_agents",
    "send_user_message",
    "get_agent_graph",
    "check_active_agents",
    "collect_all_findings",
    "cleanup_graph",
    # Errors
    "AgentError",
    "LLMError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "LLMConnectionError",
    "LLMAuthenticationError",
    "LLMContentFilterError",
    "LLMContextLengthError",
    "LLMInvalidResponseError",
    "ToolError",
    "ToolExecutionError",
    "ToolTimeoutError",
    "ToolNotFoundError",
    "ExternalToolError",
    "AgentCancelledError",
    "AgentTimeoutError",
    "AgentIterationLimitError",
    "StateRecoveryError",
    "InvalidStateTransitionError",
    "CircuitOpenError",
    "ValidationError",
    "InputValidationError",
    "PathTraversalError",
    "ErrorContext",
    "ErrorSeverity",
    "RecoveryStrategy",
    "is_recoverable",
    "get_retry_after",
    "get_recovery_strategy",
    "wrap_exception",
    # Retry
    "RetryConfig",
    "RetryResult",
    "BackoffStrategy",
    "retry_with_backoff",
    "retry_with_result",
    "with_retry",
    "RetryContext",
    "LLM_RETRY_CONFIG",
    "TOOL_RETRY_CONFIG",
    "NO_RETRY_CONFIG",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "CircuitStats",
    "CircuitBreakerRegistry",
    "get_circuit",
    "get_circuit_registry",
    "get_llm_circuit",
    "get_tool_circuit",
    "with_circuit_breaker",
    # Context
    "ExecutionContext",
    "ExecutionContextManager",
    "create_context",
    "get_current_context",
    "get_correlation_id",
    "set_correlation_id",
    "get_task_id",
    "set_task_id",
    "get_current_agent",
    "set_current_agent",
    "get_trace_path",
    "push_trace",
    "pop_trace",
    "with_context",
    "traced",
    # Logging
    "AgentLogger",
    "configure_logging",
    "get_logger",
    "log_execution",
    "LogLevel",
    # Validation
    "ToolInputValidator",
    "validate_path",
    "validate_file_extension",
    "validate_file_size",
    "sanitize_string",
    "sanitize_dict",
    "AgentTaskInput",
    "FileReadInput",
    "FileSearchInput",
    "CodeAnalysisInput",
    "PatternMatchInput",
    "ExternalToolInput",
    # Rate Limiter
    "TokenBucketRateLimiter",
    "SlidingWindowRateLimiter",
    "RateLimiterRegistry",
    "get_rate_limiter",
    "get_rate_limiter_registry",
    "get_llm_rate_limiter",
    "get_external_tool_rate_limiter",
    "get_file_read_rate_limiter",
    "rate_limited",
    "RateLimitContext",
    # Fallback
    "FallbackHandler",
    "FallbackConfig",
    "FallbackResult",
    "FallbackAction",
    "get_fallback_handler",
    "configure_fallback",
    "with_fallback",
]
