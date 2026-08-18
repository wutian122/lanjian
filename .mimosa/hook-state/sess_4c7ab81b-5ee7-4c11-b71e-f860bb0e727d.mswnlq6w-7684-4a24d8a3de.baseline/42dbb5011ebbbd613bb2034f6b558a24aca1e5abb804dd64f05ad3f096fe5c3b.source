"""
Agent 事件管理器
负责事件的创建、存储和推送
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any, List, AsyncGenerator, Callable
from datetime import datetime, timezone
from dataclasses import dataclass
import uuid

logger = logging.getLogger(__name__)


@dataclass
class AgentEventData:
    """Agent 事件数据"""
    event_type: str
    phase: Optional[str] = None
    message: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[Dict[str, Any]] = None
    tool_duration_ms: Optional[int] = None
    finding_id: Optional[str] = None
    tokens_used: int = 0
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "phase": self.phase,
            "message": self.message,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output,
            "tool_duration_ms": self.tool_duration_ms,
            "finding_id": self.finding_id,
            "tokens_used": self.tokens_used,
            "metadata": self.metadata,
        }


class AgentEventEmitter:
    """
    Agent 事件发射器
    用于在 Agent 执行过程中发射事件
    """
    
    def __init__(self, task_id: str, event_manager: 'EventManager'):
        self.task_id = task_id
        self.event_manager = event_manager
        self._sequence = 0
        self._current_phase = None
    
    async def emit(self, event_data: AgentEventData):
        """发射事件"""
        self._sequence += 1
        event_data.phase = event_data.phase or self._current_phase
        
        await self.event_manager.add_event(
            task_id=self.task_id,
            sequence=self._sequence,
            **event_data.to_dict()
        )
    
    async def emit_phase_start(self, phase: str, message: Optional[str] = None):
        """发射阶段开始事件"""
        self._current_phase = phase
        await self.emit(AgentEventData(
            event_type="phase_start",
            phase=phase,
            message=message or f"开始 {phase} 阶段",
        ))
    
    async def emit_phase_complete(self, phase: str, message: Optional[str] = None):
        """发射阶段完成事件"""
        await self.emit(AgentEventData(
            event_type="phase_complete",
            phase=phase,
            message=message or f"{phase} 阶段完成",
        ))
    
    async def emit_thinking(self, message: str, metadata: Optional[Dict] = None):
        """发射思考事件"""
        await self.emit(AgentEventData(
            event_type="thinking",
            message=message,
            metadata=metadata,
        ))
    
    async def emit_llm_thought(self, thought: str, iteration: int = 0):
        """发射 LLM 思考内容事件 - 核心！展示 LLM 在想什么"""
        display = thought[:500] + "..." if len(thought) > 500 else thought
        await self.emit(AgentEventData(
            event_type="llm_thought",
            message=f"💭 LLM 思考:\n{display}",
            metadata={"thought": thought, "iteration": iteration},
        ))
    
    async def emit_llm_decision(self, decision: str, reason: str = ""):
        """发射 LLM 决策事件"""
        await self.emit(AgentEventData(
            event_type="llm_decision",
            message=f"💡 LLM 决策: {decision}" + (f" ({reason})" if reason else ""),
            metadata={"decision": decision, "reason": reason},
        ))
    
    async def emit_llm_action(self, action: str, action_input: Dict):
        """发射 LLM 动作事件"""
        import json
        input_str = json.dumps(action_input, ensure_ascii=False)[:200]
        await self.emit(AgentEventData(
            event_type="llm_action",
            message=f"⚡ LLM 动作: {action}\n   参数: {input_str}",
            metadata={"action": action, "action_input": action_input},
        ))
    
    async def emit_tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        message: Optional[str] = None,
    ):
        """发射工具调用事件"""
        await self.emit(AgentEventData(
            event_type="tool_call",
            tool_name=tool_name,
            tool_input=tool_input,
            message=message or f"调用工具: {tool_name}",
        ))
    
    async def emit_tool_result(
        self,
        tool_name: str,
        tool_output: Any,
        duration_ms: int,
        message: Optional[str] = None,
    ):
        """发射工具结果事件"""
        # 处理输出，确保可序列化
        if hasattr(tool_output, 'to_dict'):
            output_data = tool_output.to_dict()
        elif isinstance(tool_output, str):
            output_data = {"result": tool_output[:2000]}  # 截断长输出
        else:
            output_data = {"result": str(tool_output)[:2000]}
        
        await self.emit(AgentEventData(
            event_type="tool_result",
            tool_name=tool_name,
            tool_output=output_data,
            tool_duration_ms=duration_ms,
            message=message or f"工具 {tool_name} 执行完成 ({duration_ms}ms)",
        ))
    
    async def emit_finding(
        self,
        finding_id: str,
        title: str,
        severity: str,
        vulnerability_type: str,
        is_verified: bool = False,
    ):
        """发射漏洞发现事件"""
        event_type = "finding_verified" if is_verified else "finding_new"
        await self.emit(AgentEventData(
            event_type=event_type,
            finding_id=finding_id,
            message=f"{'✅ 已验证' if is_verified else '🔍 新发现'}: [{severity.upper()}] {title}",
            metadata={
                "id": finding_id,  # 🔥 添加 id 字段供前端使用
                "title": title,
                "severity": severity,
                "vulnerability_type": vulnerability_type,
                "is_verified": is_verified,
            },
        ))
    
    async def emit_info(self, message: str, metadata: Optional[Dict] = None):
        """发射信息事件"""
        await self.emit(AgentEventData(
            event_type="info",
            message=message,
            metadata=metadata,
        ))
    
    async def emit_warning(self, message: str, metadata: Optional[Dict] = None):
        """发射警告事件"""
        await self.emit(AgentEventData(
            event_type="warning",
            message=message,
            metadata=metadata,
        ))
    
    async def emit_error(self, message: str, metadata: Optional[Dict] = None):
        """发射错误事件"""
        await self.emit(AgentEventData(
            event_type="error",
            message=message,
            metadata=metadata,
        ))
    
    async def emit_progress(
        self,
        current: int,
        total: int,
        message: Optional[str] = None,
    ):
        """发射进度事件"""
        percentage = (current / total * 100) if total > 0 else 0
        await self.emit(AgentEventData(
            event_type="progress",
            message=message or f"进度: {current}/{total} ({percentage:.1f}%)",
            metadata={
                "current": current,
                "total": total,
                "percentage": percentage,
            },
        ))
    
    async def emit_task_complete(
        self,
        findings_count: int,
        duration_ms: int,
        message: Optional[str] = None,
    ):
        """发射任务完成事件"""
        await self.emit(AgentEventData(
            event_type="task_complete",
            message=message or f"✅ 审计完成！发现 {findings_count} 个漏洞，耗时 {duration_ms/1000:.1f}秒",
            metadata={
                "findings_count": findings_count,
                "duration_ms": duration_ms,
            },
        ))
    
    async def emit_task_error(self, error: str, message: Optional[str] = None):
        """发射任务错误事件"""
        await self.emit(AgentEventData(
            event_type="task_error",
            message=message or f"❌ 任务失败: {error}",
            metadata={"error": error},
        ))
    
    async def emit_task_cancelled(self, message: Optional[str] = None):
        """发射任务取消事件"""
        await self.emit(AgentEventData(
            event_type="task_cancel",
            message=message or "⚠️ 任务已取消",
        ))


class EventManager:
    """
    事件管理器
    负责事件的存储和检索
    """

    # Wave 2 §3.3 事件队列有界化配置
    QUEUE_MAX_SIZE = 10000  # 单任务队列上限（覆盖 ~15 分钟正常任务事件量）
    IMPORTANT_PUT_TIMEOUT_SECONDS = 5.0  # 重要事件入队最长等待时间
    # Wave 2 Review Finding 1: 终态事件超时保护（30s）
    # 消费者永久离线时避免 Orchestrator 永久挂死；事件已落 DB，重连时回补可送达
    TERMINAL_PUT_TIMEOUT_SECONDS = 30.0
    TERMINAL_EVENT_TYPES = frozenset({"task_complete", "task_error", "task_cancel"})
    DROPPABLE_EVENT_TYPES = frozenset({"thinking_token"})  # 可安全丢弃的事件

    # Wave 2 §3.4 心跳配置
    HEARTBEAT_INTERVAL = 10  # 秒；前端 45s 心跳窗口 4.5x 余量

    def __init__(self, db_session_factory=None):
        self.db_session_factory = db_session_factory
        self._event_queues: Dict[str, asyncio.Queue] = {}
        self._event_callbacks: Dict[str, List[Callable]] = {}
        self._heartbeat_sent: Dict[str, bool] = {}  # task_id -> 本周期是否已发心跳
        self._terminal_tasks: set = set()  # 已终态的 task_id 集合
        # Wave 2 §3.3 分级丢弃计数器：追踪被丢弃的 thinking_token 数量
        self.dropped_thinking_tokens: Dict[str, int] = {}
    
    async def add_event(
        self,
        task_id: str,
        event_type: str,
        sequence: int = 0,
        phase: Optional[str] = None,
        message: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_input: Optional[Dict] = None,
        tool_output: Optional[Dict] = None,
        tool_duration_ms: Optional[int] = None,
        finding_id: Optional[str] = None,
        tokens_used: int = 0,
        metadata: Optional[Dict] = None,
    ):
        """添加事件"""
        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc)
        
        event_data = {
            "id": event_id,
            "task_id": task_id,
            "event_type": event_type,
            "sequence": sequence,
            "phase": phase,
            "message": message,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "tool_duration_ms": tool_duration_ms,
            "finding_id": finding_id,
            "tokens_used": tokens_used,
            "metadata": metadata,
            "timestamp": timestamp.isoformat(),
        }
        
        # 保存到数据库（跳过高频事件如 thinking_token）
        skip_db_events = {"thinking_token"}
        if self.db_session_factory and event_type not in skip_db_events:
            try:
                await self._save_event_to_db(event_data)
            except Exception as e:
                logger.error(f"Failed to save event to database: {e}")
        
        # Wave 2 §3.3 分级入队策略：
        # - 可丢弃事件（thinking_token）: put_nowait，QueueFull 直接丢弃并计数
        # - 终态事件（task_complete/task_error/task_cancel）: 无条件 await 直到成功
        # - 其他重要事件: await wait_for(put, 5s)，超时后放弃入队但已落 DB（DB 回补兜底）
        if task_id in self._event_queues:
            q = self._event_queues[task_id]

            if event_type in self.DROPPABLE_EVENT_TYPES:
                # 可丢弃事件：立即入队，满则丢弃（thinking_token 只是 UX 增强）
                try:
                    q.put_nowait(event_data)
                except asyncio.QueueFull:
                    self.dropped_thinking_tokens[task_id] = (
                        self.dropped_thinking_tokens.get(task_id, 0) + 1
                    )
                    dropped_total = self.dropped_thinking_tokens[task_id]
                    # 每 100 条 WARNING 一次，避免日志刷屏
                    if dropped_total % 100 == 0:
                        logger.warning(
                            f"[EventQueue] Dropped {dropped_total} thinking_token events for task {task_id} "
                            f"(queue full, size={q.qsize()}/{q.maxsize}); consumer likely slow or disconnected"
                        )
            elif event_type in self.TERMINAL_EVENT_TYPES:
                # 终态事件：MUST 送达；无消费者时会阻塞。为避免 Orchestrator 因消费者永久
                # 离线而永久挂死（Review Wave 2 Finding 1），加长超时保护：30 秒内还入不了队，
                # 则放弃入队。事件已落 DB（terminal 不在 skip_db_events），重连时 DB 回补
                # 可送达；同时打 CRITICAL 日志便于运维定位。
                try:
                    await asyncio.wait_for(
                        q.put(event_data), timeout=self.TERMINAL_PUT_TIMEOUT_SECONDS
                    )
                    logger.info(
                        f"[EventQueue] Added terminal event {event_type} to queue for task {task_id}, size: {q.qsize()}"
                    )
                except asyncio.TimeoutError:
                    logger.critical(
                        f"[EventQueue] Terminal event {event_type} timed out ({self.TERMINAL_PUT_TIMEOUT_SECONDS}s) "
                        f"waiting to enqueue for task {task_id} (size={q.qsize()}/{q.maxsize}); "
                        f"consumer offline. Event persisted in DB, will be delivered on next SSE reconnect."
                    )
            else:
                # 重要事件：最多等 5s。超时则放弃入队（DB 已落，重连时 DB 回补可拿到）
                try:
                    await asyncio.wait_for(
                        q.put(event_data), timeout=self.IMPORTANT_PUT_TIMEOUT_SECONDS
                    )
                    if event_type in {"thinking_start", "thinking_end", "dispatch",
                                      "tool_call", "tool_result", "llm_action"}:
                        logger.info(
                            f"[EventQueue] Added {event_type} to queue for task {task_id}, queue size: {q.qsize()}"
                        )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[EventQueue] Timed out (5s) waiting to enqueue {event_type} for task {task_id} "
                        f"(size={q.qsize()}/{q.maxsize}); event dropped from queue but persisted in DB"
                    )

        # 调用回调
        if task_id in self._event_callbacks:
            for callback in self._event_callbacks[task_id]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(event_data)
                    else:
                        callback(event_data)
                except Exception as e:
                    logger.error(f"Event callback error: {e}")
        
        # 🔥 FIX: 终态事件自动清理
        if event_type in ("task_complete", "task_error", "task_cancel"):
            self._terminal_tasks.add(task_id)
            async def _delayed_cleanup() -> None:
                await asyncio.sleep(30)
                self.remove_queue(task_id)
                self._heartbeat_sent.pop(task_id, None)
                self._terminal_tasks.discard(task_id)
                logger.info(f"[EventManager] Auto-cleaned queue for terminal task {task_id}")
            # P2-5: 后台任务加异常日志回调，避免异常静默丢失
            cleanup_task = asyncio.create_task(_delayed_cleanup())
            def _on_cleanup_done(t: asyncio.Task, _tid: str = task_id) -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc is not None:
                    logger.exception(
                        f"[EventManager] _delayed_cleanup for {_tid} raised",
                        exc_info=exc,
                    )
            cleanup_task.add_done_callback(_on_cleanup_done)
        
        return event_id
    
    async def _save_event_to_db(self, event_data: Dict):
        """保存事件到数据库"""
        from app.models.agent_task import AgentEvent

        # 🔥 清理无效的 UTF-8 字符（如二进制内容）
        def sanitize_string(s):
            """清理字符串中的无效 UTF-8 字符"""
            if s is None:
                return None
            if not isinstance(s, str):
                s = str(s)
            # 移除 NULL 字节和其他不可打印的控制字符（保留换行和制表符）
            return ''.join(
                char for char in s
                if char in '\n\r\t' or (ord(char) >= 32 and ord(char) != 127)
            )

        def sanitize_dict(d):
            """递归清理字典中的字符串值"""
            if d is None:
                return None
            if isinstance(d, dict):
                return {k: sanitize_dict(v) for k, v in d.items()}
            elif isinstance(d, list):
                return [sanitize_dict(item) for item in d]
            elif isinstance(d, str):
                return sanitize_string(d)
            return d

        async with self.db_session_factory() as db:
            event = AgentEvent(
                id=event_data["id"],
                task_id=event_data["task_id"],
                event_type=event_data["event_type"],
                sequence=event_data["sequence"],
                phase=event_data["phase"],
                message=sanitize_string(event_data["message"]),  # 🔥 清理消息
                tool_name=event_data["tool_name"],
                tool_input=sanitize_dict(event_data["tool_input"]),  # 🔥 清理工具输入
                tool_output=sanitize_dict(event_data["tool_output"]),  # 🔥 清理工具输出
                tool_duration_ms=event_data["tool_duration_ms"],
                finding_id=event_data["finding_id"],
                tokens_used=event_data["tokens_used"],
                event_metadata=sanitize_dict(event_data["metadata"]),  # 🔥 清理元数据
            )
            db.add(event)
            await db.commit()
    
    def create_queue(self, task_id: str) -> asyncio.Queue:
        """创建或获取事件队列（Wave 2 §3.3：有界队列 + 分级丢弃策略）"""
        if task_id not in self._event_queues:
            # Wave 2 §3.3：有界队列 maxsize=QUEUE_MAX_SIZE（10000），
            # add_event 按事件类型分级处理满队情况（可丢弃/重要/终态）
            self._event_queues[task_id] = asyncio.Queue(maxsize=self.QUEUE_MAX_SIZE)
        return self._event_queues[task_id]
    
    def remove_queue(self, task_id: str):
        """移除事件队列"""
        if task_id in self._event_queues:
            del self._event_queues[task_id]
    
    def add_callback(self, task_id: str, callback: Callable):
        """添加事件回调"""
        if task_id not in self._event_callbacks:
            self._event_callbacks[task_id] = []
        self._event_callbacks[task_id].append(callback)
    
    def remove_callback(self, task_id: str, callback: Callable):
        """移除事件回调"""
        if task_id in self._event_callbacks:
            self._event_callbacks[task_id].remove(callback)
    
    async def get_events(
        self,
        task_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        """获取事件列表"""
        if not self.db_session_factory:
            return []
        
        from sqlalchemy.future import select
        from app.models.agent_task import AgentEvent
        
        async with self.db_session_factory() as db:
            result = await db.execute(
                select(AgentEvent)
                .where(AgentEvent.task_id == task_id)
                .where(AgentEvent.sequence > after_sequence)
                .order_by(AgentEvent.sequence)
                .limit(limit)
            )
            events = result.scalars().all()
            return [event.to_sse_dict() for event in events]

    async def _backfill_events_paged(
        self,
        task_id: str,
        after_sequence: int,
        batch_size: int = 500,
        max_events: int = 20000,
    ) -> AsyncGenerator[Dict, None]:
        """分页拉取 DB 中 sequence > after_sequence 的事件并 yield。

        - 按 sequence 游标推进：每批 <= batch_size 条
        - 累计上限 max_events；达到时 yield notice/backfill_truncated 事件后 return
        - 事件字典中 'type' 字段统一转为 'event_type'（保持与内存事件格式一致）
        - 遇到终端事件（task_complete/task_error/task_cancel）立即 return
        - DB 异常时 yield 结束并让上层继续实时循环
        """
        cursor = after_sequence
        total = 0

        while True:
            try:
                batch = await self.get_events(task_id, after_sequence=cursor, limit=batch_size)
            except Exception as e:
                logger.warning(
                    f"[BackfillPaged] Task {task_id}: DB query failed, stopping backfill: {e}"
                )
                return

            if not batch:
                break

            for evt in batch:
                # 统一 'type' 字段为 'event_type'（保持与内存事件格式一致）
                if "type" in evt and "event_type" not in evt:
                    evt["event_type"] = evt["type"]

                # 达到累计上限保护，yield notice 事件后停止回补。
                # 注意：字段 `sent` 表示"已成功回补数"（= max_events），
                # 不是"被丢弃数"—— 后者需额外 COUNT 查询才能精确得知，
                # 前端可推断"至少还有更多事件未回补，需通过 after_sequence 手动拉取"。
                if total >= max_events:
                    yield {
                        "event_type": "notice",
                        "sequence": cursor,
                        "metadata": {
                            "kind": "backfill_truncated",
                            "sent": total,
                            "limit": max_events,
                            "after_sequence": cursor,
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    return

                total += 1
                yield evt

                # 推进游标
                seq = evt.get("sequence", 0)
                if seq > cursor:
                    cursor = seq

                # 遇到终端事件立即返回
                if evt.get("event_type") in ("task_complete", "task_error", "task_cancel"):
                    return

    async def stream_events(
        self,
        task_id: str,
        after_sequence: int = 0,
    ) -> AsyncGenerator[Dict, None]:
        """流式获取事件

        🔥 重要: 此方法会先排空队列中已缓存的事件（在 SSE 连接前产生的），
        然后继续实时推送新事件。
        只返回序列号 > after_sequence 的事件。
        """
        logger.info(f"[StreamEvents] Task {task_id}: Starting stream with after_sequence={after_sequence}")

        # 获取现有队列（由 AgentRunner 在初始化时创建）
        queue = self._event_queues.get(task_id)
        if not queue:
            # 如果队列不存在，创建一个新的（回退逻辑）
            queue = self.create_queue(task_id)
            logger.warning(f"Queue not found for task {task_id}, created new one")

        # 🔥 CRITICAL FIX v2: 持续排空直到队列真正为空
        # 之前的 bug: 使用 qsize() 取固定数量，排空期间生产者持续添加的新事件会被遗漏
        # 导致前端感知"加载了一次就卡住"
        buffered_count = 0
        skipped_count = 0
        MAX_DRAIN = 5000  # 安全上限，防止异常情况下无限循环
        
        logger.info(f"[StreamEvents] Task {task_id}: Draining buffered events (max={MAX_DRAIN})...")

        terminal_event_seen = False  # 🔥 标记是否遇到终端事件
        
        while buffered_count + skipped_count < MAX_DRAIN:
            try:
                buffered_event = queue.get_nowait()
            except asyncio.QueueEmpty:
                # 🔥 队列真正空了，退出排空阶段
                break

            # 🔥 过滤掉序列号 <= after_sequence 的事件
            event_sequence = buffered_event.get("sequence", 0)
            if event_sequence <= after_sequence:
                skipped_count += 1
                continue

            buffered_count += 1
            yield buffered_event

            # 🔥 检查是否是终端事件：发送后 break 而非 return，确保后续代码正常执行
            event_type = buffered_event.get("event_type")
            if event_type in ["task_complete", "task_error", "task_cancel"]:
                logger.info(f"[StreamEvents] Task {task_id}: Terminal event {event_type} encountered during drain")
                terminal_event_seen = True
                break

        if buffered_count > 0 or skipped_count > 0:
            logger.info(f"[StreamEvents] Task {task_id}: Drained {buffered_count} events, skipped {skipped_count}")

        # 🔥 如果遇到终端事件，不再进入实时循环
        if terminal_event_seen:
            logger.info(f"[StreamEvents] Task {task_id}: Terminal event seen, exiting stream")
            return

        # 🔥 DB 回补：重连时内存队列中可能已无事件，但 DB 中存在 after_sequence 之后的事件
        # 从 DB 分页查询断连期间未送达的事件，按序补发给前端（游标分页，支持 >500 条事件）
        if self.db_session_factory:
            try:
                db_backfill_count = 0
                async for db_evt in self._backfill_events_paged(
                    task_id, after_sequence=after_sequence
                ):
                    yield db_evt
                    db_backfill_count += 1
                    # 更新 after_sequence 以便后续实时循环不重复发送
                    seq = db_evt.get("sequence", 0)
                    if seq > after_sequence:
                        after_sequence = seq
                    # 检查终端事件
                    if db_evt.get("event_type") in ["task_complete", "task_error", "task_cancel"]:
                        logger.info(f"[StreamEvents] Task {task_id}: Terminal event during DB backfill, exiting")
                        return
                if db_backfill_count > 0:
                    logger.info(f"[StreamEvents] Task {task_id}: DB backfilled {db_backfill_count} events (after_sequence now={after_sequence})")
            except Exception as db_err:
                logger.warning(f"[StreamEvents] Task {task_id}: DB backfill failed (non-fatal): {db_err}")

        # 🔥 进入实时循环前，记录队列状态（DB 回补之后，因为回补期间生产者可能添加新事件）
        remaining = queue.qsize()
        logger.info(f"[StreamEvents] Task {task_id}: Entering real-time loop, remaining in queue: {remaining}")

        # 🔥 如果队列中仍有积压事件（排空期间生产者持续添加），快速消费一批
        if remaining > 0:
            fast_drain_count = 0
            while fast_drain_count < min(remaining, 1000):
                try:
                    fast_event = queue.get_nowait()
                    event_sequence = fast_event.get("sequence", 0)
                    if event_sequence <= after_sequence:
                        continue
                    fast_drain_count += 1
                    yield fast_event
                    
                    event_type = fast_event.get("event_type")
                    if event_type in ["task_complete", "task_error", "task_cancel"]:
                        logger.info(f"[StreamEvents] Task {task_id}: Terminal event during fast-drain")
                        return
                except asyncio.QueueEmpty:
                    break
            if fast_drain_count > 0:
                logger.info(f"[StreamEvents] Task {task_id}: Fast-drained {fast_drain_count} backlog events")

        # 然后实时推送新事件（不再过滤 after_sequence，因为初始排空阶段已处理）
        # Wave 2 §3.4: 心跳与队列消费解耦。用 asyncio.wait({queue_get, heartbeat_timer},
        # FIRST_COMPLETED) 模式：即使消费者慢，心跳也能按 10s 周期发出，不再依赖
        # queue.get 超时。
        try:
            while True:
                queue_get_task = asyncio.ensure_future(queue.get())
                heartbeat_task = asyncio.ensure_future(asyncio.sleep(self.HEARTBEAT_INTERVAL))
                try:
                    done, pending = await asyncio.wait(
                        {queue_get_task, heartbeat_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    # 上游取消：清理待处理任务后向上传播
                    for t in (queue_get_task, heartbeat_task):
                        if not t.done():
                            t.cancel()
                    raise

                if queue_get_task in done:
                    # 有新事件：yield 并取消心跳定时器（下次循环重开）
                    heartbeat_task.cancel()
                    event = queue_get_task.result()
                    yield event

                    # 检查是否是结束事件
                    if event.get("event_type") in ["task_complete", "task_error", "task_cancel"]:
                        break

                    # 🔥 积压检测：每次 yield 后检查队列是否有积压
                    backlog = queue.qsize()
                    if backlog > 100:
                        for _ in range(min(backlog, 500)):
                            try:
                                backlog_event = queue.get_nowait()
                                yield backlog_event

                                if backlog_event.get("event_type") in ["task_complete", "task_error", "task_cancel"]:
                                    logger.info(f"[StreamEvents] Task {task_id}: Terminal event during backlog drain")
                                    return
                            except asyncio.QueueEmpty:
                                break
                else:
                    # 心跳定时器先到：yield 心跳事件，取消 queue.get（它的结果本轮不用了）
                    queue_get_task.cancel()
                    # 记录 heartbeat_task 已 done 但结果无关紧要
                    logger.debug(f"[StreamEvents] Task {task_id}: Sending heartbeat (interval={self.HEARTBEAT_INTERVAL}s)")
                    yield {"event_type": "heartbeat", "timestamp": datetime.now(timezone.utc).isoformat()}

        except GeneratorExit:
            # SSE 连接断开
            logger.info(f"[StreamEvents] Task {task_id}: SSE stream closed by client (GeneratorExit)")
        except asyncio.CancelledError:
            # Wave 1 §2.4 修复：Starlette 内部取消或客户端断开会触发 CancelledError，
            # 必须显式捕获后 re-raise，让上层 StreamingResponse 正常清理。
            # 若不捕获，异常会未处理地传播到上层，可能导致 event_manager 队列引用泄漏。
            logger.info(f"[StreamEvents] Task {task_id}: Stream cancelled (client disconnect or task cancel)")
            raise
        except Exception as stream_err:
            logger.error(f"[StreamEvents] Task {task_id}: Stream error: {stream_err}", exc_info=True)
        # 🔥 不要移除队列，让 AgentRunner 管理队列的生命周期
    
    def create_emitter(self, task_id: str) -> AgentEventEmitter:
        """创建事件发射器"""
        return AgentEventEmitter(task_id, self)
    
    async def close(self):
        """关闭事件管理器，清理资源"""
        # 清理所有队列
        for task_id in list(self._event_queues.keys()):
            self.remove_queue(task_id)
        
        # 清理所有回调
        self._event_callbacks.clear()
        
        logger.debug("EventManager closed")

