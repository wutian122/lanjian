"""
结构化输出支持（方案 8）

支持 OpenAI Function Calling 和 Anthropic Tool Use
强制 LLM 返回符合 schema 的 JSON，彻底解决格式错误问题
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """LLM 提供商"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE = "azure"
    OTHER = "other"


# ---------------------------------------------------------------------------
# 多后端能力探测（structured-output-protocol 层次 1）
#
# 对 OpenAI 兼容端点（SGLang/vLLM/ollama）发两个轻量探测请求：
#   ① 假 tools 定义 → 响应是否含 tool_calls（原生 function calling）
#   ② 最小 json_schema → 输出是否为严格合法 JSON（guided decoding）
# 探测失败/超时 → 对应能力 False（不抛出），Agent 循环降级 ReAct 文本协议（零破坏）。
# 结果按 (base_url, model) 进程内缓存，同服务不重复探测。
# ---------------------------------------------------------------------------

#: guided JSON 两种字段形态：OpenAI/SGLang/vLLM 用 response_format；ollama 用 format
GUIDED_STYLE_RESPONSE_FORMAT = "response_format_json_schema"
GUIDED_STYLE_FORMAT = "format_json_schema"

_PROBE_TIMEOUT_SECONDS = 5.0
_PROBE_TOOL_NAME = "capability_probe"

_PROBE_TOOL: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": _PROBE_TOOL_NAME,
            "description": "Capability probe. Call this function exactly once with answer=1.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
            },
        },
    }
]

_PROBE_TOOL_MESSAGES = [
    {
        "role": "user",
        "content": (
            "Call the function capability_probe now with answer=1. "
            "Do not output any other content."
        ),
    }
]

# guided 探测用的最小 schema：能 json.loads 且含 bool 字段即视为严格遵循
_PROBE_GUIDED_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}

_PROBE_GUIDED_MESSAGES = [
    {
        "role": "user",
        "content": 'Reply with exactly this JSON object and nothing else: {"ok": true}',
    }
]


@dataclass
class BackendCapabilities:
    """推理后端能力探测结果"""

    tools: bool = False
    guided_json: bool = False
    #: "response_format_json_schema" | "format_json_schema" | None
    guided_style: Optional[str] = None
    probed_at: Optional[str] = None  # ISO8601 UTC 探测时间
    elapsed_ms: Optional[int] = None
    base_url_host: Optional[str] = None
    backend_hint: Optional[str] = None  # "ollama" | "openai_compatible"
    #: 探测本身失败（超时/异常/无 base_url）时的原因；后端"不支持"不算失败
    probe_error: Optional[str] = None

    def capabilities_summary(self) -> Dict[str, Any]:
        """任务事件流可追溯摘要（Task 9 参数摘要事件复用）"""
        return {
            "tools": self.tools,
            "guided_json": self.guided_json,
            "guided_style": self.guided_style,
            "probed_at": self.probed_at,
            "elapsed_ms": self.elapsed_ms,
            "base_url_host": self.base_url_host,
            "backend_hint": self.backend_hint,
            "probe_error": self.probe_error,
        }


# 进程内缓存：(归一化 base_url, model) -> 探测结果
_BACKEND_CAPABILITIES_CACHE: Dict[Tuple[str, str], BackendCapabilities] = {}


def _reset_capabilities_cache() -> None:
    """清空能力缓存（测试用）"""
    _BACKEND_CAPABILITIES_CACHE.clear()


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _backend_hint(base_url: str) -> str:
    url_lower = base_url.lower()
    # 11434 为 ollama 默认端口；关键词 ollama 覆盖非默认端口部署
    if "ollama" in url_lower or ":11434" in url_lower:
        return "ollama"
    return "openai_compatible"


def _response_message(response: Any) -> Any:
    try:
        return response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return None


def _probe_response_has_tool_call(response: Any) -> bool:
    """判定 tools 探测响应：含名为 capability_probe 的 tool_call 才算支持"""
    message = _response_message(response)
    if message is None:
        return False
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return False
    for call in tool_calls:
        function = getattr(call, "function", None)
        if function is not None and getattr(function, "name", None) == _PROBE_TOOL_NAME:
            return True
    return False


def _probe_response_is_strict_json(response: Any) -> bool:
    """判定 guided 探测响应：正文可 json.loads 且符合最小 schema（含 bool ok 字段）"""
    message = _response_message(response)
    if message is None:
        return False
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        return False
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and isinstance(data.get("ok"), bool)


async def _probe_tools(client: Any, model: str) -> Tuple[bool, Optional[str]]:
    """探测①：带假 tools 定义的小请求，看响应是否含 tool_calls。

    Returns (能力是否可用, 失败原因)——失败原因仅在能力为 False 时填充，
    供外层区分"后端不支持"与"探测本身失败（超时/异常）"。
    """
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": _PROBE_TOOL_MESSAGES,
        # 预算需容纳思考模型的 reasoning 前缀（实测 Qwen3 thinking 先输出
        # 数十 reasoning token 再发 tool_call），过小会 finish_reason=length
        # 导致 tool_calls 缺失而误判不支持
        "max_tokens": 256,
        "temperature": 0,
        "tools": _PROBE_TOOL,
    }
    try:
        _apply_thinking_guard(kwargs)
        response = await client.chat.completions.create(**kwargs)
        if _probe_response_has_tool_call(response):
            return True, None
        return False, None
    except Exception as exc:
        reason = _transport_failure_reason("tools", exc)
        logger.info(
            f"[CapabilityProbe] tools 探测失败（视为不支持）: "
            f"{type(exc).__name__}: {exc}"
        )
        return False, reason


async def _probe_guided_json(client: Any, model: str) -> Tuple[Optional[str], Optional[str]]:
    """探测②：最小 json_schema 约束，看输出是否严格合法 JSON。

    先试 OpenAI/SGLang/vLLM 风格 response_format；报错再试 ollama 风格
    format（经 extra_body 合并进 body 顶层）。成功返回 (style, None)，
    均失败返回 (None, 失败原因)。
    """
    base_kwargs: Dict[str, Any] = {
        "model": model,
        "messages": _PROBE_GUIDED_MESSAGES,
        # 同上：思考模型 reasoning 前缀实测 36 token，预算过小会 content=null、
        # finish_reason=length 而误判 guided 不可用
        "max_tokens": 512,
        "temperature": 0,
    }

    rf_kwargs = dict(base_kwargs)
    rf_kwargs["response_format"] = {
        "type": "json_schema",
        "json_schema": {"name": "capability_probe", "schema": _PROBE_GUIDED_SCHEMA},
    }
    rf_error: Optional[str] = None
    try:
        _apply_thinking_guard(rf_kwargs)
        response = await client.chat.completions.create(**rf_kwargs)
        if _probe_response_is_strict_json(response):
            return GUIDED_STYLE_RESPONSE_FORMAT, None
    except Exception as exc:
        rf_error = _transport_failure_reason("response_format", exc)
        logger.info(
            f"[CapabilityProbe] response_format 探测失败，尝试 ollama format 形态: "
            f"{type(exc).__name__}: {exc}"
        )

    fmt_kwargs = dict(base_kwargs)
    fmt_kwargs["extra_body"] = {"format": _PROBE_GUIDED_SCHEMA}
    try:
        _apply_thinking_guard(fmt_kwargs)
        response = await client.chat.completions.create(**fmt_kwargs)
        if _probe_response_is_strict_json(response):
            return GUIDED_STYLE_FORMAT, None
    except Exception as exc:
        fmt_error = _transport_failure_reason("format", exc)
        logger.info(
            f"[CapabilityProbe] format 形态探测失败（视为不支持 guided）: "
            f"{type(exc).__name__}: {exc}"
        )
        transport_errors = [e for e in (rf_error, fmt_error) if e]
        if transport_errors:
            return None, "guided_json: " + "；".join(transport_errors)
        return None, None

    # 两形态都未报错但输出不合法：后端忽略了约束，视为不支持（非探测失败）
    return None, None


def _apply_thinking_guard(kwargs: Dict[str, Any]) -> None:
    """探测请求经关思考护栏（Task 2）：探测只发思考模式请求，任何关思考参数/标记
    在出站前剥除。护栏函数在 litellm_adapter，惰性导入避免 agent→llm 导入环敏感。"""
    try:
        from app.services.llm.adapters.litellm_adapter import _assert_no_thinking_off

        _assert_no_thinking_off(kwargs, source="probe_backend_capabilities")
    except ImportError:
        # 护栏缺失时探测请求本身也不含任何思考开关，保持安全默认
        pass


def _transport_failure_reason(prefix: str, exc: Exception) -> Optional[str]:
    """探测失败原因归类：超时/连接失败（探测本身失败，spec Scenario 3）返回原因；
    4xx 等后端明确应答（后端可达但不支持，spec Scenario 2）返回 None。"""
    try:
        import openai

        is_transport = isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError))
    except ImportError:
        is_transport = False
    if not is_transport:
        return None
    return f"{prefix}: {type(exc).__name__}: {exc}"


async def probe_backend_capabilities(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> BackendCapabilities:
    """对 OpenAI 兼容端点发两个并发轻量探测，返回能力结果。

    任何失败/超时都不抛出：对应能力记 False，probe_error 记录原因，
    调用方据此降级 ReAct 文本协议。
    """
    caps = BackendCapabilities(
        base_url_host=urlparse(base_url).netloc,
        backend_hint=_backend_hint(base_url),
    )
    started = time.monotonic()
    try:
        import openai

        client = openai.AsyncOpenAI(
            api_key=api_key or "dummy",
            base_url=base_url,
            timeout=timeout,
            # Task 6 承接项：SDK 默认 max_retries=2，死后端每次重试叠加超时
            # （实测 15-30s 延迟）；探测本身已有缓存与降级，即时失败由外层兜底
            max_retries=0,
        )
        tools_task = asyncio.create_task(_probe_tools(client, model))
        guided_task = asyncio.create_task(_probe_guided_json(client, model))
        (tools_ok, tools_err), (guided_style, guided_err) = await asyncio.gather(
            tools_task, guided_task
        )
        caps.tools = tools_ok
        caps.guided_style = guided_style
        caps.guided_json = guided_style is not None
        # 两项能力均不可用且探测请求本身报错（超时/连接失败/4xx）→ 记为探测失败
        # （spec Scenario 3：发 warning）；后端正常响应但不支持时 probe_error 留空。
        if not caps.tools and not caps.guided_json:
            errors = [e for e in (tools_err, guided_err) if e]
            if errors:
                caps.probe_error = "能力探测请求失败（" + "；".join(errors) + "）"
    except Exception as exc:
        # 客户端构造失败/并发调度异常等：全 False 降级，不抛出
        caps.tools = False
        caps.guided_json = False
        caps.guided_style = None
        caps.probe_error = f"{type(exc).__name__}: {exc}"
        logger.warning(f"[CapabilityProbe] 能力探测异常，降级文本协议: {caps.probe_error}")

    caps.elapsed_ms = int((time.monotonic() - started) * 1000)
    caps.probed_at = datetime.now(timezone.utc).isoformat()
    return caps


async def get_backend_capabilities(
    service: Any,
    *,
    force_refresh: bool = False,
) -> BackendCapabilities:
    """能力探测入口：从 LLMService/LLMConfig 取 base_url/model/api_key，
    按 (base_url, model) 进程内缓存（同服务不重复探测）。

    永不抛出：无 base_url 或探测异常均返回全 False 的 BackendCapabilities。
    """
    config = getattr(service, "config", service)
    base_url = getattr(config, "base_url", None)
    model = getattr(config, "model", "") or ""
    api_key = getattr(config, "api_key", "") or ""

    if not base_url:
        # litellm 默认端点（无自定义 base_url）不探测：保守降级文本协议
        return BackendCapabilities(
            probe_error="未配置 base_url，跳过能力探测（降级 ReAct 文本协议）",
            probed_at=datetime.now(timezone.utc).isoformat(),
        )

    cache_key = (_normalize_base_url(base_url), model)
    if not force_refresh and cache_key in _BACKEND_CAPABILITIES_CACHE:
        return _BACKEND_CAPABILITIES_CACHE[cache_key]

    caps = await probe_backend_capabilities(base_url, api_key, model)
    _BACKEND_CAPABILITIES_CACHE[cache_key] = caps
    return caps


# Orchestrator 决策的 JSON Schema
ORCHESTRATOR_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {
            "type": "string",
            "description": "当前思考和分析"
        },
        "action": {
            "type": "string",
            "enum": ["dispatch_agent", "finish", "summarize"],
            "description": "要执行的动作"
        },
        "action_input": {
            "type": "object",
            "description": "动作的输入参数",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "要调度的 Agent 名称（dispatch_agent 时必填）"
                },
                "agents": {
                    "type": "array",
                    "description": "批量调度的 Agent 列表（dispatch_agent 批量模式）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {"type": "string"},
                            "task": {"type": "string"},
                            "context": {"type": "string"}
                        },
                        "required": ["agent", "task"]
                    }
                },
                "task": {
                    "type": "string",
                    "description": "任务描述"
                },
                "context": {
                    "type": "string",
                    "description": "任务上下文"
                },
                "conclusion": {
                    "type": "string",
                    "description": "审计结论（finish 时必填）"
                },
                "findings": {
                    "type": "array",
                    "description": "发现的漏洞列表",
                    "items": {"type": "object"}
                },
                "recommendations": {
                    "type": "array",
                    "description": "修复建议",
                    "items": {"type": "string"}
                }
            }
        }
    },
    "required": ["thought", "action", "action_input"]
}


# OpenAI Function Calling 格式
OPENAI_FUNCTION_DEFINITION = {
    "name": "make_decision",
    "description": "做出下一步决策：调度 Agent、完成审计或生成摘要",
    "parameters": ORCHESTRATOR_DECISION_SCHEMA
}


# Anthropic Tool Use 格式
ANTHROPIC_TOOL_DEFINITION = {
    "name": "make_decision",
    "description": "做出下一步决策：调度 Agent、完成审计或生成摘要",
    "input_schema": ORCHESTRATOR_DECISION_SCHEMA
}


class StructuredOutputAdapter:
    """结构化输出适配器"""

    def __init__(
        self,
        provider: LLMProvider = LLMProvider.OTHER,
        capabilities: Optional[BackendCapabilities] = None,
    ):
        self.provider = provider
        # 能力探测结果（structured-output-protocol 层次 1）：挂载后 is_supported
        # 以实测能力为准；为 None 时保留旧静态白名单行为（向后兼容）。
        self.capabilities = capabilities

    def is_supported(self) -> bool:
        """检查当前后端是否支持结构化输出（原生 tools 或 guided JSON 任一即可）"""
        if self.capabilities is not None:
            return self.capabilities.tools or self.capabilities.guided_json
        return self.provider in [LLMProvider.OPENAI, LLMProvider.ANTHROPIC, LLMProvider.AZURE]

    def build_structured_messages(
        self,
        conversation_history: List[Dict[str, str]],
        provider: Optional[LLMProvider] = None
    ) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        构建结构化输出的消息格式

        Returns:
            (messages, tools/functions) - 返回消息列表和工具定义
        """
        provider = provider or self.provider

        if provider == LLMProvider.OPENAI or provider == LLMProvider.AZURE:
            # OpenAI Function Calling
            return conversation_history, {
                "functions": [OPENAI_FUNCTION_DEFINITION],
                "function_call": {"name": "make_decision"}
            }

        elif provider == LLMProvider.ANTHROPIC:
            # Anthropic Tool Use
            return conversation_history, {
                "tools": [ANTHROPIC_TOOL_DEFINITION],
                "tool_choice": {"type": "tool", "name": "make_decision"}
            }

        else:
            # 不支持的 provider，返回原始消息
            return conversation_history, None

    def parse_structured_response(
        self,
        response: Any,
        provider: Optional[LLMProvider] = None
    ) -> Optional[Dict[str, Any]]:
        """
        解析结构化输出的响应

        Args:
            response: LLM 原始响应
            provider: LLM 提供商

        Returns:
            解析后的决策字典，包含 thought, action, action_input
        """
        provider = provider or self.provider

        try:
            if provider == LLMProvider.OPENAI or provider == LLMProvider.AZURE:
                return self._parse_openai_function_call(response)
            elif provider == LLMProvider.ANTHROPIC:
                return self._parse_anthropic_tool_use(response)
            else:
                return None
        except Exception as e:
            logger.error(f"[StructuredOutput] 解析失败: {e}")
            return None

    def _parse_openai_function_call(self, response: Any) -> Optional[Dict[str, Any]]:
        """解析 OpenAI Function Calling 响应"""
        # OpenAI 响应格式：
        # {
        #   "choices": [{
        #     "message": {
        #       "function_call": {
        #         "name": "make_decision",
        #         "arguments": "{...}"
        #       }
        #     }
        #   }]
        # }

        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                function_call = message.get("function_call", {})
                if function_call:
                    arguments_str = function_call.get("arguments", "{}")
                    try:
                        arguments = json.loads(arguments_str)
                        return arguments
                    except json.JSONDecodeError as e:
                        logger.error(f"[StructuredOutput] JSON 解析失败: {e}")
                        return None

        return None

    def _parse_anthropic_tool_use(self, response: Any) -> Optional[Dict[str, Any]]:
        """解析 Anthropic Tool Use 响应"""
        # Anthropic 响应格式：
        # {
        #   "content": [{
        #     "type": "tool_use",
        #     "name": "make_decision",
        #     "input": {...}
        #   }]
        # }

        if isinstance(response, dict):
            content = response.get("content", [])
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    if item.get("name") == "make_decision":
                        return item.get("input", {})

        return None

    def fallback_to_text_parsing(self, text_response: str) -> Optional[Dict[str, Any]]:
        """
        降级方案：从文本响应中解析（当结构化输出失败时）

        这是原有的正则解析逻辑，作为备用
        """
        # 这里可以调用原有的 _parse_llm_response 方法
        # 作为降级方案
        return None


def detect_provider_from_model(model_name: str) -> LLMProvider:
    """从模型名称推断 provider"""
    model_lower = model_name.lower()

    if "gpt" in model_lower or "openai" in model_lower:
        return LLMProvider.OPENAI
    elif "claude" in model_lower or "anthropic" in model_lower:
        return LLMProvider.ANTHROPIC
    elif "azure" in model_lower:
        return LLMProvider.AZURE
    else:
        return LLMProvider.OTHER


def add_structured_output_hint_to_prompt(system_prompt: str) -> str:
    """
    在系统提示词中添加结构化输出的提示

    当不支持原生结构化输出时，通过提示词引导 LLM 输出 JSON
    """
    hint = """

## 输出格式要求

你的每次响应都必须是一个有效的 JSON 对象，包含以下字段：

```json
{
  "thought": "你的思考过程",
  "action": "dispatch_agent | finish | summarize",
  "action_input": {
    // 根据 action 类型填写相应参数
  }
}
```

**重要**：只输出 JSON，不要包含任何其他文字。
"""
    return system_prompt + hint
