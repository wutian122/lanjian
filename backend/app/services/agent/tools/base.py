"""
Agent 工具基类
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type
from dataclasses import dataclass, field
from pydantic import BaseModel
import logging
import time

logger = logging.getLogger(__name__)


# ============ P9-3: 映射参数形状防御 ============

# 引导错误中的类型中文名（未命中时回退 type(...).__name__）
_TYPE_CN_LABELS = {
    str: "字符串(str)",
    list: "列表(list)",
    tuple: "元组(tuple)",
    int: "数字(int)",
    float: "数字(float)",
    bool: "布尔(bool)",
}


def coerce_mapping_arg(value: Any, field_name: str) -> Optional[Dict[str, Any]]:
    """把工具入参规整为 dict（映射参数形状防御，P9-3）。

    - None → None（字段未提供，由调用方按"缺省"语义处理）
    - dict → 原样返回
    - str → strip 后仅当以 ``{`` 开头时尝试 json.loads；成功且为 dict 返回，
      其余情况（JSON 数组/标量/坏文本）一律 None
    - list/其他类型 → None

    注意：返回 None 同时表示"未提供"与"形状非法"，调用方须先判断原始值
    是否为 None，再据此区分（见 build_mapping_arg_error 的配套用法）。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                return None
            if isinstance(parsed, dict):
                return parsed
        return None
    return None


def build_mapping_arg_error(field_name: str, value: Any) -> str:
    """构造映射参数的结构化引导错误（P9-3）。

    文案特征与 R1 保持一致（含「参数示例」），_enhance_missing_arg_error
    开头短路据此识别、不叠加第二份指引。
    """
    type_label = _TYPE_CN_LABELS.get(type(value), type(value).__name__)
    return (
        f"{field_name} 必须是 JSON 对象，收到 {type_label}。"
        '参数示例: {"k": "v"}'
    )


def normalize_string_mapping(mapping: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """把 dict 的 key/value 统一 str 化（值类型 str 化策略）。

    LLM 偶发发送 ``{"a": 1}``（值非字符串），下游 wrapper 插值与
    ``_analyze_output`` 的 ``value.lower()`` 会抛 AttributeError。
    统一在此转换：key 恒为 str（JSON 对象天然如此），非 str 值 str() 化。
    """
    if mapping is None:
        return None
    return {
        str(k): (v if isinstance(v, str) else str(v))
        for k, v in mapping.items()
    }


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }
    
    def to_string(self, max_length: int = 5000) -> str:
        """转换为字符串（用于 LLM 输出）"""
        if not self.success:
            return f"Error: {self.error}"
        
        if isinstance(self.data, str):
            result = self.data
        elif isinstance(self.data, (dict, list)):
            import json
            result = json.dumps(self.data, ensure_ascii=False, indent=2)
        else:
            result = str(self.data)
        
        if len(result) > max_length:
            result = result[:max_length] + f"\n... (truncated, total {len(result)} chars)"
        
        return result


class AgentTool(ABC):
    """
    Agent 工具基类
    所有工具需要继承此类并实现必要的方法
    """
    
    def __init__(self):
        self._call_count = 0
        self._total_duration_ms = 0
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述（用于 Agent 理解工具功能）"""
        pass
    
    @property
    def args_schema(self) -> Optional[Type[BaseModel]]:
        """参数 Schema（Pydantic 模型）"""
        return None
    
    @abstractmethod
    async def _execute(self, **kwargs) -> ToolResult:
        """执行工具（子类实现）"""
        pass
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具（带计时和日志）"""
        start_time = time.time()

        # R1: 通用必填参数校验 —— args_schema 声明的必填字段缺失时返回结构化错误，
        # 避免下游 _execute(**kwargs) 抛 TypeError: missing 1 required positional argument。
        # P9-2：pydantic v2 FieldInfo 无 .required 属性（旧自省恒判"非必填"），
        # 改以 is_required() 为权威、.required（v1 ModelField）回退。
        schema = self.args_schema
        if schema is not None:
            try:
                model_fields = getattr(schema, "model_fields", None)
                if model_fields is None:
                    # pydantic v1 回退
                    model_fields = getattr(schema, "__fields__", None) or {}
                for fname, finfo in model_fields.items():
                    required_checker = getattr(finfo, "is_required", None)
                    if callable(required_checker):
                        required = bool(required_checker())
                    else:
                        required = bool(getattr(finfo, "required", False))
                    # key 存在即放行（即便值为 None/空串）：显式传值与"缺字段"
                    # 语义不同，空值由 _execute 内部业务校验处理。
                    if required and fname not in kwargs:
                        result = ToolResult(
                            success=False,
                            error=(
                                f"必填参数缺失: {fname}。"
                                "工具调用参数必须是完整 JSON 对象，包含全部必需字段，"
                                "禁止空参数或省略字段。"
                                f'参数示例: {{"{fname}": "<实际值>", ...}}。'
                                "若无法确定取值，请先用 list_files/search_code 获取上下文。"
                            ),
                        )
                        duration_ms = int((time.time() - start_time) * 1000)
                        result.duration_ms = duration_ms
                        self._call_count += 1
                        self._total_duration_ms += duration_ms
                        return result
            except Exception:
                # 校验逻辑本身异常则 fallback 到原执行流程（自省异常放行）
                logger.debug(
                    f"R1 schema introspection failed for tool '{self.name}'",
                    exc_info=True,
                )

        try:
            logger.debug(f"Tool '{self.name}' executing with args: {kwargs}")
            result = await self._execute(**kwargs)

        except Exception as e:
            logger.error(f"Tool '{self.name}' error: {e}", exc_info=True)
            error_msg = str(e)
            result = ToolResult(
                success=False,
                data=f"工具执行异常: {error_msg}",  # 🔥 修复：设置 data 字段避免 None
                error=error_msg,
            )
        
        duration_ms = int((time.time() - start_time) * 1000)
        result.duration_ms = duration_ms
        
        self._call_count += 1
        self._total_duration_ms += duration_ms
        
        logger.debug(f"Tool '{self.name}' completed in {duration_ms}ms, success={result.success}")
        
        return result
    
    @property
    def stats(self) -> Dict[str, Any]:
        """工具使用统计"""
        return {
            "name": self.name,
            "call_count": self._call_count,
            "total_duration_ms": self._total_duration_ms,
            "avg_duration_ms": self._total_duration_ms // max(1, self._call_count),
        }

