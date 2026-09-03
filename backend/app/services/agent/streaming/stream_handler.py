"""
流式事件处理器
处理 LangGraph 的各种流式事件并转换为前端可消费的格式
"""

import json
import logging
from enum import Enum
from typing import Any, Dict, Optional, AsyncGenerator, List
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class StreamEventType(str, Enum):
    """流式事件类型"""
    # 🔥 LLM 思考相关 - 这些是最重要的！展示 LLM 的大脑活动
    LLM_START = "llm_start"                # LLM 开始思考
    LLM_THOUGHT = "llm_thought"            # LLM 思考内容 ⭐ 核心
    LLM_DECISION = "llm_decision"          # LLM 决策 ⭐ 核心
    LLM_ACTION = "llm_action"              # LLM 动作
    LLM_OBSERVATION = "llm_observation"    # LLM 观察结果
    LLM_COMPLETE = "llm_complete"          # LLM 完成
    
    # LLM Token 流 (实时输出)
    THINKING_START = "thinking_start"      # 开始思考
    THINKING_TOKEN = "thinking_token"      # 思考 Token (流式)
    THINKING_END = "thinking_end"          # 思考结束
    # structured-output-protocol Task 4：正文流独立事件（与思考流分离）
    CONTENT_TOKEN = "content_token"        # 正文 Token (流式)
    CONTENT_END = "content_end"            # 正文流结束（带全文，落库兜底）
    
    # 工具调用相关 - LLM 决定调用工具
    TOOL_CALL_START = "tool_call_start"    # 工具调用开始
    TOOL_CALL_INPUT = "tool_call_input"    # 工具输入参数
    TOOL_CALL_OUTPUT = "tool_call_output"  # 工具输出结果
    TOOL_CALL_END = "tool_call_end"        # 工具调用结束
    TOOL_CALL_ERROR = "tool_call_error"    # 工具调用错误
    
    # 节点相关
    NODE_START = "node_start"              # 节点开始
    NODE_END = "node_end"                  # 节点结束
    
    # 阶段相关
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    
    # 发现相关
    FINDING_NEW = "finding_new"            # 新发现
    FINDING_VERIFIED = "finding_verified"  # 验证通过
    
    # 状态相关
    PROGRESS = "progress"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    
    # 任务相关
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_ERROR = "task_error"
    TASK_CANCEL = "task_cancel"
    
    # 心跳
    HEARTBEAT = "heartbeat"


@dataclass
class StreamEvent:
    """流式事件"""
    event_type: StreamEventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sequence: int = 0
    
    # 可选字段
    node_name: Optional[str] = None
    phase: Optional[str] = None
    tool_name: Optional[str] = None
    
    def to_sse(self) -> str:
        """转换为 SSE 格式"""
        event_data = {
            "type": self.event_type.value,
            "data": self.data,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
        }
        
        if self.node_name:
            event_data["node"] = self.node_name
        if self.phase:
            event_data["phase"] = self.phase
        if self.tool_name:
            event_data["tool"] = self.tool_name
        
        return f"event: {self.event_type.value}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "node_name": self.node_name,
            "phase": self.phase,
            "tool_name": self.tool_name,
        }


class StreamHandler:
    """
    流式事件处理器
    
    最佳实践:
    1. 使用 astream_events 捕获所有 LangGraph 事件
    2. 将内部事件转换为前端友好的格式
    3. 支持多种事件类型的分发
    """
    
    def __init__(self, task_id: str):
        self.task_id = task_id
        self._sequence = 0
        self._current_phase = None
        self._current_node = None
        self._thinking_buffer = []
        self._tool_states: Dict[str, Dict] = {}
    
    def _next_sequence(self) -> int:
        """获取下一个序列号"""
        self._sequence += 1
        return self._sequence
    
    async def process_langgraph_event(self, event: Dict[str, Any]) -> Optional[StreamEvent]:
        """
        处理 LangGraph 事件
        
        支持的事件类型:
        - on_chain_start: 链/节点开始
        - on_chain_end: 链/节点结束
        - on_chain_stream: LLM Token 流
        - on_chat_model_start: 模型开始
        - on_chat_model_stream: 模型 Token 流
        - on_chat_model_end: 模型结束
        - on_tool_start: 工具开始
        - on_tool_end: 工具结束
        - on_custom_event: 自定义事件
        """
        event_kind = event.get("event", "")
        event_name = event.get("name", "")
        event_data = event.get("data", {})
        
        # LLM Token 流
        if event_kind == "on_chat_model_stream":
            return await self._handle_llm_stream(event_data, event_name)
        
        # LLM 开始
        elif event_kind == "on_chat_model_start":
            return await self._handle_llm_start(event_data, event_name)
        
        # LLM 结束
        elif event_kind == "on_chat_model_end":
            return await self._handle_llm_end(event_data, event_name)
        
        # 工具开始
        elif event_kind == "on_tool_start":
            return await self._handle_tool_start(event_name, event_data)
        
        # 工具结束
        elif event_kind == "on_tool_end":
            return await self._handle_tool_end(event_name, event_data)
        
        # 节点开始
        elif event_kind == "on_chain_start" and self._is_node_event(event_name):
            return await self._handle_node_start(event_name, event_data)
        
        # 节点结束
        elif event_kind == "on_chain_end" and self._is_node_event(event_name):
            return await self._handle_node_end(event_name, event_data)
        
        # 自定义事件
        elif event_kind == "on_custom_event":
            return await self._handle_custom_event(event_name, event_data)
        
        return None
    
    def _is_node_event(self, name: str) -> bool:
        """判断是否是节点事件"""
        node_names = ["recon", "analysis", "verification", "report", "ReconNode", "AnalysisNode", "VerificationNode", "ReportNode"]
        return any(n.lower() in name.lower() for n in node_names)
    
    async def _handle_llm_start(self, data: Dict, name: str) -> StreamEvent:
        """处理 LLM 开始事件"""
        self._thinking_buffer = []
        
        return StreamEvent(
            event_type=StreamEventType.THINKING_START,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            data={
                "model": name,
                "message": "🤔 正在思考...",
            },
        )
    
    async def _handle_llm_stream(self, data: Dict, name: str) -> Optional[StreamEvent]:
        """处理 LLM Token 流事件"""
        chunk = data.get("chunk")
        if not chunk:
            return None
        
        # 提取 Token 内容
        content = ""
        if hasattr(chunk, "content"):
            content = chunk.content
        elif isinstance(chunk, dict):
            content = chunk.get("content", "")
        
        if not content:
            return None
        
        # 添加到缓冲区
        self._thinking_buffer.append(content)
        
        return StreamEvent(
            event_type=StreamEventType.THINKING_TOKEN,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            data={
                "token": content,
                "accumulated": "".join(self._thinking_buffer),
            },
        )
    
    async def _handle_llm_end(self, data: Dict, name: str) -> StreamEvent:
        """处理 LLM 结束事件"""
        full_response = "".join(self._thinking_buffer)
        self._thinking_buffer = []
        
        # 提取使用的 Token 数
        usage = {}
        output = data.get("output")
        if output and hasattr(output, "usage_metadata"):
            usage = {
                "input_tokens": getattr(output.usage_metadata, "input_tokens", 0),
                "output_tokens": getattr(output.usage_metadata, "output_tokens", 0),
            }
        
        return StreamEvent(
            event_type=StreamEventType.THINKING_END,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            data={
                "response": full_response[:2000],  # 截断长响应
                "usage": usage,
                "message": "💡 思考完成",
            },
        )
    
    async def _handle_tool_start(self, tool_name: str, data: Dict) -> StreamEvent:
        """处理工具开始事件"""
        import time
        
        tool_input = data.get("input", {})
        
        # 记录工具状态
        self._tool_states[tool_name] = {
            "start_time": time.time(),
            "input": tool_input,
        }
        
        return StreamEvent(
            event_type=StreamEventType.TOOL_CALL_START,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            tool_name=tool_name,
            data={
                "tool_name": tool_name,
                "input": self._truncate_data(tool_input),
                "message": f"🔧 调用工具: {tool_name}",
            },
        )
    
    async def _handle_tool_end(self, tool_name: str, data: Dict) -> StreamEvent:
        """处理工具结束事件"""
        import time
        
        # 计算执行时间
        duration_ms = 0
        if tool_name in self._tool_states:
            start_time = self._tool_states[tool_name].get("start_time", time.time())
            duration_ms = int((time.time() - start_time) * 1000)
            del self._tool_states[tool_name]
        
        # 提取输出
        output = data.get("output", "")
        if hasattr(output, "content"):
            output = output.content
        
        return StreamEvent(
            event_type=StreamEventType.TOOL_CALL_END,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            tool_name=tool_name,
            data={
                "tool_name": tool_name,
                "output": self._truncate_data(output),
                "duration_ms": duration_ms,
                "message": f"✅ 工具 {tool_name} 完成 ({duration_ms}ms)",
            },
        )
    
    async def _handle_node_start(self, node_name: str, data: Dict) -> StreamEvent:
        """处理节点开始事件"""
        self._current_node = node_name
        
        # 映射节点到阶段
        phase_map = {
            "recon": "reconnaissance",
            "analysis": "analysis",
            "verification": "verification",
            "report": "reporting",
        }
        
        for key, phase in phase_map.items():
            if key in node_name.lower():
                self._current_phase = phase
                break
        
        return StreamEvent(
            event_type=StreamEventType.NODE_START,
            sequence=self._next_sequence(),
            node_name=node_name,
            phase=self._current_phase,
            data={
                "node": node_name,
                "phase": self._current_phase,
                "message": f"▶️ 开始节点: {node_name}",
            },
        )
    
    async def _handle_node_end(self, node_name: str, data: Dict) -> StreamEvent:
        """处理节点结束事件"""
        # 提取输出信息
        output = data.get("output", {})
        
        summary = {}
        if isinstance(output, dict):
            # 提取关键信息
            if "findings" in output:
                summary["findings_count"] = len(output["findings"])
            if "entry_points" in output:
                summary["entry_points_count"] = len(output["entry_points"])
            if "high_risk_areas" in output:
                summary["high_risk_areas_count"] = len(output["high_risk_areas"])
            if "verified_findings" in output:
                summary["verified_count"] = len(output["verified_findings"])
        
        return StreamEvent(
            event_type=StreamEventType.NODE_END,
            sequence=self._next_sequence(),
            node_name=node_name,
            phase=self._current_phase,
            data={
                "node": node_name,
                "phase": self._current_phase,
                "summary": summary,
                "message": f"⏹️ 节点完成: {node_name}",
            },
        )
    
    async def _handle_custom_event(self, event_name: str, data: Dict) -> StreamEvent:
        """处理自定义事件"""
        # 映射自定义事件名到事件类型
        event_type_map = {
            "finding": StreamEventType.FINDING_NEW,
            "finding_verified": StreamEventType.FINDING_VERIFIED,
            "progress": StreamEventType.PROGRESS,
            "warning": StreamEventType.WARNING,
            "error": StreamEventType.ERROR,
        }
        
        event_type = event_type_map.get(event_name, StreamEventType.INFO)
        
        return StreamEvent(
            event_type=event_type,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            data=data,
        )
    
    def _truncate_data(self, data: Any, max_length: int = 1000) -> Any:
        """截断数据"""
        if isinstance(data, str):
            return data[:max_length] + "..." if len(data) > max_length else data
        elif isinstance(data, dict):
            return {k: self._truncate_data(v, max_length // 2) for k, v in list(data.items())[:10]}
        elif isinstance(data, list):
            return [self._truncate_data(item, max_length // len(data)) for item in data[:10]]
        else:
            return str(data)[:max_length]
    
    def create_progress_event(
        self,
        current: int,
        total: int,
        message: Optional[str] = None,
    ) -> StreamEvent:
        """创建进度事件"""
        percentage = (current / total * 100) if total > 0 else 0
        
        return StreamEvent(
            event_type=StreamEventType.PROGRESS,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            data={
                "current": current,
                "total": total,
                "percentage": round(percentage, 1),
                "message": message or f"进度: {current}/{total}",
            },
        )
    
    def create_finding_event(
        self,
        finding: Dict[str, Any],
        is_verified: bool = False,
    ) -> StreamEvent:
        """创建发现事件"""
        event_type = StreamEventType.FINDING_VERIFIED if is_verified else StreamEventType.FINDING_NEW
        
        return StreamEvent(
            event_type=event_type,
            sequence=self._next_sequence(),
            node_name=self._current_node,
            phase=self._current_phase,
            data={
                "title": finding.get("title", "Unknown"),
                "severity": finding.get("severity", "medium"),
                "vulnerability_type": finding.get("vulnerability_type", "other"),
                "file_path": finding.get("file_path"),
                "line_start": finding.get("line_start"),
                "is_verified": is_verified,
                "message": f"{'✅ 已验证' if is_verified else '🔍 新发现'}: [{finding.get('severity', 'medium').upper()}] {finding.get('title', 'Unknown')}",
            },
        )
    
    def create_heartbeat(self) -> StreamEvent:
        """创建心跳事件"""
        return StreamEvent(
            event_type=StreamEventType.HEARTBEAT,
            sequence=self._sequence,  # 心跳不增加序列号
            data={"message": "ping"},
        )

