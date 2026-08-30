"""
结构化输出支持（方案 8）

支持 OpenAI Function Calling 和 Anthropic Tool Use
强制 LLM 返回符合 schema 的 JSON，彻底解决格式错误问题
"""

import json
import logging
from typing import Any, Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """LLM 提供商"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE = "azure"
    OTHER = "other"


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

    def __init__(self, provider: LLMProvider = LLMProvider.OTHER):
        self.provider = provider

    def is_supported(self) -> bool:
        """检查当前 provider 是否支持结构化输出"""
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
