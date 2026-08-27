"""
蓝鉴 Agent 审计任务 API
基于 LangGraph 的 Agent 审计
"""

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.core.encryption import decrypt_sensitive_data
from app.core.rbac import (
    assert_can_access_project,
    build_agent_task_filter,
    get_subordinate_user_ids,
)
from app.core.timeutil import serialize_cst
from app.db.session import async_session_factory, get_db
from app.models.agent_task import (
    AgentCheckpoint,
    AgentEvent,
    AgentEventType,
    AgentFinding,
    AgentTask,
    AgentTaskPhase,
    AgentTaskStatus,
    FindingStatus,
    VulnerabilitySeverity,
)
from app.models.project import Project
from app.models.user import User
from app.services.agent.agents.base import AgentResult
from app.services.agent.event_manager import EventManager
from app.services.agent.strict_finding import is_strict_finding, _to_int
from app.services.agent.task_cleanup import cleanup_agent_task_resources
from app.services.git_ssh_service import GitSSHOperations
from app.services.llm.service import LLMService

logger = logging.getLogger(__name__)
router = APIRouter()

# 🔥 B5: 索引进度消息模板（分块阶段 vs 嵌入阶段分别标识，供用户区分当前所处阶段）
# 分块阶段进度：明确标识"分块"；嵌入阶段使用独立的"嵌入"标识。
CHUNK_PROGRESS_MSG_TEMPLATE = "📝 分块进度: {processed}/{total} 文件 ({pct:.0f}%)"
EMBED_PROGRESS_MSG_TEMPLATE = "🔢 嵌入进度: {processed}/{total} ({pct:.0f}%)"

# 运行中的任务（兼容旧接口）
_running_tasks: dict[str, Any] = {}

# 🔥 运行中的 asyncio Tasks（用于强制取消）
_running_asyncio_tasks: dict[str, asyncio.Task] = {}


# P2-5: 后台任务异常保护 —— fire-and-forget 的 asyncio.create_task 如果内部抛异常，
# 只留一条 "Task exception was never retrieved" warning 就静默丢失，SSE 客户端和
# 后端日志都收不到确切错误。用 _launch_task_bg 包装：
#   - 加 done_callback 打 logger.exception
#   - 保留强引用避免被 GC（asyncio.create_task 只弱引用 task 本身，coro 也可能被 GC）
_background_task_refs: set[asyncio.Task] = set()


def _launch_task_bg(coro, task_name: str) -> asyncio.Task:
    """
    fire-and-forget 地启动一个协程，异常时打 logger.exception。

    与 ``asyncio.create_task`` 的区别：
      - 保留强引用 —— 避免 asyncio.Task 被 GC 导致协程被静默中断；
      - 加 done_callback —— 任务完成后自动打异常日志并解引用。

    Args:
        coro: 要运行的协程。
        task_name: 异常日志里显示的任务名，便于定位。
    """
    task = asyncio.create_task(coro, name=task_name)
    _background_task_refs.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_task_refs.discard(t)
        if t.cancelled():
            logger.debug(f"[BgTask] {task_name} cancelled")
            return
        exc = t.exception()
        if exc is not None:
            logger.exception(
                f"[BgTask] {task_name} raised unhandled exception",
                exc_info=exc,
            )

    task.add_done_callback(_on_done)
    return task


# ============ Schemas ============

class AgentTaskCreate(BaseModel):
    """创建 Agent 任务请求"""
    project_id: str = Field(..., description="项目 ID")
    name: str | None = Field(None, description="任务名称")
    description: str | None = Field(None, description="任务描述")

    # 审计配置
    audit_scope: dict | None = Field(None, description="审计范围")
    target_vulnerabilities: list[str] | None = Field(
        default=["sql_injection", "xss", "command_injection", "path_traversal", "ssrf"],
        description="目标漏洞类型"
    )
    verification_level: str = Field(
        "sandbox",
        description="验证级别: analysis_only, sandbox, generate_poc"
    )

    # 分支
    branch_name: str | None = Field(None, description="分支名称")

    # 排除模式
    exclude_patterns: list[str] | None = Field(
        default=["node_modules", "__pycache__", ".git", "*.min.js"],
        description="排除模式"
    )

    # 文件范围
    target_files: list[str] | None = Field(None, description="指定扫描的文件")

    # Agent 配置
    max_iterations: int = Field(50, ge=1, le=200, description="最大迭代次数")
    timeout_seconds: int = Field(1800, ge=60, le=7200, description="超时时间（秒）")


class AgentTaskResponse(BaseModel):
    """Agent 任务响应 - 包含所有前端需要的字段"""
    id: str
    project_id: str
    name: str | None
    description: str | None
    task_type: str = "agent_audit"
    status: str
    paused: bool = False
    paused_at: datetime | None = None
    pause_reason: str | None = None
    last_error_code: str | None = None
    last_checkpoint_id: str | None = None
    resume_count: int = 0
    current_phase: str | None
    current_step: str | None = None

    # 进度统计
    total_files: int = 0
    indexed_files: int = 0
    analyzed_files: int = 0
    total_chunks: int = 0

    # Agent 统计
    total_iterations: int = 0
    tool_calls_count: int = 0
    tokens_used: int = 0

    # 发现统计（兼容两种命名）
    findings_count: int = 0
    total_findings: int = 0  # 兼容字段
    verified_count: int = 0
    static_confirmed_count: int = 0  # 代码推理确认数（未动态复现）
    verified_findings: int = 0  # 兼容字段
    false_positive_count: int = 0
    # Q1: 验证状态分布（confirmed/not_reproducible/needs_context/false_positive）
    verification_status_breakdown: dict | None = None

    # 严重程度统计
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0

    # 评分
    quality_score: float = 0.0
    security_score: float | None = None

    # 进度百分比
    progress_percentage: float = 0.0

    # 时间
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_serializer("created_at", "started_at", "completed_at", "paused_at", when_used="json")
    def _ser_time(self, dt: datetime | None) -> str | None:
        return serialize_cst(dt)

    # 配置
    audit_scope: dict | None = None
    target_vulnerabilities: list[str] | None = None
    verification_level: str | None = None
    exclude_patterns: list[str] | None = None
    target_files: list[str] | None = None

    # 错误信息
    error_message: str | None = None

    # Wave 2 §3.2: Orchestrator 存活心跳。True=后端进程在 30s 内刷新过 alive_at；
    # False=Redis 中键已过期或 orchestrator 崩溃/重启（stale running 判定依据）。
    # None=后端未启用 Redis registry（Redis 不可用时保持向后兼容）。
    orchestrator_alive: bool | None = None

    class Config:
        from_attributes = True


class AgentEventResponse(BaseModel):
    """Agent 事件响应"""
    id: str
    task_id: str
    event_type: str
    phase: str | None
    message: str | None = None
    sequence: int
    # 🔥 ORM 字段名是 created_at，序列化为 timestamp
    created_at: datetime = Field(serialization_alias="timestamp")

    @field_serializer("created_at", when_used="json")
    def _ser_event_time(self, dt: datetime) -> str:
        return serialize_cst(dt) or ""

    # 工具相关字段
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None
    tool_duration_ms: int | None = None

    # 其他字段
    progress_percent: float | None = None
    finding_id: str | None = None
    tokens_used: int | None = None
    # 🔥 ORM 字段名是 event_metadata，序列化为 metadata
    event_metadata: dict[str, Any] | None = Field(default=None, serialization_alias="metadata")

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "by_alias": True,  # 🔥 关键：确保序列化时使用别名
    }


class AgentFindingResponse(BaseModel):
    """Agent 发现响应"""
    id: str
    task_id: str
    vulnerability_type: str
    severity: str
    title: str
    description: str | None
    file_path: str | None
    line_start: int | None
    line_end: int | None
    code_snippet: str | None

    is_verified: bool
    # 🔥 FIX: Map from ai_confidence in ORM, make Optional with default
    confidence: float | None = Field(default=0.5, validation_alias="ai_confidence")
    status: str

    suggestion: str | None = None
    poc: dict | None = None
    sandbox_attempts: list[dict] | None = None
    verification_status: str | None = None
    verification_result: dict | None = None
    verification_method: str | None = None

    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser_finding_time(self, dt: datetime) -> str:
        return serialize_cst(dt) or ""

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,  # Allow both 'confidence' and 'ai_confidence'
    }


class TaskSummaryResponse(BaseModel):
    """任务摘要响应"""
    task_id: str
    status: str
    security_score: int | None

    total_findings: int
    verified_findings: int

    severity_distribution: dict[str, int]
    vulnerability_types: dict[str, int]

    duration_seconds: int | None
    phases_completed: list[str]


class AgentTaskChatRequest(BaseModel):
    """任务级 AI 协同请求"""
    message: str = Field(..., min_length=1, max_length=4000, description="用户在 AI 协同栏输入的消息")


class AgentTaskChatResponse(BaseModel):
    """任务级 AI 协同响应"""
    reply: str
    context_summary: dict[str, Any]
    usage: dict[str, int] | None = None


# ============ 后台任务执行 ============

# 运行中的动态执行器
_running_orchestrators: dict[str, Any] = {}
# 运行中的事件管理器（用于 SSE 流）
_running_event_managers: dict[str, EventManager] = {}
# 🔥 已取消的任务集合（用于前置操作的取消检查）
# P2-8: 从 set() 换成带 TTL 的 dict —— 旧版如果任务在标记为 cancelled 后从未走到
# _execute_agent_task 的 finally 分支（例如根本没启动就 cancel），task_id 会永远留在集合里，
# 长时间运行的进程内会累积成不可控的内存泄漏。这里给每个 entry 记录 added_at，
# 每次 is_task_cancelled 顺手扫掉超过 TTL 的条目（惰性清理，零后台线程）。
_CANCELLED_TASK_TTL_SECONDS = 24 * 3600  # 24h 足够任何合理的重试窗口
_cancelled_tasks: dict[str, float] = {}


def _cancelled_tasks_add(task_id: str) -> None:
    import time as _time
    _cancelled_tasks[task_id] = _time.monotonic()


def _cancelled_tasks_discard(task_id: str) -> None:
    _cancelled_tasks.pop(task_id, None)


def _cancelled_tasks_prune() -> None:
    """惰性清理：移除超过 TTL 的条目。"""
    import time as _time
    now = _time.monotonic()
    expired = [
        tid for tid, t in _cancelled_tasks.items()
        if now - t > _CANCELLED_TASK_TTL_SECONDS
    ]
    for tid in expired:
        _cancelled_tasks.pop(tid, None)

# SSE 流的终态状态集合：任务进入这些状态时，SSE 流推送 task_end 并断开。
# 注意：
# - paused 也纳入，使前端能感知暂停并展示"继续"入口（见 fix-pause-resume-rpm-isolation）。
# - completed_with_gaps 必须纳入（覆盖率不足/超时安全阀放行），否则 DB 轮询流不识别终态，
#   前端一直显示"运行中"直到 300s max_idle（见 fix-sse-realtime-stream Wave 0.3）。
# - initializing 明确不纳入：属于任务运行前置阶段，SSE 应保持连接推送初始化进度事件。
_SSE_TERMINAL_STATUSES: set[str] = {
    "completed",
    "completed_with_gaps",
    "failed",
    "cancelled",
    "paused",
}


def is_task_cancelled(task_id: str) -> bool:
    """检查任务是否已被取消"""
    # P2-8: 顺手做惰性 TTL 清理
    _cancelled_tasks_prune()
    return task_id in _cancelled_tasks


async def _re_audit_task(task_id: str, finding_ids: list[str]):
    """Bug E: Re-run verification only for specified unverified findings.

    Uses the existing _execute_agent_task resume mechanism by creating
    a checkpoint that instructs the Orchestrator to dispatch Verification
    for the specified findings.
    """
    logger.info(f"[ReAudit] Starting re-audit for task {task_id}, {len(finding_ids)} findings")
    try:
        async with async_session_factory() as db:
            task = await db.get(AgentTask, task_id)
            if not task:
                logger.error(f"[ReAudit] Task {task_id} not found")
                return

            # Load unverified findings from DB
            result = await db.execute(
                select(AgentFinding).where(AgentFinding.id.in_(finding_ids))
            )
            db_findings = result.scalars().all()

            if not db_findings:
                logger.info("[ReAudit] No findings to verify")
                task.status = AgentTaskStatus.COMPLETED
                await db.commit()
                return

            # Build finding summaries for the verification task
            finding_summaries = []
            for f in db_findings:
                finding_summaries.append(
                    f"{f.file_path or 'unknown'}:{f.line_start or 0} "
                    f"[{f.vulnerability_type}] {f.title or ''}"
                )
            finding_list_str = "\n".join(finding_summaries)

            # Create a checkpoint with re-audit instructions
            import json as _json
            resume_state = {
                "all_findings": [
                    {
                        "id": f.id,
                        "title": f.title,
                        "vulnerability_type": f.vulnerability_type,
                        "severity": f.severity,
                        "file_path": f.file_path,
                        "line_start": f.line_start,
                        "line_end": f.line_end,
                        "code_snippet": f.code_snippet,
                        "description": f.description,
                        "is_verified": False,
                        "verification_status": None,
                    }
                    for f in db_findings
                ],
                "_re_audit_mode": True,
                "_re_audit_finding_ids": finding_ids,
            }

            checkpoint = AgentCheckpoint(
                id=str(uuid4()),
                task_id=task_id,
                agent_id="re_audit",
                agent_name="ReAudit",
                agent_type="orchestrator",
                iteration=0,
                status="paused",
                total_tokens=0,
                tool_calls=0,
                findings_count=len(finding_ids),
                checkpoint_type="manual",
                checkpoint_name="re_audit",
                state_data=_json.dumps(resume_state, default=str),
                checkpoint_metadata={"re_audit_finding_ids": finding_ids, "resume_state": resume_state},
                created_at=datetime.now(UTC),
            )
            db.add(checkpoint)
            await db.flush()

            task.last_checkpoint_id = checkpoint.id
            task.status = AgentTaskStatus.RUNNING
            task.paused = False
            task.resume_count = int(getattr(task, "resume_count", 0) or 0) + 1
            await db.commit()

            checkpoint_id = checkpoint.id

        # Launch the standard execution with resume from checkpoint
        # P2-5: 用 _launch_task_bg 包装，异常自动打 logger.exception
        _launch_task_bg(
            _execute_agent_task(task_id, resume_checkpoint_id=checkpoint_id),
            task_name=f"reaudit-{task_id}",
        )
        logger.info(f"[ReAudit] Task {task_id} launched with checkpoint {checkpoint_id}")

    except Exception as e:
        logger.error(f"[ReAudit] Outer error: {e}", exc_info=True)
        async with async_session_factory() as db:
            task = await db.get(AgentTask, task_id)
            if task:
                task.status = AgentTaskStatus.COMPLETED_WITH_GAPS
                task.last_error_code = "re_audit_failed"
                await db.commit()

async def _execute_agent_task(task_id: str, resume_checkpoint_id: str | None = None):
    """
    在后台执行 Agent 任务 - 使用动态 Agent 树架构
    
    架构：OrchestratorAgent 作为大脑，动态调度子 Agent
    """
    import time

    from app.core.config import settings
    from app.services.agent.agents import (
        AgentExecutionPaused,
        AnalysisAgent,
        OrchestratorAgent,
        ReconAgent,
        VerificationAgent,
    )
    from app.services.agent.core import agent_registry
    from app.services.agent.event_manager import AgentEventEmitter, EventManager
    from app.services.agent.tools import SandboxManager
    from app.services.llm.service import LLMService
    async with async_session_factory() as _db:
        _task = await _db.get(AgentTask, task_id)
        if _task and (_task.paused or _task.status == AgentTaskStatus.PAUSED):
            logger.info(f"⏸️ Task {task_id} is paused, skip execution")
            return

    # 🔥 在任务最开始就初始化 Docker 沙箱管理器
    # 这样可以确保整个任务生命周期内使用同一个管理器，并且尽早发现 Docker 问题
    logger.info(f"🚀 Starting execution for task {task_id}")
    sandbox_manager = SandboxManager()

    # 修复"任务已断开"横幅在 RAG 索引阶段（10+ 分钟）误报：
    # _execute_agent_task 进入到 orchestrator.run() 之间可能长达 10+ 分钟
    # （下载 ZIP / 解压 / RAG 索引），期间没有心跳，Redis alive key 空 →
    # 前端 orchestrator_alive=false → 显示"任务已断开"。
    # 这里 spawn 一个 early heartbeat 协程，每 5s 刷 alive，orchestrator.run()
    # 里的正式心跳启动后我们 cancel 它，避免双写。
    _early_alive_task: asyncio.Task | None = None

    async def _early_pump_alive():
        """在 orchestrator 心跳启动前的兜底心跳。"""
        try:
            from app.services.agent.core.orchestrator_registry import get_registry
            registry = await get_registry()
        except Exception as e:
            logger.warning(f"[AgentTask] early heartbeat init failed for {task_id}: {e}")
            return
        while True:
            try:
                await asyncio.wait_for(
                    registry.set_alive(task_id, event_manager_local=True),
                    timeout=2.0,
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"[AgentTask] early heartbeat set_alive error for {task_id}: {e}")
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return

    try:
        _early_alive_task = asyncio.create_task(_early_pump_alive(), name=f"early-alive-{task_id}")
    except Exception as _spawn_err:
        logger.debug(f"[AgentTask] early heartbeat spawn failed for {task_id}: {_spawn_err}")
    await sandbox_manager.initialize()
    logger.info(f"🐳 Global Sandbox Manager initialized (Available: {sandbox_manager.is_available})")

    # 🔥 提前创建事件管理器，以便在克隆仓库和索引时发送实时日志
    event_manager = EventManager(db_session_factory=async_session_factory)
    event_manager.create_queue(task_id)
    event_emitter = AgentEventEmitter(task_id, event_manager)
    _running_event_managers[task_id] = event_manager

    async with async_session_factory() as db:
        orchestrator = None
        start_time = time.time()

        try:
            # 获取任务
            task = await db.get(AgentTask, task_id, options=[selectinload(AgentTask.project)])
            if not task:
                logger.error(f"Task {task_id} not found")
                return

            # 获取项目
            project = task.project
            if not project:
                logger.error(f"Project not found for task {task_id}")
                return

            # 🔥 发送任务开始事件 - 使用 phase_start 让前端知道进入准备阶段
            await event_emitter.emit_phase_start("preparation", f"🚀 任务开始执行: {project.name}")

            # Init progress: set status to INITIALIZING so frontend shows progress
            task.status = AgentTaskStatus.INITIALIZING
            await db.commit()
            await event_emitter.emit_info("Docker sandbox ready", metadata={"init_step": "Docker sandbox", "init_status": "done"})

            resume_state = None
            if resume_checkpoint_id:
                checkpoint = await db.get(AgentCheckpoint, resume_checkpoint_id)
                if not checkpoint or checkpoint.task_id != task_id:
                    raise RuntimeError("resume checkpoint not found")
                meta = checkpoint.checkpoint_metadata or {}
                if isinstance(meta, dict):
                    resume_state = meta.get("resume_state")

            task.status = AgentTaskStatus.RUNNING
            task.paused = False
            task.paused_at = None
            task.pause_reason = None
            task.last_error_code = None
            task.last_checkpoint_id = resume_checkpoint_id or task.last_checkpoint_id
            if not task.started_at:
                task.started_at = datetime.now(UTC)
            task.current_phase = AgentTaskPhase.PLANNING  # preparation 对应 PLANNING
            await db.commit()

            await db.refresh(task)
            if task.paused or task.status == AgentTaskStatus.PAUSED:
                logger.info(f"⏸️ Task {task_id} is paused, skip execution")
                return

            # 获取用户配置（需要在获取项目根目录之前，以便传递 token）
            user_config = await _get_user_config(db, task.created_by)

            # 从用户配置中提取 token和SSH密钥（用于私有仓库克隆）
            other_config = (user_config or {}).get('otherConfig', {})
            github_token = other_config.get('githubToken') or settings.GITHUB_TOKEN
            gitlab_token = other_config.get('gitlabToken') or settings.GITLAB_TOKEN
            gitea_token = other_config.get('giteaToken') or settings.GITEA_TOKEN

            # 解密SSH私钥
            ssh_private_key = None
            if 'sshPrivateKey' in other_config:
                try:
                    encrypted_key = other_config['sshPrivateKey']
                    ssh_private_key = decrypt_sensitive_data(encrypted_key)
                    logger.info("成功解密SSH私钥")
                except Exception as e:
                    logger.warning(f"解密SSH私钥失败: {e}")

            # 获取项目根目录（传递任务指定的分支和认证 token/SSH密钥）
            # 🔥 传递 event_emitter 以发送克隆进度
            project_root = await _get_project_root(
                project,
                task_id,
                task.branch_name,
                github_token=github_token,
                gitlab_token=gitlab_token,
                gitea_token=gitea_token,  # 🔥 新增
                ssh_private_key=ssh_private_key,  # 🔥 新增SSH密钥
                event_emitter=event_emitter,  # 🔥 新增
            )

            # 🔥 自动修正 target_files 路径
            # 如果发生了目录调整（例如 ZIP 解压后只有一层目录，root 被下移），
            # 原有的 target_files (如 "Prefix/file.php") 可能无法匹配。
            # 我们需要检测并移除这些无效的前缀。
            if task.target_files and len(task.target_files) > 0:
                # 1. 检查是否存在不匹配的文件
                all_exist = True
                for tf in task.target_files:
                    if not os.path.exists(os.path.join(project_root, tf)):
                        all_exist = False
                        break

                if not all_exist:
                    logger.info(f"Target files path mismatch detected in {project_root}")
                    # 尝试通过路径匹配来修复
                    # 获取当前根目录的名称
                    root_name = os.path.basename(project_root)

                    new_target_files = []
                    fixed_count = 0

                    for tf in task.target_files:
                        # 检查文件是否以 root_name 开头（例如 "PHP-Project/index.php" 而 root 是 ".../PHP-Project"）
                        if tf.startswith(root_name + "/"):
                            fixed_path = tf[len(root_name)+1:]
                            if os.path.exists(os.path.join(project_root, fixed_path)):
                                new_target_files.append(fixed_path)
                                fixed_count += 1
                                continue

                        # 如果上面的没匹配，尝试暴力搜索（只针对未找到的文件）
                        # 这种情况比较少见，先保留原样或标记为丢失
                        if os.path.exists(os.path.join(project_root, tf)):
                            new_target_files.append(tf)
                        else:
                            # 尝试查看 tf 的 basename 是否在根目录直接存在（针对常见的最简情况）
                            basename = os.path.basename(tf)
                            if os.path.exists(os.path.join(project_root, basename)):
                                new_target_files.append(basename)
                                fixed_count += 1
                            else:
                                # 实在找不到，保留原样，让后续流程报错或忽略
                                new_target_files.append(tf)

                    if fixed_count > 0:
                        logger.info(f"🔧 Auto-fixed {fixed_count} target file paths")
                        await event_emitter.emit_info(f"🔧 自动修正了 {fixed_count} 个目标文件的路径")
                        task.target_files = new_target_files

            # 🔥 重新验证修正后的文件
            valid_target_files = []
            if task.target_files:
                for tf in task.target_files:
                    if os.path.exists(os.path.join(project_root, tf)):
                        valid_target_files.append(tf)
                    else:
                        logger.warning(f"⚠️ Target file not found: {tf}")

                if not valid_target_files:
                    logger.warning("❌ No valid target files found after adjustment!")
                    await event_emitter.emit_warning("⚠️ 警告：无法找到指定的目标文件，将扫描所有文件")
                    task.target_files = None  # 回退到全量扫描
                elif len(valid_target_files) < len(task.target_files):
                    logger.warning(f"⚠️ Partial target files missing. Found {len(valid_target_files)}/{len(task.target_files)}")
                    task.target_files = valid_target_files

            logger.info(f"🚀 Task {task_id} started with Dynamic Agent Tree architecture")
            await event_emitter.emit_info("Code indexing complete", metadata={"init_step": "Indexing code", "init_status": "done"})
            await event_emitter.emit_info("Preparing AI agents", metadata={"init_step": "Preparing agents", "init_status": "start"})

            # 🔥 获取项目根目录后检查取消
            if is_task_cancelled(task_id):
                logger.info(f"[Cancel] Task {task_id} cancelled after project preparation")
                raise asyncio.CancelledError("任务已取消")

            # 创建 LLM 服务
            llm_service = LLMService(user_config=user_config)

            # 初始化工具集 - 传递排除模式和目标文件以及预初始化的 sandbox_manager
            # 🔥 传递 event_emitter 以发送索引进度，传递 task_id 以支持取消
            tools = await _initialize_tools(
                project_root,
                llm_service,
                user_config,
                sandbox_manager=sandbox_manager,
                exclude_patterns=task.exclude_patterns,
                target_files=task.target_files,
                project_id=str(project.id),
                event_emitter=event_emitter,
                task_id=task_id,
            )

            # 🔥 FIX: 将 RAG 统计从 _initialize_tools 移到此处，此处 task 和 db 均在作用域内
            try:
                rag_indexed_files = tools.pop('_rag_indexed_files', 0)
                rag_total_chunks = tools.pop('_rag_total_chunks', 0)
                if (rag_indexed_files or rag_total_chunks) and task and db:
                    task.indexed_files = int(rag_indexed_files)
                    task.total_chunks = int(rag_total_chunks)
                    await db.commit()
                    logger.info(f'[RAG] Persisted index stats in _execute_agent_task: indexed_files={task.indexed_files}, total_chunks={task.total_chunks}')
            except Exception as persist_err:
                logger.warning(f'[RAG] Failed to persist index stats in caller: {persist_err}')

            # 🔥 初始化工具后检查取消
            if is_task_cancelled(task_id):
                logger.info(f"[Cancel] Task {task_id} cancelled after tools initialization")
                raise asyncio.CancelledError("任务已取消")

            # 🔥 从 task.agent_config 快照读取 task-scoped RPM（无则 fallback 到全局默认）
            task_rpm: int | None = None
            if isinstance(task.agent_config, dict):
                rpm_value = task.agent_config.get("llm_rate_per_minute")
                if rpm_value is not None:
                    task_rpm = int(rpm_value)

            # 创建子 Agent（透传 task_id 和 RPM，用于 task-scoped LLM 限流隔离）
            recon_agent = ReconAgent(
                llm_service=llm_service,
                tools=tools.get("recon", {}),
                event_emitter=event_emitter,
                task_id=task_id,
                llm_rate_per_minute=task_rpm,
            )

            analysis_agent = AnalysisAgent(
                llm_service=llm_service,
                tools=tools.get("analysis", {}),
                event_emitter=event_emitter,
                task_id=task_id,
                llm_rate_per_minute=task_rpm,
            )

            verification_agent = VerificationAgent(
                llm_service=llm_service,
                tools=tools.get("verification", {}),
                event_emitter=event_emitter,
                task_id=task_id,
                llm_rate_per_minute=task_rpm,
            )

            await event_emitter.emit_info("AI agents ready", metadata={"init_step": "Preparing agents", "init_status": "done"})
            await event_emitter.emit_info("Starting audit engine", metadata={"init_step": "Starting audit", "init_status": "start"})
            # 创建 Orchestrator Agent
            orchestrator = OrchestratorAgent(
                llm_service=llm_service,
                tools=tools.get("orchestrator", {}),
                event_emitter=event_emitter,
                sub_agents={
                    "recon": recon_agent,
                    "analysis": analysis_agent,
                    "verification": verification_agent,
                },
                task_id=task_id,
                llm_rate_per_minute=task_rpm,
            )
            orchestrator._pause_task_id = task_id
            orchestrator._pause_db_session_factory = async_session_factory

            # 🔥 设置外部取消检查回调
            # 这确保即使 runner.cancel() 失败，Agent 也能通过 checking 全局标志感知取消
            def check_global_cancel():
                return is_task_cancelled(task_id)

            orchestrator.set_cancel_callback(check_global_cancel)
            # 同时也为子 Agent 设置（虽然 Orchestrator 会传播）
            recon_agent.set_cancel_callback(check_global_cancel)
            analysis_agent.set_cancel_callback(check_global_cancel)
            verification_agent.set_cancel_callback(check_global_cancel)

            # 注册到全局
            _running_orchestrators[task_id] = orchestrator
            _running_tasks[task_id] = orchestrator  # 兼容旧的取消逻辑
            _running_event_managers[task_id] = event_manager  # 用于 SSE 流

            # C1: 只清理本任务的旧注册表作用域（并发任务互不干扰）
            from app.services.agent.core import agent_registry
            agent_registry.clear_task(task_id)

            # 注册 Orchestrator 到 Agent Registry（使用其内置方法）
            orchestrator._register_to_registry(task="Root orchestrator for security audit")
            # C1: 绑定任务 → 根 Agent，供 clear_task / stop_task_agents / get_task_tree 按任务隔离
            agent_registry.bind_task(task_id, orchestrator._agent_id)

            await event_emitter.emit_info("🧠 动态 Agent 树架构启动")
            await event_emitter.emit_info(f"📁 项目路径: {project_root}")
            await event_emitter.emit_info("Project files ready", metadata={"init_step": "Extracting project", "init_status": "done"})

            # 收集项目信息 - 传递排除模式和目标文件
            project_info = await _collect_project_info(
                project_root,
                project.name,
                exclude_patterns=task.exclude_patterns,
                target_files=task.target_files,
            )

            # 更新任务文件统计
            task.total_files = project_info.get("file_count", 0)
            await db.commit()

            # 🧠 加载项目历史审计记忆（同项目往次已确认漏洞，作为线索注入 Agent）
            audit_memory = []
            try:
                from app.services.agent.audit_memory import load_project_memory
                audit_memory = await load_project_memory(
                    db,
                    project_id=task.project_id,
                    exclude_task_id=task_id,
                )
                if audit_memory:
                    logger.info(
                        f"[AgentTask] Loaded {len(audit_memory)} historical memory "
                        f"entries for project {task.project_id}"
                    )
            except Exception as e:
                logger.warning(f"[AgentTask] Failed to load audit memory (non-fatal): {e}")

            # 构建输入数据
            input_data = {
                "project_info": project_info,
                "config": {
                    "target_vulnerabilities": task.target_vulnerabilities or [],
                    "verification_level": task.verification_level or "sandbox",
                    "exclude_patterns": task.exclude_patterns or [],
                    "target_files": task.target_files or [],
                    "max_iterations": task.max_iterations or 50,
                },
                "project_root": project_root,
                "task_id": task_id,
                "audit_memory": audit_memory,
            }
            if resume_state and isinstance(resume_state, dict):
                input_data["resume_checkpoint"] = resume_state

            # 问题 1A 修复（心跳启动前置）：
            # 原实现先 emit_phase_start 再 create_task(orchestrator.run)，而心跳协程在
            # orchestrator.run() 内部才创建，导致前端收到 phase_start 后立即拉取 /agent-tasks/{id}
            # 时 Redis 尚无 alive 键，orchestrator_alive 被误判为 False，横幅误报"任务可能已断开"。
            # 修复：在 phase_start 之前，先由 endpoint 侧同步写入一次 alive 键，确保后续
            # is_alive 判定为 True。心跳协程随后正常刷新 TTL。
            try:
                from app.services.agent.core.orchestrator_registry import (
                    get_registry as _get_registry,
                )
                _registry = await _get_registry()
                await asyncio.wait_for(
                    _registry.set_alive(task_id, event_manager_local=True),
                    timeout=2.0,
                )
            except Exception as _pre_alive_err:  # 不阻塞主流程；心跳协程会兜底
                logger.debug(f"[AgentTask] pre-alive registry write failed for {task_id}: {_pre_alive_err}")

            # 执行 Orchestrator
            await event_emitter.emit_phase_start("orchestration", "🎯 Orchestrator 开始编排审计流程")
            task.current_phase = AgentTaskPhase.ANALYSIS
            await db.commit()

            # 🔥 将 orchestrator.run() 包装在 asyncio.Task 中，以便可以强制取消
            run_task = asyncio.create_task(orchestrator.run(input_data))
            _running_asyncio_tasks[task_id] = run_task

            # 修复"任务已断开"误报：orchestrator.run 内部会启动自己的心跳协程，
            # 这里把 early heartbeat 取消掉避免两个协程同时写 alive key。
            if _early_alive_task is not None and not _early_alive_task.done():
                _early_alive_task.cancel()
                try:
                    await _early_alive_task
                except (asyncio.CancelledError, Exception):
                    pass
                _early_alive_task = None

            try:
                # Fix: 总体超时保护，防止任务无限运行
                task_timeout = task.timeout_seconds or 1800
                try:
                    result = await asyncio.wait_for(run_task, timeout=task_timeout)
                except TimeoutError:
                    logger.warning(f"[AgentTask] Task {task_id} timed out after {task_timeout}s, cancelling and marking as COMPLETED_WITH_GAPS")
                    run_task.cancel()
                    try:
                        await run_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    # Wave 1 §2.2 修复：AgentEventEmitter 无 emit_event 方法，此处应为 emit_warning。
                    # 原实现 event_emitter.emit_event('warning', ...) 会抛 AttributeError，
                    # 被外层 except Exception 吞掉，任务被误判为 FAILED 且不发终态事件。
                    await event_emitter.emit_warning(f"任务超时（{task_timeout}秒），已强制结束并保存已有发现")
                    # 构造超时结果，保存已有发现
                    result = AgentResult(
                        success=True,
                        data={"findings": getattr(orchestrator, '_all_findings', [])},
                        iterations=getattr(orchestrator, '_iteration', 0),
                        tool_calls=getattr(orchestrator, '_tool_calls', 0),
                        tokens_used=getattr(orchestrator, '_total_tokens', 0) + getattr(orchestrator, '_sub_agent_total_tokens', 0),
                        duration_ms=int((time.time() - start_time) * 1000),
                        metadata={"coverage_bypassed": True, "coverage_info": {"reason": "task_timeout", "timeout_seconds": task_timeout}},
                    )
            except AgentExecutionPaused as e:
                task.status = AgentTaskStatus.PAUSED
                task.paused = True
                task.paused_at = datetime.now(UTC)
                task.pause_reason = getattr(e, "reason", None) or task.pause_reason or "manual"
                task.last_error_code = getattr(e, "error_code", None)
                task.last_checkpoint_id = e.checkpoint_id
                await db.commit()
                await event_emitter.emit_info(f"⏸️ 任务已暂停，checkpoint={e.checkpoint_id}")
                return
            finally:
                _running_asyncio_tasks.pop(task_id, None)

            # 处理结果
            duration_ms = int((time.time() - start_time) * 1000)

            await db.refresh(task)

            if result.success:
                # 🔥 CRITICAL FIX: Log and save findings with detailed debugging
                findings = result.data.get("findings", [])
                logger.info(f"[AgentTask] Task {task_id} completed with {len(findings)} findings from Orchestrator")

                # R6: 持久化门禁拒绝/兜底原因到 agent_tasks.observations（历史字段无写入点）
                _orch_obs = result.data.get("observations") or []
                if _orch_obs:
                    task.observations = list(_orch_obs)
                    logger.info(f"[AgentTask] Task {task_id} persisted {len(_orch_obs)} gate observations")

                # 🔥 Debug: Log each finding for verification
                for i, f in enumerate(findings[:5]):  # Log first 5
                    if isinstance(f, dict):
                        logger.debug(f"[AgentTask] Finding {i+1}: {f.get('title', 'N/A')[:50]} - {f.get('severity', 'N/A')}")

                # 🔥 v2.1: 传递 project_root 用于文件路径验证
                saved_count = await _save_findings(db, task_id, findings, project_root=project_root)
                logger.info(f"[AgentTask] Saved {saved_count}/{len(findings)} findings (filtered {len(findings) - saved_count} hallucinations)")

                # 更新任务统计
                # 🔥 CRITICAL FIX: 在设置完成前再次检查取消状态
                # 避免 "取消后后端继续运行并最终标记为完成" 的问题
                if is_task_cancelled(task_id):
                    logger.info(f"[AgentTask] Task {task_id} was cancelled, overriding success result")
                    task.status = AgentTaskStatus.CANCELLED
                else:
                    # 安全阀放行（覆盖率不足）时标记为 COMPLETED_WITH_GAPS
                    _meta = result.metadata or {}
                    if _meta.get("coverage_bypassed"):
                        task.status = AgentTaskStatus.COMPLETED_WITH_GAPS
                        logger.warning(f"[AgentTask] Task {task_id} completed with coverage gaps: {_meta.get("coverage_info", {})}")
                    else:
                        task.status = AgentTaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC)
                task.current_phase = AgentTaskPhase.REPORTING
                task.findings_count = saved_count  # 🔥 v2.1: 使用实际保存的数量（排除幻觉）
                task.total_iterations = result.iterations
                task.tool_calls_count = result.tool_calls
                task.tokens_used = result.tokens_used

                # 🔥 统计文件数量
                # analyzed_files = 实际扫描过的文件数（任务完成时等于 total_files）
                # files_with_findings = 有漏洞发现的唯一文件数
                task.analyzed_files = task.total_files  # Agent 扫描了所有符合条件的文件

                # D1/D2/D3: 计数器从已落库的 AgentFinding 重查，保证与 findings 表一致。
                # 旧逻辑遍历含幻觉 finding 的原始列表做 += 累加，导致统计虚高；
                # verified_count 仅统计 confirmed/verified/true_positive 且 is_verified=True。
                await _recalc_task_counters_from_db(db, task, task_id)

                # 计算安全评分
                task.security_score = _calculate_security_score(findings)
                # P4: 质量评分（agent 流程之前恒为 0）
                _cov_info = (_meta or {}).get("coverage_info", {}) if isinstance(_meta, dict) else {}
                task.quality_score = _calculate_quality_score(
                    findings,
                    verified_count=task.verified_count,
                    coverage_covered=_cov_info.get("covered_count", 0),
                    coverage_total=_cov_info.get("total_dimensions", 0) or 10,
                    saved_count=saved_count,
                )
                # 🔥 注意: progress_percentage 是计算属性，不需要手动设置
                # 当 status = COMPLETED 时会自动返回 100.0

                await event_emitter.emit_phase_complete(
                    "orchestration",
                    "✅ 编排完成，准备进入结果汇总",
                )

                await db.commit()

                # 🔥 完成阶段事件：让前端能看到完整结束点
                await event_emitter.emit_phase_start("reporting", "📝 开始生成审计报告")
                await event_emitter.emit_phase_complete("reporting", "✅ 审计报告生成完成")

                await event_emitter.emit_task_complete(
                    findings_count=saved_count,  # 使用实际落库数量，避免与 DB 不一致
                    duration_ms=duration_ms,
                )

                logger.info(f"✅ Task {task_id} completed: {saved_count} findings (saved), {duration_ms}ms")
            else:
                # 🔥 检查是否是取消导致的失败
                if result.error == "任务已取消":
                    # 状态可能已经被 cancel API 更新，只需确保一致性
                    if task.status != AgentTaskStatus.CANCELLED:
                        task.status = AgentTaskStatus.CANCELLED
                        task.completed_at = datetime.now(UTC)
                        await db.commit()
                    logger.info(f"🛑 Task {task_id} cancelled")
                else:
                    task.status = AgentTaskStatus.FAILED
                    task.error_message = result.error or "Unknown error"
                    task.completed_at = datetime.now(UTC)
                    await db.commit()

                    await event_emitter.emit_error(result.error or "Unknown error")
                    logger.error(f"❌ Task {task_id} failed: {result.error} (phase={task.current_phase})")

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} cancelled")
            try:
                task = await db.get(AgentTask, task_id)
                if task:
                    task.status = AgentTaskStatus.CANCELLED
                    task.completed_at = datetime.now(UTC)
                    await db.commit()
            except Exception:
                pass
            # Wave 1 §2.3 修复：CancelledError 分支也要发 task_cancel 终态事件，
            # 否则 SSE 流的 stream_events 在等待终态事件永远等不到（原实现只更新 DB）。
            try:
                await event_emitter.emit_task_cancelled("任务已取消")
            except Exception as e:
                logger.warning(f"[Cancel] Failed to emit task_cancelled from CancelledError branch: {e}")

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)

            try:
                task = await db.get(AgentTask, task_id)
                if task:
                    task.status = AgentTaskStatus.FAILED
                    task.error_message = str(e)[:1000]
                    task.completed_at = datetime.now(UTC)
                    await db.commit()
            except Exception as db_error:
                logger.error(f"Failed to update task status: {db_error}")

        finally:
            # 🔥 在清理之前保存 Agent 树到数据库
            try:
                async with async_session_factory() as save_db:
                    await _save_agent_tree(save_db, task_id)
            except Exception as save_error:
                logger.error(f"Failed to save agent tree: {save_error}")

            # 清理
            _running_orchestrators.pop(task_id, None)
            _running_tasks.pop(task_id, None)
            _running_event_managers.pop(task_id, None)
            _running_asyncio_tasks.pop(task_id, None)  # 🔥 清理 asyncio task
            _cancelled_tasks_discard(task_id)  # 🔥 P2-8: TTL dict 版本的 discard

            # 修复"任务已断开"误报：任务异常退出（未走到 orchestrator.run() 前置 cancel）时
            # 也要 cancel early heartbeat，避免协程泄漏。
            if _early_alive_task is not None and not _early_alive_task.done():
                _early_alive_task.cancel()

            # C1: 只清理本任务的注册表作用域（包括所有子 Agent），不影响并发任务
            agent_registry.clear_task(task_id)

            # REQ-CLEAN-1: 清理任务的临时源码目录，防止 tmpfs 累积塞满
            # （ignore_errors=True 幂等容忍目录已不存在，不阻断任务收尾）
            shutil.rmtree(f"/tmp/lanjian/{task_id}", ignore_errors=True)

            logger.debug(f"Task {task_id} cleaned up")


async def _get_user_config(db: AsyncSession, user_id: str | None) -> dict[str, Any] | None:
    """获取用户配置（问题一：包含系统级配置共享）"""
    if not user_id:
        return None

    try:
        from app.api.v1.endpoints.config import resolve_effective_config
        from app.models.user import User as _User

        # 查询该用户是否为超管（决定是否叠加系统级层）
        user_row = await db.execute(select(_User).where(_User.id == user_id))
        user_obj = user_row.scalar_one_or_none()
        is_superuser = bool(user_obj.is_superuser) if user_obj else False

        merged_llm, merged_other = await resolve_effective_config(
            db, user_id, is_superuser=is_superuser
        )
        return {
            "llmConfig": merged_llm,
            "otherConfig": merged_other,
        }
    except Exception as e:
        logger.warning(f"Failed to get user config: {e}")

    return None


async def _initialize_tools(
    project_root: str,
    llm_service,
    user_config: dict[str, Any] | None,
    sandbox_manager: Any, # 传递预初始化的 SandboxManager
    exclude_patterns: list[str] | None = None,
    target_files: list[str] | None = None,
    project_id: str | None = None,  # 🔥 用于 RAG collection_name
    event_emitter: Any | None = None,  # 🔥 新增：用于发送实时日志
    task_id: str | None = None,  # 🔥 新增：用于取消检查
) -> dict[str, dict[str, Any]]:
    """初始化工具集

    Args:
        project_root: 项目根目录
        llm_service: LLM 服务
        user_config: 用户配置
        sandbox_manager: 沙箱管理器
        exclude_patterns: 排除模式列表
        target_files: 目标文件列表
        project_id: 项目 ID（用于 RAG collection_name）
        event_emitter: 事件发送器（用于发送实时日志）
        task_id: 任务 ID（用于取消检查）
    """
    from app.core.config import settings
    from app.services.agent.knowledge import (
        GetVulnerabilityKnowledgeTool,
        SecurityKnowledgeQueryTool,
    )
    from app.services.agent.tools import (
        BanditTool,
        CreateVulnerabilityReportTool,
        DataFlowAnalysisTool,
        FileReadTool,
        FileSearchTool,
        FunctionContextTool,
        GitleaksTool,
        ListFilesTool,
        NpmAuditTool,  # 🔥 Added missing tools
        OSVScannerTool,
        PatternMatchTool,
        # 🔥 RAG 工具
        RAGQueryTool,
        ReflectTool,
        SafetyTool,
        SecurityCodeSearchTool,
        SemgrepTool,
        ThinkTool,
        TruffleHogTool,
    )

    # 🔥 RAG 相关导入
    from app.services.rag import CodeIndexer, CodeRetriever, EmbeddingService, IndexUpdateMode
    from app.services.rag.embeddings import EmbeddingUnavailableError

    # 辅助函数：发送事件
    async def emit(message: str, level: str = "info"):
        if event_emitter:
            logger.debug(f"[EMIT-TOOLS] Sending {level}: {message[:60]}...")
            if level == "info":
                await event_emitter.emit_info(message)
            elif level == "warning":
                await event_emitter.emit_warning(message)
            elif level == "error":
                await event_emitter.emit_error(message)
        else:
            logger.warning(f"[EMIT-TOOLS] No event_emitter, skipping: {message[:60]}...")

    # ============ 🔥 初始化 RAG 系统 ============
    retriever = None
    last_progress_update = 0  # 🔥 移到 try 外部，避免作用域 bug
    try:
        await emit("🔍 正在初始化 RAG 系统...")

        # 从用户配置中获取 embedding 配置
        user_llm_config = (user_config or {}).get('llmConfig', {})
        user_other_config = (user_config or {}).get('otherConfig', {})
        user_embedding_config = user_other_config.get('embedding_config', {})

        # Embedding Provider 优先级：用户嵌入配置 > 环境变量
        embedding_provider = (
            user_embedding_config.get('provider') or
            getattr(settings, 'EMBEDDING_PROVIDER', 'openai')
        )

        # Embedding Model 优先级：用户嵌入配置 > 环境变量
        embedding_model = (
            user_embedding_config.get('model') or
            getattr(settings, 'EMBEDDING_MODEL', 'text-embedding-3-small')
        )

        # API Key 优先级：用户嵌入配置 > 环境变量 EMBEDDING_API_KEY > 用户 LLM 配置 > 环境变量 LLM_API_KEY
        # 注意：API Key 可以共享，因为很多用户使用同一个 OpenAI Key 做 LLM 和 Embedding
        embedding_api_key = (
            user_embedding_config.get('api_key') or
            getattr(settings, 'EMBEDDING_API_KEY', None) or
            user_llm_config.get('llmApiKey') or
            getattr(settings, 'LLM_API_KEY', '') or
            ''
        )

        # Base URL 优先级：用户嵌入配置 > 环境变量 EMBEDDING_BASE_URL > None（使用提供商默认地址）
        # 🔥 重要：Base URL 不应该回退到 LLM 的 base_url，因为 Embedding 和 LLM 可能使用完全不同的服务
        # 例如：LLM 使用 SiliconFlow，但 Embedding 使用 HuggingFace
        embedding_base_url = (
            user_embedding_config.get('base_url') or
            getattr(settings, 'EMBEDDING_BASE_URL', None) or
            None
        )

        logger.info(f"RAG 配置: provider={embedding_provider}, model={embedding_model}, base_url={embedding_base_url or '(使用默认)'}")
        await emit(f"📊 Embedding 配置: {embedding_provider}/{embedding_model}")

        # 创建 Embedding 服务
        embedding_service = EmbeddingService(
            provider=embedding_provider,
            model=embedding_model,
            api_key=embedding_api_key,
            base_url=embedding_base_url,
        )

        # 创建 collection_name（基于 project_id）
        collection_name = f"project_{project_id}" if project_id else "default_project"

        # 🔥 v2.0: 创建 CodeIndexer 并进行智能索引
        # 智能索引会自动：
        # - 检测 embedding 模型变更，如需要则自动重建
        # - 对比文件 hash，只更新变化的文件（增量更新）
        indexer = CodeIndexer(
            collection_name=collection_name,
            embedding_service=embedding_service,
            persist_directory=settings.VECTOR_DB_PATH,
        )

        logger.info(f"📝 开始智能索引项目: {project_root}")
        await emit("📝 正在构建代码向量索引...")

        index_progress = None
        last_embedding_progress = [0]  # 使用列表以便在闭包中修改
        embedding_total = [0]  # 记录总数
        indexing_timed_out = False  # 🔥 标记索引是否超时
        rag_unavailable = False  # 🔥 wave4/B4: 标记嵌入服务是否不可用

        # 🔥 嵌入进度回调函数（同步，但会调度异步任务）
        def on_embedding_progress(processed: int, total: int):
            embedding_total[0] = total
            # 🔥 优化：每处理 10 个或完成时更新（原为 50，太慢导致前端看起来卡住）
            if processed - last_embedding_progress[0] >= 10 or processed == total:
                last_embedding_progress[0] = processed
                percentage = (processed / total * 100) if total > 0 else 0
                msg = EMBED_PROGRESS_MSG_TEMPLATE.format(
                    processed=processed, total=total, pct=percentage
                )
                logger.info(msg)
                # 使用 asyncio.create_task 调度异步 emit
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(emit(msg))
                except Exception as e:
                    logger.warning(f"Failed to emit embedding progress: {e}")

        # 🔥 创建取消检查函数，用于在嵌入批处理中检查取消状态
        def check_cancelled() -> bool:
            return task_id is not None and is_task_cancelled(task_id)

        # 🔥 RAG 索引超时机制：根据项目规模动态调整
        # 小项目 5 分钟，大项目最多 30 分钟
        RAG_INDEX_TIMEOUT = 1800  # 30 分钟

        async def run_indexing_with_timeout():
            nonlocal index_progress, indexing_timed_out, last_progress_update, rag_unavailable
            try:
                async with asyncio.timeout(RAG_INDEX_TIMEOUT):
                    async for progress in indexer.smart_index_directory(
                        directory=project_root,
                        exclude_patterns=exclude_patterns or [],
                        include_patterns=target_files,
                        update_mode=IndexUpdateMode.SMART,
                        embedding_progress_callback=on_embedding_progress,
                        cancel_check=check_cancelled,
                    ):
                        # 🔥 在索引过程中检查取消状态
                        if check_cancelled():
                            logger.info(f"[Cancel] RAG indexing cancelled for task {task_id}")
                            raise asyncio.CancelledError("任务已取消")

                        index_progress = progress
                        # 每处理 10 个文件或有重要变化时发送进度更新
                        if progress.processed_files - last_progress_update >= 10 or progress.processed_files == progress.total_files:
                            if progress.total_files > 0:
                                await emit(
                                    CHUNK_PROGRESS_MSG_TEMPLATE.format(
                                        processed=progress.processed_files,
                                        total=progress.total_files,
                                        pct=progress.progress_percentage,
                                    )
                                )
                                # Mark progress as complete when 100%
                                if progress.processed_files >= progress.total_files:
                                    await emit("progress_complete:index_progress")
                            last_progress_update = progress.processed_files

                        # 🔥 发送状态消息（如嵌入向量生成进度）
                        if progress.status_message:
                            await emit(progress.status_message)
                            progress.status_message = ""  # 清空已发送的消息
            except TimeoutError:
                indexing_timed_out = True
                logger.warning(f"⚠️ RAG 索引超时（{RAG_INDEX_TIMEOUT}秒），跳过 RAG 继续审计")
                await emit("⚠️ RAG 索引超时，跳过向量检索继续审计（不影响基础审计）")
            except EmbeddingUnavailableError as rag_err:
                # wave4/B4: 嵌入批次重试耗尽 → RAG 嵌入服务不可用。
                # 语义不同于索引超时：分块已完成，仅嵌入阶段失败，
                # 跳过向量检索、继续基础审计，绝不写入零向量。
                rag_unavailable = True
                logger.error(f"⚠️ 嵌入服务不可用，跳过向量检索继续基础审计: {rag_err}")
                await emit(f"⚠️ 嵌入服务不可用（{rag_err}），跳过向量检索，继续基础审计")
            except asyncio.CancelledError:
                raise

        await run_indexing_with_timeout()

        if rag_unavailable:
            # 🔥 wave4/B4: 嵌入服务不可用 → 跳过 RAG 检索器创建，继续基础审计
            logger.error("⚠️ 嵌入服务不可用，跳过 RAG 检索器初始化，继续执行审计")
            await emit("⚠️ 嵌入服务不可用，跳过 RAG 检索，使用基础审计模式")
            retriever = None
            rag_indexed_files = 0
            rag_total_chunks = 0
        elif indexing_timed_out:
            # 🔥 超时后跳过 RAG 检索器创建，但继续审计
            logger.info("⚠️ 跳过 RAG 检索器初始化，继续执行审计")
            await emit("⚠️ 跳过 RAG 检索，使用基础审计模式")
            retriever = None
            rag_indexed_files = 0
            rag_total_chunks = 0
        elif index_progress:
            summary = (
                f"✅ 索引完成: 模式={index_progress.update_mode}, "
                f"新增={index_progress.added_files}, "
                f"更新={index_progress.updated_files}, "
                f"删除={index_progress.deleted_files}, "
                f"代码块={index_progress.indexed_chunks}"
            )
            logger.info(summary)
            await emit(summary)

            rag_indexed_files = int(getattr(index_progress, 'added_files', 0) or 0)
            rag_total_chunks = int(getattr(index_progress, 'indexed_chunks', 0) or 0)

            # ✅ FIX: 索引成功后创建 CodeRetriever，让 rag_query 工具可用
            try:
                retriever = CodeRetriever(
                    collection_name=collection_name,
                    embedding_service=embedding_service,
                    persist_directory=settings.VECTOR_DB_PATH,
                )
                logger.info(f"✅ CodeRetriever 创建成功 (collection={collection_name})")
            except Exception as retriever_err:
                logger.warning(f"⚠️ CodeRetriever 创建失败: {retriever_err}")
                retriever = None
        else:
            retriever = None
            rag_indexed_files = 0
            rag_total_chunks = 0

    except Exception as e:
        logger.warning(f"⚠️ RAG 系统初始化失败: {e}")
        await emit(f"⚠️ RAG 系统初始化失败: {e}", "warning")
        import traceback
        logger.debug(f"RAG 初始化异常详情:\n{traceback.format_exc()}")
        retriever = None
        rag_indexed_files = 0
        rag_total_chunks = 0

    # 基础工具 - 传递排除模式和目标文件
    base_tools = {
        "read_file": FileReadTool(project_root, exclude_patterns, target_files),
        "list_files": ListFilesTool(project_root, exclude_patterns, target_files),
        "search_code": FileSearchTool(project_root, exclude_patterns, target_files),
        "think": ThinkTool(),
        "reflect": ReflectTool(),
    }

    # Recon 工具
    recon_tools = {
        **base_tools,
        # 🔥 外部侦察工具 (Recon 阶段也需要使用这些工具来收集初步信息)
        "semgrep_scan": SemgrepTool(project_root, sandbox_manager),
        "bandit_scan": BanditTool(project_root, sandbox_manager),
        "gitleaks_scan": GitleaksTool(project_root, sandbox_manager),
        "npm_audit": NpmAuditTool(project_root, sandbox_manager),
        "safety_scan": SafetyTool(project_root, sandbox_manager),
        "trufflehog_scan": TruffleHogTool(project_root, sandbox_manager),
        "osv_scan": OSVScannerTool(project_root, sandbox_manager),
    }

    # 🔥 注册 RAG 工具到 Recon Agent
    if retriever:
        recon_tools["rag_query"] = RAGQueryTool(retriever)
        logger.info("✅ RAG 工具 (rag_query) 已注册到 Recon Agent")

    # Analysis 工具
    # 🔥 导入智能扫描工具
    from app.services.agent.tools import QuickAuditTool, SmartScanTool

    analysis_tools = {
        **base_tools,
        # 🔥 智能扫描工具（推荐首先使用）
        "smart_scan": SmartScanTool(project_root),
        "quick_audit": QuickAuditTool(project_root),
        # 模式匹配工具（增强版）
        "pattern_match": PatternMatchTool(project_root),
        # 数据流分析
        "dataflow_analysis": DataFlowAnalysisTool(llm_service),
        # 外部安全工具 (传入共享的 sandbox_manager)
        "semgrep_scan": SemgrepTool(project_root, sandbox_manager),
        "bandit_scan": BanditTool(project_root, sandbox_manager),
        "gitleaks_scan": GitleaksTool(project_root, sandbox_manager),
        "npm_audit": NpmAuditTool(project_root, sandbox_manager),
        "safety_scan": SafetyTool(project_root, sandbox_manager),
        "trufflehog_scan": TruffleHogTool(project_root, sandbox_manager),
        "osv_scan": OSVScannerTool(project_root, sandbox_manager),
        # 安全知识查询
        "query_security_knowledge": SecurityKnowledgeQueryTool(),
        "get_vulnerability_knowledge": GetVulnerabilityKnowledgeTool(),
    }

    # 🔥 注册 RAG 工具到 Analysis Agent
    if retriever:
        analysis_tools["rag_query"] = RAGQueryTool(retriever)
        analysis_tools["security_search"] = SecurityCodeSearchTool(retriever)
        analysis_tools["function_context"] = FunctionContextTool(retriever)
        logger.info("✅ RAG 工具 (rag_query, security_search, function_context) 已注册到 Analysis Agent")
    else:
        logger.warning("⚠️ RAG 未初始化，rag_query/security_search/function_context 工具不可用")

    # Verification 工具
    # 🔥 导入沙箱工具
    from app.services.agent.tools import (
        # 漏洞验证专用工具
        CommandInjectionTestTool,
        DeserializationTestTool,
        ExtractFunctionTool,
        GoTestTool,
        JavaScriptTestTool,
        JavaTestTool,
        PathTraversalTestTool,
        # 多语言代码测试工具
        PhpTestTool,
        PythonTestTool,
        RubyTestTool,
        # 🔥 新增：通用代码执行工具 (LLM 驱动的 Fuzzing Harness)
        SandboxBrowserTool,
        SandboxHttpTool,
        SandboxTool,
        ShellTestTool,
        SqlInjectionTestTool,
        SstiTestTool,
        UniversalCodeTestTool,
        UniversalVulnTestTool,
        VulnerabilityVerifyTool,
        XssTestTool,
    )

    verification_tools = {
        **base_tools,
        # 🔥 沙箱验证工具
        # 修复沙箱验证空目录 bug：SandboxTool 必须传 project_root，否则 execute_tool_command
        # 里 effective_workdir=None，永远走 execute_command 不挂载分支，
        # 导致沙箱 /workspace/src 为空，Verification Agent 找不到项目文件，验证全部失败。
        "sandbox_exec": SandboxTool(sandbox_manager, project_root=project_root),
        "sandbox_http": SandboxHttpTool(sandbox_manager),
        "sandbox_browser": SandboxBrowserTool(sandbox_manager),  # Q2: 浏览器验证
        "verify_vulnerability": VulnerabilityVerifyTool(sandbox_manager),

        # 🔥 多语言代码测试工具
        "php_test": PhpTestTool(sandbox_manager, project_root),
        "python_test": PythonTestTool(sandbox_manager, project_root),
        "javascript_test": JavaScriptTestTool(sandbox_manager, project_root),
        "java_test": JavaTestTool(sandbox_manager, project_root),
        "go_test": GoTestTool(sandbox_manager, project_root),
        "ruby_test": RubyTestTool(sandbox_manager, project_root),
        "shell_test": ShellTestTool(sandbox_manager, project_root),
        "universal_code_test": UniversalCodeTestTool(sandbox_manager, project_root),

        # 🔥 漏洞验证专用工具
        "test_command_injection": CommandInjectionTestTool(sandbox_manager, project_root),
        "test_sql_injection": SqlInjectionTestTool(sandbox_manager, project_root),
        "test_xss": XssTestTool(sandbox_manager, project_root),
        "test_path_traversal": PathTraversalTestTool(sandbox_manager, project_root),
        "test_ssti": SstiTestTool(sandbox_manager, project_root),
        "test_deserialization": DeserializationTestTool(sandbox_manager, project_root),
        "universal_vuln_test": UniversalVulnTestTool(sandbox_manager, project_root),

        # 代码提取工具（只读）
        "extract_function": ExtractFunctionTool(project_root),

        # 报告工具 - 🔥 v2.1: 传递 project_root 用于文件验证
        "create_vulnerability_report": CreateVulnerabilityReportTool(project_root),
    }

    # Orchestrator 工具（主要是思考工具）
    orchestrator_tools = {
        "think": ThinkTool(),
        "reflect": ReflectTool(),
    }

    result = {
        "recon": recon_tools,
        "analysis": analysis_tools,
        "verification": verification_tools,
        "orchestrator": orchestrator_tools,
    }

    # 传递 RAG 统计给调用方（不在这里写 DB，由 _execute_agent_task 写入）
    try:
        result['_rag_indexed_files'] = rag_indexed_files
        result['_rag_total_chunks'] = rag_total_chunks
    except Exception:
        pass

    return result


async def _collect_project_info(
    project_root: str,
    project_name: str,
    exclude_patterns: list[str] | None = None,
    target_files: list[str] | None = None,
) -> dict[str, Any]:
    """收集项目信息
    
    Args:
        project_root: 项目根目录
        project_name: 项目名称
        exclude_patterns: 排除模式列表
        target_files: 目标文件列表
    
    🔥 重要：当指定了 target_files 时，返回的项目结构应该只包含目标文件相关的信息，
    以确保 Orchestrator 和子 Agent 看到的是一致的、过滤后的视图。
    """
    import fnmatch

    info = {
        "name": project_name,
        "root": project_root,
        "languages": [],
        "file_count": 0,
        "structure": {},
    }

    try:
        # 默认排除目录
        exclude_dirs = {
            "node_modules", "__pycache__", ".git", "venv", ".venv",
            "build", "dist", "target", ".idea", ".vscode",
        }

        # 从用户配置的排除模式中提取目录
        if exclude_patterns:
            for pattern in exclude_patterns:
                if pattern.endswith("/**"):
                    exclude_dirs.add(pattern[:-3])
                elif "/" not in pattern and "*" not in pattern:
                    exclude_dirs.add(pattern)

        # 目标文件集合
        target_files_set = set(target_files) if target_files else None

        lang_map = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".java": "Java", ".go": "Go", ".php": "PHP",
            ".rb": "Ruby", ".rs": "Rust", ".c": "C", ".cpp": "C++",
        }

        # 🔥 收集过滤后的文件列表
        filtered_files = []
        filtered_dirs = set()

        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]

            for f in files:
                relative_path = os.path.relpath(os.path.join(root, f), project_root)

                # 检查是否在目标文件列表中
                if target_files_set and relative_path not in target_files_set:
                    continue

                # 检查排除模式
                should_skip = False
                if exclude_patterns:
                    for pattern in exclude_patterns:
                        if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(f, pattern):
                            should_skip = True
                            break
                if should_skip:
                    continue

                info["file_count"] += 1
                filtered_files.append(relative_path)

                # 🔥 收集文件所在的目录
                dir_path = os.path.dirname(relative_path)
                if dir_path:
                    # 添加目录及其父目录
                    parts = dir_path.split(os.sep)
                    for i in range(len(parts)):
                        filtered_dirs.add(os.sep.join(parts[:i+1]))

                ext = os.path.splitext(f)[1].lower()
                if ext in lang_map and lang_map[ext] not in info["languages"]:
                    info["languages"].append(lang_map[ext])

        # 🔥 根据是否有目标文件限制，生成不同的结构信息
        if target_files_set:
            # 当指定了目标文件时，只显示目标文件和相关目录
            info["structure"] = {
                "directories": sorted(list(filtered_dirs))[:20],
                "files": filtered_files[:30],
                "scope_limited": True,  # 🔥 标记这是限定范围的视图
                "scope_message": f"审计范围限定为 {len(filtered_files)} 个指定文件",
            }
        else:
            # 全项目审计时，显示顶层目录结构
            try:
                top_items = os.listdir(project_root)
                info["structure"] = {
                    "directories": [d for d in top_items if os.path.isdir(os.path.join(project_root, d)) and d not in exclude_dirs],
                    "files": [f for f in top_items if os.path.isfile(os.path.join(project_root, f))][:20],
                    "scope_limited": False,
                }
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"Failed to collect project info: {e}")

    return info


# D4: 从 finding 各字段提取/回填 file_path，支持 file/file_path/location/source/
# matched_pattern/title/description，无路径时返回 None（不计入 files_with_findings）。
_FILE_PATH_RE = re.compile(
    r'((?:[A-Za-z0-9_.\-]+[\\/])+[A-Za-z0-9_.\-]+(?:\.[A-Za-z0-9_.\-]+)?)(?:[:\s,，?|？\-]|$)'
)


def _extract_finding_file_path(finding: dict) -> str | None:
    """从 finding 多字段提取 file_path，空时尝试从 source/matched_pattern/
    title/description 正则回填。返回路径字符串或 None。
    """
    # 1. 直接字段
    file_path = finding.get("file_path") or finding.get("file")
    if not file_path:
        location = finding.get("location", "")
        if ":" in location:
            file_path = location.split(":")[0]
        elif location:
            file_path = location
    if file_path:
        return file_path

    # 2. D4 回填：从 source/matched_pattern/title/description 正则提取
    for candidate_field in ("source", "matched_pattern", "title", "description"):
        candidate_value = str(finding.get(candidate_field) or "").strip()
        if not candidate_value:
            continue
        m = _FILE_PATH_RE.search(candidate_value)
        if m:
            return m.group(1).strip().rstrip(".,;:!?")

    return None


async def _save_findings(
    db: AsyncSession,
    task_id: str,
    findings: list[dict],
    project_root: str | None = None,
) -> int:
    """
    保存发现到数据库

    🔥 增强版：支持多种 Agent 输出格式，健壮的字段映射
    🔥 v2.1: 添加文件路径验证，过滤幻觉发现

    Args:
        db: 数据库会话
        task_id: 任务ID
        findings: 发现列表
        project_root: 项目根目录（用于验证文件路径）

    Returns:
        int: 实际保存的发现数量
    """
    from app.models.agent_task import VulnerabilityType

    logger.info(f"[SaveFindings] Starting to save {len(findings)} findings for task {task_id}")

    if not findings:
        logger.warning(f"[SaveFindings] No findings to save for task {task_id}")
        return 0

    # 🔥 Case-insensitive mapping preparation
    severity_map = {
        "critical": VulnerabilitySeverity.CRITICAL,
        "high": VulnerabilitySeverity.HIGH,
        "medium": VulnerabilitySeverity.MEDIUM,
        "low": VulnerabilitySeverity.LOW,
        "info": VulnerabilitySeverity.INFO,
    }

    type_map = {
        "sql_injection": VulnerabilityType.SQL_INJECTION,
        "nosql_injection": VulnerabilityType.NOSQL_INJECTION,
        "xss": VulnerabilityType.XSS,
        "command_injection": VulnerabilityType.COMMAND_INJECTION,
        "code_injection": VulnerabilityType.CODE_INJECTION,
        "path_traversal": VulnerabilityType.PATH_TRAVERSAL,
        "ssrf": VulnerabilityType.SSRF,
        "xxe": VulnerabilityType.XXE,
        "auth_bypass": VulnerabilityType.AUTH_BYPASS,
        "idor": VulnerabilityType.IDOR,
        "sensitive_data_exposure": VulnerabilityType.SENSITIVE_DATA_EXPOSURE,
        "hardcoded_secret": VulnerabilityType.HARDCODED_SECRET,
        "deserialization": VulnerabilityType.DESERIALIZATION,
        "weak_crypto": VulnerabilityType.WEAK_CRYPTO,
        "file_inclusion": VulnerabilityType.FILE_INCLUSION,
        "race_condition": VulnerabilityType.RACE_CONDITION,
        "business_logic": VulnerabilityType.BUSINESS_LOGIC,
        "memory_corruption": VulnerabilityType.MEMORY_CORRUPTION,
        "security_misconfiguration": VulnerabilityType.SECURITY_MISCONFIGURATION,
        "open_redirect": VulnerabilityType.OPEN_REDIRECT,
        "unvalidated_redirect": VulnerabilityType.OPEN_REDIRECT,
        "csrf": VulnerabilityType.CSRF,
        "ssti": VulnerabilityType.CODE_INJECTION,
    }

    saved_count = 0
    logger.info(f"Saving {len(findings)} findings for task {task_id}")

    for finding in findings:
        if not isinstance(finding, dict):
            logger.debug(f"[SaveFindings] Skipping non-dict finding: {type(finding)}")
            continue

        # B1-fix: strict finding validation - reject findings without file_path, line, or low confidence
        if not is_strict_finding(finding):
            _f_title = str(finding.get("title", "N/A"))[:60]
            logger.info(f"[SaveFindings] Filtered by is_strict_finding: {_f_title}")
            continue


        try:
            # 🔥 Handle severity (case-insensitive, support multiple field names)
            raw_severity = str(
                finding.get("severity") or
                finding.get("risk") or
                "medium"
            ).lower().strip()
            severity_enum = severity_map.get(raw_severity, VulnerabilitySeverity.MEDIUM)

            # 🔥 Handle vulnerability type (case-insensitive & snake_case normalization)
            # Support multiple field names: vulnerability_type, type, vuln_type
            raw_type = str(
                finding.get("vulnerability_type") or
                finding.get("type") or
                finding.get("vuln_type") or
                "other"
            ).lower().strip().replace(" ", "_").replace("-", "_")

            type_enum = type_map.get(raw_type, VulnerabilityType.OTHER)

            # 🔥 Additional fallback for common Agent output variations
            if "sqli" in raw_type or "sql" in raw_type:
                type_enum = VulnerabilityType.SQL_INJECTION
            if "xss" in raw_type:
                type_enum = VulnerabilityType.XSS
            if "rce" in raw_type or "command" in raw_type or "cmd" in raw_type:
                type_enum = VulnerabilityType.COMMAND_INJECTION
            if "traversal" in raw_type or "lfi" in raw_type or "rfi" in raw_type:
                type_enum = VulnerabilityType.PATH_TRAVERSAL
            if "ssrf" in raw_type:
                type_enum = VulnerabilityType.SSRF
            if "xxe" in raw_type:
                type_enum = VulnerabilityType.XXE
            if "auth" in raw_type:
                type_enum = VulnerabilityType.AUTH_BYPASS
            if "secret" in raw_type or "credential" in raw_type or "password" in raw_type:
                type_enum = VulnerabilityType.HARDCODED_SECRET
            if "deserial" in raw_type:
                type_enum = VulnerabilityType.DESERIALIZATION

            # 🔥 Handle file path (support multiple field names) — D4: 提取为可测试函数
            file_path = _extract_finding_file_path(finding)

            # 🔥 v2.1: 文件路径验证 - 过滤幻觉发现
            if project_root and file_path:
                # 清理路径（移除可能的行号）
                clean_path = file_path.split(":")[0].strip() if ":" in file_path else file_path.strip()
                full_path = os.path.join(project_root, clean_path)

                if not os.path.isfile(full_path):
                    # 尝试作为绝对路径
                    if not (os.path.isabs(clean_path) and os.path.isfile(clean_path)):
                        logger.warning(
                            f"[SaveFindings] 🚫 跳过幻觉发现: 文件不存在 '{file_path}' "
                            f"(title: {finding.get('title', 'N/A')[:50]})"
                        )
                        continue  # 跳过这个发现

            # 🔥 Handle line numbers (support multiple formats)
            # REQ-TH-3: 落库前数值归一化——LLM 的 str line_start 直传 Integer 列会 asyncpg 整批 rollback（c9de9d40 同族）
            line_start = _to_int(finding.get("line_start") or finding.get("line") or 0) or 0
            if not line_start and ":" in finding.get("location", ""):
                try:
                    line_start = int(str(finding.get("location", "")).split(":")[1])
                except (ValueError, IndexError):
                    line_start = None

            line_end = _to_int(finding.get("line_end"))
            if line_end is None:
                line_end = line_start

            # 🔥 Handle code snippet (support multiple field names)
            code_snippet = (
                finding.get("code_snippet") or
                finding.get("code") or
                finding.get("vulnerable_code")
            )

            # 🔥 Handle title (generate from type if not provided)
            title = finding.get("title")
            if not title:
                # Generate title from vulnerability type and file
                type_display = raw_type.replace("_", " ").title()
                if file_path:
                    title = f"{type_display} in {os.path.basename(file_path)}"
                else:
                    title = f"{type_display} Vulnerability"

            # 🔥 Handle description (support multiple field names)
            description = (
                finding.get("description") or
                finding.get("details") or
                finding.get("explanation") or
                finding.get("impact") or
                ""
            )

            # 🔥 Handle suggestion/recommendation
            suggestion = (
                finding.get("suggestion") or
                finding.get("recommendation") or
                finding.get("remediation") or
                finding.get("fix")
            )

            # 🔥 Handle confidence (map to ai_confidence field in model)
            confidence = finding.get("confidence")
            if confidence is None:
                confidence = finding.get("ai_confidence")
            if confidence is None:
                confidence = 0.5
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except ValueError:
                    confidence = 0.5

            # 🔥 Handle verification status
            is_verified = finding.get("is_verified", False)
            if finding.get("verdict") == "confirmed":
                is_verified = True

            # 🔥 Handle PoC information
            poc_data = finding.get("poc", {})
            has_poc = bool(poc_data)
            poc_code = None
            poc_description = None
            poc_steps = None

            if isinstance(poc_data, dict):
                poc_description = poc_data.get("description")
                poc_steps = poc_data.get("steps")
                poc_code = poc_data.get("payload") or poc_data.get("code")
            elif isinstance(poc_data, str):
                poc_description = poc_data

            # 🔥 Handle verification details
            verification_method = finding.get("verification_method")
            verification_result = None
            vr = {
                "details": finding.get("verification_details"),
                "verdict": finding.get("verdict"),
                "verification_status": finding.get("verification_status"),
                "verification_note": finding.get("verification_note"),
                "failure_reason": finding.get("failure_reason"),
            }
            vr = {k: v for k, v in vr.items() if v is not None}
            if vr:
                verification_result = vr

            # FIX-BUG-A: Extract verification_status for DB storage
            raw_vstatus = str(finding.get('verification_status') or finding.get('verdict') or 'needs_context').lower().strip()
            if raw_vstatus in ('confirmed', 'verified', 'true_positive'):
                db_verification_status = 'confirmed'
            elif raw_vstatus == 'static_confirmed':
                db_verification_status = 'static_confirmed'
            elif raw_vstatus == 'false_positive':
                db_verification_status = 'false_positive'
            elif raw_vstatus == 'not_reproducible':
                db_verification_status = 'not_reproducible'
            else:
                db_verification_status = 'needs_context'

            # 🔥 Handle CWE and CVSS
            cwe_id = finding.get("cwe_id") or finding.get("cwe")
            cvss_score = finding.get("cvss_score") or finding.get("cvss")
            if isinstance(cvss_score, str):
                try:
                    cvss_score = float(cvss_score)
                except ValueError:
                    cvss_score = None

            db_finding = AgentFinding(
                id=str(uuid4()),
                task_id=task_id,
                vulnerability_type=type_enum,
                severity=severity_enum,
                title=title[:500] if title else "Unknown Vulnerability",
                description=description[:5000] if description else "",
                file_path=file_path[:500] if file_path else None,
                line_start=line_start,
                line_end=line_end,
                code_snippet=code_snippet[:10000] if code_snippet else None,
                suggestion=suggestion[:5000] if suggestion else None,
                is_verified=is_verified,
                verification_status=db_verification_status,
                ai_confidence=confidence,  # 🔥 FIX: Use ai_confidence, not confidence
                status=FindingStatus.VERIFIED if is_verified else FindingStatus.NEW,
                # 🔥 Additional fields
                has_poc=has_poc,
                poc_code=poc_code,
                poc_description=poc_description,
                poc_steps=poc_steps,
                verification_method=verification_method,
                verification_result=verification_result,
                sandbox_attempts=finding.get("sandbox_attempts"),
                cvss_score=cvss_score,
                # References for CWE
                references=[{"cwe": cwe_id}] if cwe_id else None,
                # 🔥 P3: 发现来源追踪 — Semgrep vs LLM
                matched_rule_code=finding.get("matched_rule_code"),
                matched_pattern=finding.get("matched_pattern"),
                finding_metadata=(
                    finding.get("finding_metadata")
                    or ({"discovery_source": "semgrep"} if finding.get("matched_rule_code") else {"discovery_source": "llm"})
                ),
            )
            db.add(db_finding)
            saved_count += 1
            logger.debug(f"[SaveFindings] Prepared finding: {title[:50]}... ({severity_enum})")

        except Exception as e:
            logger.warning(f"Failed to save finding: {e}, data: {finding}")
            import traceback
            logger.debug(f"[SaveFindings] Traceback: {traceback.format_exc()}")

    logger.info(f"Successfully prepared {saved_count} findings for commit")

    try:
        await db.commit()
        logger.info(f"[SaveFindings] Successfully committed {saved_count} findings to database")
    except Exception as e:
        logger.error(f"Failed to commit findings: {e}")
        await db.rollback()

    return saved_count


def _calculate_security_score(findings: list[dict]) -> float:
    """计算安全评分"""
    if not findings:
        return 100.0

    # 基于发现的严重程度计算扣分
    deductions = {
        "critical": 25,
        "high": 15,
        "medium": 8,
        "low": 3,
        "info": 1,
    }

    total_deduction = 0
    for f in findings:
        if isinstance(f, dict):
            sev = f.get("severity", "low")
            total_deduction += deductions.get(sev, 3)

    score = max(0, 100 - total_deduction)
    return float(score)


async def _recalc_task_counters_from_db(db: AsyncSession, task: AgentTask, task_id: str) -> None:
    """从已落库的 AgentFinding 重查计数器，保证 task 表与 findings 表一致。

    修复 D1/D2/D3：
    - D1: 严重度计数器（critical/high/medium/low_count）改为 DB GROUP BY severity 重查，
          替代遍历含幻觉 finding 的原始列表做 += 累加（旧逻辑无归零、含被过滤的幻觉 finding）。
    - D2: files_with_findings 改为 COUNT(DISTINCT file_path) WHERE file_path IS NOT NULL。
    - D3: verified_count 仅统计 verification_status in (confirmed/verified/true_positive)
          AND is_verified=True，排除 not_reproducible/false_positive。

    必须在 _save_findings 落库 commit 之后调用，否则查不到数据。
    """
    from sqlalchemy import func, select

    from app.models.agent_task import AgentFinding

    # 先归零所有计数器（避免脏值残留）
    task.critical_count = 0
    task.high_count = 0
    task.medium_count = 0
    task.low_count = 0

    # D1: 严重度计数器 - GROUP BY severity
    severity_stmt = select(AgentFinding.severity, func.count()).where(
        AgentFinding.task_id == task_id
    ).group_by(AgentFinding.severity)
    severity_rows = (await db.execute(severity_stmt)).all()
    for sev, cnt in severity_rows:
        sev_lower = (sev or "low").lower()
        if sev_lower == "critical":
            task.critical_count = cnt
        elif sev_lower == "high":
            task.high_count = cnt
        elif sev_lower == "medium":
            task.medium_count = cnt
        elif sev_lower == "low":
            task.low_count = cnt

    # D2: files_with_findings - COUNT(DISTINCT file_path) 排除空路径
    files_stmt = select(func.count(func.distinct(AgentFinding.file_path))).where(
        AgentFinding.task_id == task_id,
        AgentFinding.file_path.isnot(None),
        AgentFinding.file_path != "",
    )
    task.files_with_findings = (await db.execute(files_stmt)).scalar() or 0

    # D3: verified_count - 仅 confirmed（_save_findings 已把 verified/true_positive 映射为 confirmed）
    #      且 is_verified=True，排除 not_reproducible/false_positive
    from app.models.agent_task import VerificationStatus
    verified_stmt = select(func.count()).where(
        AgentFinding.task_id == task_id,
        AgentFinding.is_verified == True,  # noqa: E712
        AgentFinding.verification_status == VerificationStatus.CONFIRMED,
    )
    task.verified_count = (await db.execute(verified_stmt)).scalar() or 0

    # D3b: static_confirmed_count - 代码推理确认数（未动态复现，不计入 verified_count）
    static_stmt = select(func.count()).where(
        AgentFinding.task_id == task_id,
        AgentFinding.is_verified == True,  # noqa: E712
        AgentFinding.verification_status == VerificationStatus.STATIC_CONFIRMED,
    )
    task.static_confirmed_count = (await db.execute(static_stmt)).scalar() or 0


async def _get_verification_status_breakdown(db: AsyncSession, task_id: str) -> dict:
    """Q1: 聚合任务下 AgentFinding 的 verification_status 分布。

    返回 {confirmed, not_reproducible, needs_context, false_positive} 四类计数。
    用于前端展示完整验证状态分布，避免仅展示 verified_count 导致用户误解。
    verified_count 严格语义（仅 confirmed 且 is_verified=True）保持不变。
    """
    from sqlalchemy import func, select

    from app.models.agent_task import AgentFinding

    breakdown = {
        "confirmed": 0,
        "static_confirmed": 0,
        "not_reproducible": 0,
        "needs_context": 0,
        "false_positive": 0,
    }
    stmt = select(AgentFinding.verification_status, func.count()).where(
        AgentFinding.task_id == task_id
    ).group_by(AgentFinding.verification_status)
    rows = (await db.execute(stmt)).all()
    for status, cnt in rows:
        if status in breakdown:
            breakdown[status] = cnt
    return breakdown


def _calculate_quality_score(
    findings: list[dict],
    verified_count: int,
    coverage_covered: int,
    coverage_total: int,
    saved_count: int | None = None,
) -> float:
    """计算 agent 审计任务质量评分（P4）。

    组成：
    - 验证覆盖率（40%）：verified_count / findings_count
    - 误报率（30%）：1 - false_positive / findings_count
    - 平均置信度（30%）：finding 的 ai_confidence 均值
    无 finding 时返回 100.0（无问题即满分）。

    注意：findings_count 须用落库数（saved_count），与 verified_count 同口径，
    避免原始 findings 列表含幻觉 finding 导致分母虚大、质量分被压低。
    """
    if not findings:
        return 100.0

    # P4-fix: 分母用落库数（与 verified_count 同口径），未提供时回退到 len(findings)
    findings_count = saved_count if saved_count is not None else len(findings)
    findings_count = findings_count or len(findings)
    # 验证覆盖率
    verification_ratio = verified_count / findings_count if findings_count else 0.0
    # 误报率（false_positive_count 从 findings 中 status 推断）
    false_positives = sum(
        1 for f in findings
        if isinstance(f, dict)
        and str(f.get("verification_status", "")).lower() in ("false_positive",)
    )
    false_positive_ratio = false_positives / findings_count if findings_count else 0.0
    # 平均置信度
    confidences = [
        f.get("ai_confidence") or f.get("confidence") or 0
        for f in findings
        if isinstance(f, dict)
    ]
    avg_confidence = (
        sum(c for c in confidences if isinstance(c, (int, float))) / len(confidences)
        if confidences
        else 0.0
    )
    # 覆盖率
    coverage_ratio = coverage_covered / coverage_total if coverage_total else 0.0

    score = (
        verification_ratio * 40
        + (1 - false_positive_ratio) * 30
        + avg_confidence * 30
    )
    # 覆盖率作为调整项（覆盖率低则扣分）
    score = score * (0.5 + 0.5 * coverage_ratio)
    return float(max(0.0, min(100.0, score)))


async def _save_agent_tree(db: AsyncSession, task_id: str) -> None:
    """
    保存 Agent 树到数据库

    🔥 在任务完成前调用，将内存中的 Agent 树持久化到数据库
    """
    from app.models.agent_task import AgentTreeNode
    from app.services.agent.core import agent_registry

    try:
        # FIX: Load task object from DB (function only receives task_id, but needs task.status/started_at/completed_at)
        _result = await db.execute(select(AgentTask).where(AgentTask.id == task_id))
        task = _result.scalar_one_or_none()
        if not task:
            logger.warning(f"[SaveAgentTree] Task {task_id} not found in DB")
            return

        tree = agent_registry.get_task_tree(task_id)
        nodes = tree.get("nodes", {})

        if not nodes:
            logger.warning(f"[SaveAgentTree] No agent nodes to save for task {task_id}")
            return

        logger.info(f"[SaveAgentTree] Saving {len(nodes)} agent nodes for task {task_id}")

        # 计算每个节点的深度
        def get_depth(agent_id: str, visited: set = None) -> int:
            if visited is None:
                visited = set()
            if agent_id in visited:
                return 0
            visited.add(agent_id)
            node = nodes.get(agent_id)
            if not node:
                return 0
            parent_id = node.get("parent_id")
            if not parent_id:
                return 0
            return 1 + get_depth(parent_id, visited)

        saved_count = 0
        for agent_id, node_data in nodes.items():
            # 获取 Agent 实例的统计数据
            agent_instance = agent_registry.get_agent(agent_id)
            iterations = 0
            tool_calls = 0
            tokens_used = 0

            if agent_instance and hasattr(agent_instance, 'get_stats'):
                stats = agent_instance.get_stats()
                iterations = stats.get("iterations", 0)
                tool_calls = stats.get("tool_calls", 0)
                tokens_used = stats.get("tokens_used", 0)

            # 从结果中获取发现数量
            findings_count = 0
            result_summary = None
            if node_data.get("result"):
                result = node_data.get("result", {})
                if isinstance(result, dict):
                    findings_count = len(result.get("findings", []))
                    if result.get("summary"):
                        result_summary = str(result.get("summary"))[:2000]

            # 问题五修复：根据任务终态修正 Agent 节点状态
            # registry 中的状态可能仍为 running（Agent 执行已结束但未更新），需根据任务终态修正
            node_status = node_data.get("status", "unknown")
            _now = datetime.now(UTC)
            node_started = node_data.get("started_at")
            node_finished = node_data.get("finished_at")
            if node_status in ("running", "unknown", None):
                if task.status in [AgentTaskStatus.COMPLETED, AgentTaskStatus.COMPLETED_WITH_GAPS]:
                    node_status = "finished"
                elif task.status == AgentTaskStatus.FAILED:
                    node_status = "failed"
                elif task.status == AgentTaskStatus.CANCELLED:
                    node_status = "cancelled"
                else:
                    node_status = "finished"
            if not node_started:
                node_started = task.started_at
            if not node_finished:
                node_finished = task.completed_at or _now
            _node_duration = None
            if node_started and node_finished:
                try:
                    _node_duration = int((node_finished - node_started).total_seconds() * 1000)
                except Exception:
                    _node_duration = None
            tree_node = AgentTreeNode(
                id=str(uuid4()),
                task_id=task_id,
                agent_id=agent_id,
                agent_name=node_data.get("name", "Unknown"),
                agent_type=node_data.get("type", "unknown"),
                parent_agent_id=node_data.get("parent_id"),
                depth=get_depth(agent_id),
                task_description=node_data.get("task"),
                knowledge_modules=node_data.get("knowledge_modules"),
                status=node_status,
                result_summary=result_summary,
                findings_count=findings_count,
                iterations=iterations,
                tool_calls=tool_calls,
                tokens_used=tokens_used,
                started_at=node_started,
                finished_at=node_finished,
                duration_ms=_node_duration,
            )
            db.add(tree_node)
            saved_count += 1

        await db.commit()
        logger.info(f"[SaveAgentTree] Successfully saved {saved_count} agent nodes to database")

    except Exception as e:
        logger.error(f"[SaveAgentTree] Failed to save agent tree: {e}", exc_info=True)
        await db.rollback()


# ============ API Endpoints ============

@router.post("/", response_model=AgentTaskResponse)
async def create_agent_task(
    request: AgentTaskCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    创建并启动 Agent 审计任务
    """
    # 验证项目
    project = await db.get(Project, request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    assert_can_access_project(current_user, project)

    # 创建任务
    # 🔥 从用户配置读取 RPM 快照，存入 task.agent_config，供任务执行与恢复时使用（task-scoped limiter）
    task_agent_config: dict | None = None
    try:
        user_config = await _get_user_config(db, current_user.id)
        if user_config and user_config.get("otherConfig"):
            rpm_value = user_config["otherConfig"].get("llmRatePerMinute")
            if rpm_value is not None:
                task_agent_config = {"llm_rate_per_minute": int(rpm_value)}
    except Exception as e:
        logger.warning(f"Failed to snapshot RPM for task: {e}")

    task = AgentTask(
        id=str(uuid4()),
        project_id=project.id,
        name=request.name or f"Agent Audit - {datetime.now().strftime('%Y%m%d_%H%M%S')}",
        description=request.description,
        status=AgentTaskStatus.PENDING,
        paused=False,
        paused_at=None,
        pause_reason=None,
        last_error_code=None,
        last_checkpoint_id=None,
        resume_count=0,
        current_phase=AgentTaskPhase.PLANNING,
        target_vulnerabilities=request.target_vulnerabilities,
        verification_level=request.verification_level or "sandbox",
        branch_name=request.branch_name,  # 保存用户选择的分支
        exclude_patterns=request.exclude_patterns,
        target_files=request.target_files,
        max_iterations=request.max_iterations or 50,
        timeout_seconds=request.timeout_seconds or 1800,
        created_by=current_user.id,
        agent_config=task_agent_config,
    )

    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 在后台启动任务（项目根目录在任务内部获取）
    background_tasks.add_task(_execute_agent_task, task.id)

    logger.info(f"Created agent task {task.id} for project {project.name}")

    return task


@router.get("/", response_model=list[AgentTaskResponse])
async def list_agent_tasks(
    project_id: str | None = None,
    status: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取 Agent 任务列表
    """
    # P2-2: 按角色数据范围过滤，替代原来只查 owner_id == current_user.id 的做法。
    # 旧逻辑存在两个 Bug：
    #   1) SUPER_ADMIN 只能看到自己项目下的 Agent 任务，其他用户的看不到
    #   2) ADMIN 看不到下辖用户创建的 Agent 任务
    # 现在改用 core.rbac.build_agent_task_filter，语义与其他 list 接口一致。
    from app.models.user import UserRole
    if current_user.role == UserRole.ADMIN:
        sub_ids = await get_subordinate_user_ids(db, current_user.id)
    else:
        sub_ids = None

    query = select(AgentTask)
    task_filter = build_agent_task_filter(current_user, sub_ids)
    if task_filter is not None:
        query = query.where(task_filter)

    if project_id:
        # 显式指定项目时，同样要保证用户能访问该项目（否则单独用 project_id 就能越权枚举）
        target_project = await db.get(Project, project_id)
        from app.core.rbac import can_access_project
        if not can_access_project(current_user, target_project):
            return []
        query = query.where(AgentTask.project_id == project_id)

    if status:
        try:
            status_enum = AgentTaskStatus(status)
            query = query.where(AgentTask.status == status_enum)
        except ValueError:
            pass

    query = query.order_by(AgentTask.created_at.desc())
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    tasks = result.scalars().all()

    return tasks


@router.get("/{task_id}", response_model=AgentTaskResponse)
async def get_agent_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取 Agent 任务详情
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 检查权限
    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    # 构建响应，确保所有字段都包含
    try:
        # 计算进度百分比
        progress = 0.0
        if hasattr(task, 'progress_percentage'):
            progress = task.progress_percentage
        elif task.status == AgentTaskStatus.COMPLETED:
            progress = 100.0
        elif task.status in [AgentTaskStatus.FAILED, AgentTaskStatus.CANCELLED]:
            progress = 0.0

        # 🔥 从运行中的 Orchestrator 获取实时统计
        total_iterations = task.total_iterations or 0
        tool_calls_count = task.tool_calls_count or 0
        tokens_used = task.tokens_used or 0

        orchestrator = _running_orchestrators.get(task_id)
        if orchestrator and task.status == AgentTaskStatus.RUNNING:
            # 从 Orchestrator 获取统计
            stats = orchestrator.get_stats()
            total_iterations = stats.get("iterations", 0)
            tool_calls_count = stats.get("tool_calls", 0)
            tokens_used = stats.get("tokens_used", 0)

            # 累加子 Agent 的统计
            if hasattr(orchestrator, 'sub_agents'):
                for agent in orchestrator.sub_agents.values():
                    if hasattr(agent, 'get_stats'):
                        sub_stats = agent.get_stats()
                        total_iterations += sub_stats.get("iterations", 0)
                        tool_calls_count += sub_stats.get("tool_calls", 0)
                        tokens_used += sub_stats.get("tokens_used", 0)

        # 手动构建响应数据
        response_data = {
            "id": task.id,
            "project_id": task.project_id,
            "name": task.name,
            "description": task.description,
            "task_type": task.task_type or "agent_audit",
            "status": task.status,
            "paused": bool(getattr(task, "paused", False)),
            "paused_at": getattr(task, "paused_at", None),
            "pause_reason": getattr(task, "pause_reason", None),
            "last_error_code": getattr(task, "last_error_code", None),
            "last_checkpoint_id": getattr(task, "last_checkpoint_id", None),
            "resume_count": int(getattr(task, "resume_count", 0) or 0),
            "current_phase": task.current_phase,
            "current_step": task.current_step,
            "total_files": task.total_files or 0,
            "indexed_files": task.indexed_files or 0,
            "analyzed_files": task.analyzed_files or 0,
            "total_chunks": task.total_chunks or 0,
            "total_iterations": total_iterations,
            "tool_calls_count": tool_calls_count,
            "tokens_used": tokens_used,
            "findings_count": task.findings_count or 0,
            "total_findings": task.findings_count or 0,  # 兼容字段
            "verified_count": task.verified_count or 0,
            "verified_findings": task.verified_count or 0,  # 兼容字段
            "false_positive_count": task.false_positive_count or 0,
            "verification_status_breakdown": await _get_verification_status_breakdown(db, task.id),
            "critical_count": task.critical_count or 0,
            "high_count": task.high_count or 0,
            "medium_count": task.medium_count or 0,
            "low_count": task.low_count or 0,
            "quality_score": float(task.quality_score or 0.0),
            "security_score": float(task.security_score) if task.security_score is not None else None,
            "progress_percentage": progress,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "error_message": task.error_message,
            "audit_scope": task.audit_scope,
            "target_vulnerabilities": task.target_vulnerabilities,
            "verification_level": task.verification_level,
            "exclude_patterns": task.exclude_patterns,
            "target_files": task.target_files,
        }

        # Wave 2 §3.2: 填充 orchestrator_alive 字段。基于 Redis registry 判定；
        # Redis 不可用时字段为 None（前端可保持向后兼容行为，不显示恢复横幅）。
        # 问题 1A 修复（统一存活判定）：Redis 判定为 False 时，兜底检查同进程
        # _running_orchestrators。多 worker 部署下 Redis 可能因故未刷新，但只要本进程
        # 持有 orchestrator 实例，任务就一定在运行，避免误报"任务可能已断开"。
        try:
            from app.services.agent.core.orchestrator_registry import get_registry
            registry = await get_registry()
            _alive = await registry.is_alive(task_id)
            if not _alive and _running_orchestrators.get(task_id) is not None:
                _alive = True
            response_data["orchestrator_alive"] = _alive
        except Exception as e:
            logger.debug(f"[GetTask] orchestrator_alive check failed for {task_id}: {e}")
            # 即便 registry 抛异常，若本进程仍持有 orchestrator，也应视为存活
            response_data["orchestrator_alive"] = (
                True if _running_orchestrators.get(task_id) is not None else None
            )

        return AgentTaskResponse(**response_data)
    except Exception as e:
        logger.error(f"Error serializing task {task_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"序列化任务数据失败: {str(e)}")


@router.post("/{task_id}/cancel")
async def cancel_agent_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    取消 Agent 任务
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    if task.status in [AgentTaskStatus.COMPLETED, AgentTaskStatus.COMPLETED_WITH_GAPS, AgentTaskStatus.FAILED, AgentTaskStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail="任务已结束，无法取消")

    source = request.query_params.get("source") or request.headers.get("referer") or "unknown"
    logger.warning(
        "[Cancel] User requested agent task cancellation: task_id=%s user_id=%s project_id=%s "
        "status=%s phase=%s paused=%s source=%s",
        task_id,
        getattr(current_user, "id", None),
        task.project_id,
        task.status,
        task.current_phase,
        task.paused,
        source,
    )

    # 🔥 0. 立即标记任务为已取消（用于前置操作的取消检查）
    _cancelled_tasks_add(task_id)  # P2-8: TTL dict 版本的 add
    logger.info(f"[Cancel] Added task {task_id} to cancelled set")

    # 🔥 1. 设置 Agent 的取消标志
    runner = _running_tasks.get(task_id)
    if runner:
        runner.cancel()
        logger.info(f"[Cancel] Set cancel flag for task {task_id}")

    # 🔥 2. 通过 agent_registry 取消本任务的子 Agent（C1：只停本任务，不影响并发任务）
    from app.services.agent.core.graph_controller import stop_task_agents
    try:
        # 停止本任务的所有 Agent（包括子 Agent）
        stop_result = stop_task_agents(task_id)
        logger.info(f"[Cancel] Stopped task {task_id} agents: {stop_result}")
    except Exception as e:
        logger.warning(f"[Cancel] Failed to stop agents via registry: {e}")

    # 🔥 3. 强制取消 asyncio Task（立即中断 LLM 调用）
    asyncio_task = _running_asyncio_tasks.get(task_id)
    if asyncio_task and not asyncio_task.done():
        asyncio_task.cancel()
        logger.info(f"[Cancel] Cancelled asyncio task for {task_id}")

    # 更新状态
    task.status = AgentTaskStatus.CANCELLED
    task.completed_at = datetime.now(UTC)
    await db.commit()

    # Wave 1 §2.3 修复：通过 SSE 发出 task_cancel 终态事件，让前端立即感知（原实现
    # 只更新 DB，stream_events 的实时循环在等 task_cancel 事件永远等不到，前端要等
    # 15 秒心跳超时才断开）
    event_manager = _running_event_managers.get(task_id)
    if event_manager is not None:
        try:
            emitter = AgentEventEmitter(task_id, event_manager)
            await emitter.emit_task_cancelled("任务已取消")
        except Exception as e:
            logger.warning(f"[Cancel] Failed to emit task_cancelled for {task_id}: {e}")

    logger.info(f"[Cancel] Task {task_id} cancelled successfully")
    return {"message": "任务已取消", "task_id": task_id}


@router.post("/{task_id}/pause")
async def pause_agent_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """优雅暂停 Agent 审计任务并落最新 checkpoint。"""
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    if task.status == AgentTaskStatus.PAUSED:
        return {
            "message": "任务已暂停",
            "task_id": task_id,
            "checkpoint_id": task.last_checkpoint_id,
        }

    if task.status in [
        AgentTaskStatus.COMPLETED,
        AgentTaskStatus.COMPLETED_WITH_GAPS,
        AgentTaskStatus.FAILED,
        AgentTaskStatus.CANCELLED,
    ]:
        raise HTTPException(status_code=400, detail="任务已结束，无法暂停")

    if task.status != AgentTaskStatus.RUNNING:
        task.status = AgentTaskStatus.PAUSED
        task.paused = True
        task.paused_at = datetime.now(UTC)
        task.pause_reason = "manual"
        task.last_error_code = None
        await db.commit()
        return {
            "message": "任务已暂停",
            "task_id": task_id,
            "checkpoint_id": task.last_checkpoint_id,
        }

    orchestrator = _running_orchestrators.get(task_id)
    if not orchestrator:
        task.status = AgentTaskStatus.PAUSED
        task.paused = True
        task.paused_at = datetime.now(UTC)
        task.pause_reason = "manual"
        task.last_error_code = None
        await db.commit()
        return {
            "message": "任务已暂停",
            "task_id": task_id,
            "checkpoint_id": task.last_checkpoint_id,
        }

    try:
        checkpoint_id = await orchestrator.request_pause(
            task_id=task_id,
            db_session_factory=async_session_factory,
        )
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e

    task.status = AgentTaskStatus.PAUSED
    task.paused = True
    task.paused_at = datetime.now(UTC)
    task.pause_reason = "manual"
    task.last_error_code = None
    task.last_checkpoint_id = checkpoint_id
    await db.commit()

    return {
        "message": "任务已暂停",
        "task_id": task_id,
        "checkpoint_id": checkpoint_id,
    }


@router.post("/{task_id}/resume")
async def resume_agent_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """从最近 checkpoint 恢复 Agent 审计任务。"""
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    if task.status != AgentTaskStatus.PAUSED:
        raise HTTPException(status_code=400, detail="只有已暂停任务可以继续")

    checkpoint_id = task.last_checkpoint_id
    if not checkpoint_id:
        checkpoint_stmt = (
            select(AgentCheckpoint.id)
            .where(AgentCheckpoint.task_id == task_id)
            .order_by(AgentCheckpoint.created_at.desc(), AgentCheckpoint.iteration.desc())
            .limit(1)
        )
        checkpoint_id = (await db.execute(checkpoint_stmt)).scalar_one_or_none()
        if checkpoint_id:
            task.last_checkpoint_id = checkpoint_id

    if not checkpoint_id:
        task.last_checkpoint_id = None

    task.status = AgentTaskStatus.RUNNING
    task.paused = False
    task.paused_at = None
    task.pause_reason = None
    task.last_error_code = None
    task.resume_count = int(getattr(task, "resume_count", 0) or 0) + 1
    await db.commit()

    # P2-5: 用 _launch_task_bg 包装，异常自动打 logger.exception
    _launch_task_bg(
        _execute_agent_task(task_id, resume_checkpoint_id=checkpoint_id),
        task_name=f"resume-{task_id}",
    )

    return {
        "message": "任务已继续",
        "task_id": task_id,
        "checkpoint_id": checkpoint_id,
    }


@router.post("/{task_id}/re-audit")
async def re_audit_agent_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Bug E: Re-audit completed_with_gaps task - re-run verification on unverified findings."""
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    if task.status != AgentTaskStatus.COMPLETED_WITH_GAPS:
        raise HTTPException(
            status_code=400,
            detail="only completed_with_gaps tasks can be re-audited"
        )

    # Find unverified findings
    unverified_result = await db.execute(
        select(AgentFinding)
        .where(AgentFinding.task_id == task_id)
        .where(AgentFinding.is_verified == False)
    )
    unverified_findings = unverified_result.scalars().all()
    if not unverified_findings:
        raise HTTPException(
            status_code=400,
            detail="all findings already verified"
        )

    task.status = AgentTaskStatus.RUNNING
    task.paused = False
    task.resume_count = int(getattr(task, "resume_count", 0) or 0) + 1
    await db.commit()

    # P2-5: 用 _launch_task_bg 包装，异常自动打 logger.exception
    _launch_task_bg(
        _re_audit_task(task_id, [f.id for f in unverified_findings]),
        task_name=f"reaudit-findings-{task_id}",
    )

    return {
        "message": "re-audit started",
        "task_id": task_id,
        "unverified_count": len(unverified_findings),
    }


@router.post("/{task_id}/findings/{finding_id}/reverify")
async def reverify_finding(
    task_id: str,
    finding_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """B4: 重跑单条 finding 的 PoC 验证（直接沙箱重放，不走 LLM 编排）。"""
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    finding = await db.get(AgentFinding, finding_id)
    if not finding or finding.task_id != task_id:
        raise HTTPException(status_code=404, detail="finding not found")
    if not finding.has_poc or not finding.poc_code:
        raise HTTPException(status_code=400, detail="该 finding 没有可重跑的 PoC")

    from app.core.config import settings
    from app.services.agent.tools.sandbox_tool import SandboxConfig, SandboxManager

    network_enabled = bool(getattr(settings, "SANDBOX_NETWORK_ENABLED", False))
    manager = SandboxManager(config=SandboxConfig(network_mode="bridge" if network_enabled else "none"))
    await manager.initialize()
    if not manager.is_available:
        raise HTTPException(status_code=503, detail="沙箱环境不可用")

    # 项目源码目录（任务执行期解压/克隆位置）；已被收尾清理时重新准备源码
    host_project_dir = "/tmp/lanjian/" + str(task_id)
    # REQ-CLEAN-2: 任务收尾会清理临时目录；目录缺失时 ZIP 项目重新解压恢复挂载，
    # 仓库项目（需凭证重克隆）要求重新执行审计任务。
    if not os.path.isdir(host_project_dir):
        if project.source_type == "zip":
            await _get_project_root(project, task_id)
        else:
            raise HTTPException(
                status_code=409,
                detail="项目源码已被清理，请重新执行审计任务后再验证",
            )
    result = await manager.execute_poc(
        poc_code=finding.poc_code,
        host_project_dir=host_project_dir,
        timeout=60,
    )

    success = bool(result.get("success"))
    now = datetime.now(UTC)
    attempts = list(finding.sandbox_attempts or [])
    attempts.append({
        "tool": "poc-rerun",
        "success": success,
        "exit_code": result.get("exit_code"),
        "evidence_summary": (result.get("stdout") or result.get("stderr") or "")[:500],
        "reason": "manual-rerun",
    })
    finding.sandbox_attempts = attempts
    finding.verification_result = {
        "method": "poc-rerun",
        "success": success,
        "exit_code": result.get("exit_code"),
        "stdout": (result.get("stdout") or "")[:2000],
        "stderr": (result.get("stderr") or "")[:2000],
        "executed_at": serialize_cst(now),
    }
    from app.models.agent_task import VerificationStatus
    finding.verification_status = (
        VerificationStatus.CONFIRMED if success else VerificationStatus.NOT_REPRODUCIBLE
    )
    finding.is_verified = True
    finding.verified_at = now
    await db.commit()

    return {
        "message": "PoC 重跑完成" if success else "PoC 重跑未复现",
        "finding_id": finding_id,
        "success": success,
        "verification_status": finding.verification_status,
        "exit_code": result.get("exit_code"),
    }


@router.post("/{task_id}/recover")
async def recover_stale_agent_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Bug F: Recover stale running task (process died but DB status not updated)."""
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    if task.status != AgentTaskStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail="only running tasks can be recovered"
        )

    # 问题 1A 修复（统一存活判定）：与 GET /agent-tasks/{id} 保持一致的判定源。
    # 原实现仅检查同进程 _running_orchestrators，在多 worker 部署下，orchestrator
    # 可能运行在其他 worker，此处会误判为"未运行"从而错误 recover。
    # 修复：任一存活证据（Redis alive 键 或 本进程 orchestrator 实例）成立即拒绝 recover。
    orchestrator = _running_orchestrators.get(task_id)
    _alive_by_registry = False
    try:
        from app.services.agent.core.orchestrator_registry import get_registry as _get_registry
        _registry = await _get_registry()
        _alive_by_registry = await _registry.is_alive(task_id)
    except Exception as _e:
        logger.debug(f"[Recover] is_alive check failed for {task_id}: {_e}")

    if orchestrator or _alive_by_registry:
        raise HTTPException(
            status_code=400,
            detail="task is actually running, no recovery needed"
        )

    # Stale running: process died but DB status not updated
    task.status = AgentTaskStatus.PAUSED
    task.paused = True
    task.paused_at = datetime.now(UTC)
    task.pause_reason = "stale_running_recovered"
    task.last_error_code = "stale_running"
    await db.commit()

    return {
        "message": "task recovered to paused state, can resume",
        "task_id": task_id,
    }


@router.delete("/{task_id}")
async def delete_agent_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """删除 Agent 审计任务及其关联资源"""
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    # 注意：PAUSED 属于「已停止但可恢复」的中间态，允许直接删除。
    # 只有真正处于活跃执行阶段的任务才拒绝删除。
    active_statuses = [
        AgentTaskStatus.PENDING,
        AgentTaskStatus.INITIALIZING,
        AgentTaskStatus.RUNNING,
        AgentTaskStatus.PLANNING,
        AgentTaskStatus.INDEXING,
        AgentTaskStatus.ANALYZING,
        AgentTaskStatus.VERIFYING,
        AgentTaskStatus.REPORTING,
    ]
    if task.status in active_statuses:
        raise HTTPException(status_code=400, detail="运行中的 Agent 任务不能直接删除，请先取消任务")

    return await cleanup_agent_task_resources(db, task)


@router.get("/{task_id}/events")
async def stream_agent_events(
    task_id: str,
    request: Request,  # Wave 1 §2.4: 用于 request.is_disconnected() 检测客户端断开
    after_sequence: int = Query(0, ge=0, description="从哪个序号之后开始"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """
    获取 Agent 事件流 (SSE)
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    async def event_generator():
        """生成 SSE 事件流"""
        last_sequence = after_sequence
        poll_interval = 0.5
        max_idle = 300  # 5 分钟无事件后关闭
        idle_time = 0

        while True:
            # Post-Wave 2 修复：删除 Wave 1 §2.4 的 `if await request.is_disconnected(): break`。
            # 该检查会消费 ASGI receive channel，与 Starlette StreamingResponse 内建的
            # listen_for_disconnect 竞争，导致每次 rerender 触发的短暂 fetch abort 会被
            # 误判为永久断开，立即 cancel 整个 stream_events。Starlette 内建的
            # listen_for_disconnect 已自动检测客户端断开并 cancel body_iterator，无需重复。
            # 查询新事件
            async with async_session_factory() as session:
                result = await session.execute(
                    select(AgentEvent)
                    .where(AgentEvent.task_id == task_id)
                    .where(AgentEvent.sequence > last_sequence)
                    .order_by(AgentEvent.sequence)
                    .limit(50)
                )
                events = result.scalars().all()

                # 获取任务状态
                current_task = await session.get(AgentTask, task_id)
                task_status = current_task.status if current_task else None

            if events:
                idle_time = 0
                for event in events:
                    last_sequence = event.sequence
                    # event_type 已经是字符串，不需要 .value
                    event_type_str = str(event.event_type)
                    phase_str = str(event.phase) if event.phase else None

                    data = {
                        "id": event.id,
                        "type": event_type_str,
                        "phase": phase_str,
                        "message": event.message,
                        "sequence": event.sequence,
                        "timestamp": serialize_cst(event.created_at) if event.created_at else None,
                        "progress_percent": event.progress_percent,
                        "tool_name": event.tool_name,
                    }
                    # Wave 1 §2.6: SSE 标准 id: 字段（值 = event.sequence），
                    # 前端可通过 Last-Event-ID header 携带并回补事件
                    yield f"id: {event.sequence}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            else:
                idle_time += poll_interval

            # 检查任务是否结束
            if task_status:
                # task_status 可能是字符串或枚举，统一转换为字符串
                status_str = str(task_status)
                if status_str in _SSE_TERMINAL_STATUSES:
                    yield f"data: {json.dumps({'type': 'task_end', 'status': status_str})}\n\n"
                    break

            # 检查空闲超时
            if idle_time >= max_idle:
                yield f"data: {json.dumps({'type': 'timeout'})}\n\n"
                break

            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/{task_id}/stream")
async def stream_agent_with_thinking(
    task_id: str,
    request: Request,  # Wave 1 §2.4: 用于 request.is_disconnected() 检测客户端断开
    include_thinking: bool = Query(True, description="是否包含 LLM 思考过程"),
    include_tool_calls: bool = Query(True, description="是否包含工具调用详情"),
    after_sequence: int = Query(0, ge=0, description="从哪个序号之后开始"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """
    增强版事件流 (SSE)

    支持:
    - LLM 思考过程的 Token 级流式输出 (仅运行时)
    - 工具调用的详细输入/输出
    - 节点执行状态
    - 发现事件

    优先使用内存中的事件队列 (支持 thinking_token)，
    如果任务未在运行，则回退到数据库轮询 (不支持 thinking_token 复盘)。
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    # 定义 SSE 格式化函数
    def format_sse_event(event_data: dict[str, Any]) -> str:
        """格式化为 SSE 事件"""
        event_type = event_data.get("event_type") or event_data.get("type")

        # 统一字段
        if "type" not in event_data:
            event_data["type"] = event_type

        # Wave 1 §2.6: 心跳除外，其他事件都带 id: {sequence} 行（Last-Event-ID 语义）
        seq = event_data.get("sequence")
        prefix = ""
        if event_type != "heartbeat" and isinstance(seq, int):
            prefix = f"id: {seq}\n"
        return f"{prefix}event: {event_type}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"

    async def enhanced_event_generator():
        """生成增强版 SSE 事件流"""
        # 1. 检查任务是否在运行中 (内存)
        event_manager = _running_event_managers.get(task_id)

        if event_manager:
            logger.debug(f"Stream {task_id}: Using in-memory event manager")
            try:
                # 使用 EventManager 的流式接口
                # 过滤选项
                skip_types = set()
                if not include_thinking:
                    skip_types.update(["thinking_start", "thinking_token", "thinking_end"])
                if not include_tool_calls:
                    skip_types.update(["tool_call_start", "tool_call_input", "tool_call_output", "tool_call_end"])

                async for event in event_manager.stream_events(task_id, after_sequence=after_sequence):
                    # Post-Wave 2 修复：删除 Wave 1 §2.4 的 is_disconnected 检查。
                    # 该检查会消费 ASGI receive channel 与 Starlette 内建 listen_for_disconnect
                    # 竞争，前端每次 rerender 引发的短暂 fetch abort 会立即杀掉整个 SSE stream。
                    # 依赖 stream_events 自身对 CancelledError 的捕获（真正断开时会正确关闭）。

                    event_type = event.get("event_type")

                    if event_type in skip_types:
                        continue

                    # 🔥 Debug: 记录 thinking_token 事件
                    if event_type == "thinking_token":
                        token = event.get("metadata", {}).get("token", "")[:20]
                        logger.debug(f"Stream {task_id}: Sending thinking_token: '{token}...'")

                    # 格式化并 yield
                    yield format_sse_event(event)

            except asyncio.CancelledError:
                logger.info(f"[stream_agent_with_thinking] Cancelled for task {task_id}")
                raise
            except Exception as e:
                logger.error(f"In-memory stream error: {e}")
                err_data = {"type": "error", "message": str(e)}
                yield format_sse_event(err_data)

        else:
            logger.debug(f"Stream {task_id}: Task not running, falling back to DB polling")
            # 2. 回退到数据库轮询 (无法获取 thinking_token)
            last_sequence = after_sequence
            poll_interval = 2.0  # 完成的任务轮询可以慢一点
            heartbeat_interval = 15
            max_idle = 60  # 1分钟无事件关闭
            idle_time = 0
            last_heartbeat = 0

            skip_types = set()
            if not include_thinking:
                skip_types.update(["thinking_start", "thinking_token", "thinking_end"])

            while True:
                # Post-Wave 2 修复：删除 is_disconnected 检查（同前面两处原因）。
                try:
                    async with async_session_factory() as session:
                        # 查询新事件
                        result = await session.execute(
                            select(AgentEvent)
                            .where(AgentEvent.task_id == task_id)
                            .where(AgentEvent.sequence > last_sequence)
                            .order_by(AgentEvent.sequence)
                            .limit(100)
                        )
                        events = result.scalars().all()

                        # 获取任务状态
                        current_task = await session.get(AgentTask, task_id)
                        task_status = current_task.status if current_task else None

                    if events:
                        idle_time = 0
                        for event in events:
                            last_sequence = event.sequence
                            event_type = str(event.event_type)

                            if event_type in skip_types:
                                continue

                            # 构建数据
                            data = {
                                "id": event.id,
                                "type": event_type,
                                "phase": str(event.phase) if event.phase else None,
                                "message": event.message,
                                "sequence": event.sequence,
                                "timestamp": serialize_cst(event.created_at) if event.created_at else None,
                            }

                            # 添加详情
                            if include_tool_calls and event.tool_name:
                                data["tool"] = {
                                    "name": event.tool_name,
                                    "input": event.tool_input,
                                    "output": event.tool_output,
                                    "duration_ms": event.tool_duration_ms,
                                }

                            if event.event_metadata:
                                data["metadata"] = event.event_metadata

                            if event.tokens_used:
                                data["tokens_used"] = event.tokens_used

                            yield format_sse_event(data)
                    else:
                        idle_time += poll_interval

                        # 检查是否应该结束
                        if task_status:
                            status_str = str(task_status)
                            # 如果任务已结束（completed/failed/cancelled）或已暂停，结束流
                            if status_str in _SSE_TERMINAL_STATUSES:
                                end_data = {
                                    "type": "task_end",
                                    "status": status_str,
                                    "message": f"任务已{status_str}"
                                }
                                yield format_sse_event(end_data)
                                break

                    # 心跳
                    last_heartbeat += poll_interval
                    if last_heartbeat >= heartbeat_interval:
                        last_heartbeat = 0
                        yield format_sse_event({"type": "heartbeat", "timestamp": serialize_cst(datetime.now(UTC))})

                    # 超时
                    if idle_time >= max_idle:
                        break

                    await asyncio.sleep(poll_interval)

                except Exception as e:
                    logger.error(f"DB poll stream error: {e}")
                    yield format_sse_event({"type": "error", "message": str(e)})
                    break

    return StreamingResponse(
        enhanced_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        }
    )


@router.get("/{task_id}/events/list", response_model=list[AgentEventResponse])
async def list_agent_events(
    task_id: str,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取 Agent 事件列表
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    result = await db.execute(
        select(AgentEvent)
        .where(AgentEvent.task_id == task_id)
        .where(AgentEvent.sequence > after_sequence)
        .order_by(AgentEvent.sequence.asc())
        .limit(limit)
    )
    events = result.scalars().all()

    # 🔥 Debug logging
    logger.debug(f"[EventsList] Task {task_id}: returning {len(events)} events (after_sequence={after_sequence})")
    if events:
        logger.debug(f"[EventsList] First event: type={events[0].event_type}, seq={events[0].sequence}")
        if len(events) > 1:
            logger.debug(f"[EventsList] Last event: type={events[-1].event_type}, seq={events[-1].sequence}")

    return events


@router.get("/{task_id}/findings", response_model=list[AgentFindingResponse])
async def list_agent_findings(
    task_id: str,
    severity: str | None = None,
    verified_only: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取 Agent 发现列表
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    query = select(AgentFinding).where(AgentFinding.task_id == task_id)

    if severity:
        try:
            sev_enum = VulnerabilitySeverity(severity)
            query = query.where(AgentFinding.severity == sev_enum)
        except ValueError:
            pass

    if verified_only:
        query = query.where(AgentFinding.is_verified == True)

    # 按严重程度排序
    severity_order = {
        VulnerabilitySeverity.CRITICAL: 0,
        VulnerabilitySeverity.HIGH: 1,
        VulnerabilitySeverity.MEDIUM: 2,
        VulnerabilitySeverity.LOW: 3,
        VulnerabilitySeverity.INFO: 4,
    }

    query = query.order_by(AgentFinding.severity, AgentFinding.created_at.desc())
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    findings = result.scalars().all()

    return findings


@router.get("/{task_id}/summary", response_model=TaskSummaryResponse)
async def get_task_summary(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取任务摘要
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    # 获取所有发现
    result = await db.execute(
        select(AgentFinding).where(AgentFinding.task_id == task_id)
    )
    findings = result.scalars().all()

    # 统计
    severity_distribution = {}
    vulnerability_types = {}
    verified_count = 0

    for f in findings:
        # severity 和 vulnerability_type 已经是字符串
        sev = str(f.severity)
        vtype = str(f.vulnerability_type)

        severity_distribution[sev] = severity_distribution.get(sev, 0) + 1
        vulnerability_types[vtype] = vulnerability_types.get(vtype, 0) + 1

        if f.is_verified:
            verified_count += 1

    # 计算持续时间
    duration = None
    if task.started_at and task.completed_at:
        duration = int((task.completed_at - task.started_at).total_seconds())

    # 获取已完成的阶段
    phases_result = await db.execute(
        select(AgentEvent.phase)
        .where(AgentEvent.task_id == task_id)
        .where(AgentEvent.event_type == AgentEventType.PHASE_COMPLETE)
        .distinct()
    )
    phases = [str(p[0]) for p in phases_result.fetchall() if p[0]]

    return TaskSummaryResponse(
        task_id=task_id,
        status=str(task.status),  # status 已经是字符串
        security_score=task.security_score,
        total_findings=len(findings),
        verified_findings=verified_count,
        severity_distribution=severity_distribution,
        vulnerability_types=vulnerability_types,
        duration_seconds=duration,
        phases_completed=phases,
    )


@router.patch("/{task_id}/findings/{finding_id}/status")
async def update_finding_status(
    task_id: str,
    finding_id: str,
    status: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    更新发现状态
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    finding = await db.get(AgentFinding, finding_id)
    if not finding or finding.task_id != task_id:
        raise HTTPException(status_code=404, detail="发现不存在")

    try:
        finding.status = FindingStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的状态: {status}")

    await db.commit()

    return {"message": "状态已更新", "finding_id": finding_id, "status": status}


# ============ Helper Functions ============

async def _get_project_root(
    project: Project,
    task_id: str,
    branch_name: str | None = None,
    github_token: str | None = None,
    gitlab_token: str | None = None,
    gitea_token: str | None = None,  # 🔥 新增
    ssh_private_key: str | None = None,  # 🔥 新增：SSH私钥（用于SSH认证）
    event_emitter: Any | None = None,  # 🔥 新增：用于发送实时日志
) -> str:
    """
    获取项目根目录

    支持两种项目类型：
    - ZIP 项目：解压 ZIP 文件到临时目录
    - 仓库项目：克隆仓库到临时目录

    Args:
        project: 项目对象
        task_id: 任务ID
        branch_name: 分支名称（仓库项目使用，优先于 project.default_branch）
        github_token: GitHub 访问令牌（用于私有仓库）
        gitlab_token: GitLab 访问令牌（用于私有仓库）
        gitea_token: Gitea 访问令牌（用于私有仓库）
        ssh_private_key: SSH私钥（用于SSH认证）
        event_emitter: 事件发送器（用于发送实时日志）

    Returns:
        项目根目录路径

    Raises:
        RuntimeError: 当项目文件获取失败时
    """
    import subprocess
    import zipfile
    from urllib.parse import urlparse, urlunparse

    # 辅助函数：发送事件
    async def emit(message: str, level: str = "info"):
        if event_emitter:
            if level == "info":
                await event_emitter.emit_info(message)
            elif level == "warning":
                await event_emitter.emit_warning(message)
            elif level == "error":
                await event_emitter.emit_error(message)

    # 🔥 辅助函数：检查取消状态
    def check_cancelled():
        if is_task_cancelled(task_id):
            raise asyncio.CancelledError("任务已取消")

    base_path = f"/tmp/lanjian/{task_id}"

    # 确保目录存在且为空
    if os.path.exists(base_path):
        shutil.rmtree(base_path)
    os.makedirs(base_path, exist_ok=True)

    # 🔥 在开始任何操作前检查取消
    check_cancelled()

    # 根据项目类型处理
    if project.source_type == "zip":
        # 🔥 ZIP 项目：解压 ZIP 文件
        check_cancelled()  # 🔥 解压前检查
        await emit("📦 正在解压项目文件...")
        from app.services.zip_storage import load_project_zip

        zip_path = await load_project_zip(project.id)

        if zip_path and os.path.exists(zip_path):
            try:
                check_cancelled()  # 🔥 解压前再次检查
                # P0-2: 先做 Zip Slip / Bomb / symlink 静态检查（assert_safe_zip），
                # 通过后才逐条 extract；循环体内保留取消检查，两者互不干扰。
                from app.utils.safe_extract import SafeExtractError, assert_safe_zip
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    try:
                        safe_dest = assert_safe_zip(zip_ref, base_path)
                    except SafeExtractError as e:
                        logger.error(f"⛔ Agent task rejected malicious ZIP {zip_path}: {e}")
                        await emit(f"❌ ZIP 被安全检查拒绝: {e}", "error")
                        raise RuntimeError(f"ZIP 未通过安全检查: {e}")
                    # 🔥 逐个文件解压，支持取消检查
                    file_list = zip_ref.namelist()
                    for i, file_name in enumerate(file_list):
                        if i % 50 == 0:  # 每50个文件检查一次
                            check_cancelled()
                        zip_ref.extract(file_name, safe_dest)
                logger.info(f"✅ Extracted ZIP project {project.id} to {base_path}")
                await emit("✅ ZIP 文件解压完成")
            except Exception as e:
                logger.error(f"Failed to extract ZIP {zip_path}: {e}")
                await emit(f"❌ 解压失败: {e}", "error")
                raise RuntimeError(f"无法解压项目文件: {e}")
        else:
            logger.warning(f"⚠️ ZIP file not found for project {project.id}")
            await emit("❌ ZIP 文件不存在", "error")
            raise RuntimeError(f"项目 ZIP 文件不存在: {project.id}")

    elif project.source_type == "repository" and project.repository_url:
        # 🔥 仓库项目：优先使用 ZIP 下载（更快更稳定），git clone 作为回退
        repo_url = project.repository_url
        repo_type = project.repository_type or "other"

        await emit(f"🔄 正在获取仓库: {repo_url}")

        # 检测是否为SSH URL（SSH链接不支持ZIP下载）
        is_ssh_url = GitSSHOperations.is_ssh_url(repo_url)

        # 解析仓库 URL 获取 owner/repo
        parsed = urlparse(repo_url)
        path_parts = parsed.path.strip('/').replace('.git', '').split('/')
        if len(path_parts) >= 2:
            owner, repo = path_parts[0], path_parts[1]
        else:
            owner, repo = None, None

        # 构建分支尝试顺序
        branches_to_try = []
        if branch_name:
            branches_to_try.append(branch_name)
        if project.default_branch and project.default_branch not in branches_to_try:
            branches_to_try.append(project.default_branch)
        for common_branch in ["main", "master"]:
            if common_branch not in branches_to_try:
                branches_to_try.append(common_branch)

        download_success = False
        last_error = ""

        # ============ 方案1: 优先使用 ZIP 下载（更快更稳定）============
        # SSH链接直接跳过ZIP下载，使用git clone
        if is_ssh_url:
            logger.info("检测到SSH URL，跳过ZIP下载，直接使用Git克隆")
            await emit("🔑 检测到SSH认证，使用Git克隆...")

        if owner and repo and not is_ssh_url:
            import httpx

            for branch in branches_to_try:
                check_cancelled()

                # 清理目录
                if os.path.exists(base_path) and os.listdir(base_path):
                    shutil.rmtree(base_path)
                os.makedirs(base_path, exist_ok=True)

                # 构建 ZIP 下载 URL
                if repo_type == "github" or "github.com" in repo_url:
                    # GitHub ZIP 下载 URL
                    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
                    headers = {}
                    if github_token:
                        headers["Authorization"] = f"token {github_token}"
                elif repo_type == "gitlab" or "gitlab" in repo_url:
                    # GitLab ZIP 下载 URL（需要对 owner/repo 进行 URL 编码）
                    import urllib.parse
                    project_path = urllib.parse.quote(f"{owner}/{repo}", safe='')
                    gitlab_host = parsed.netloc
                    zip_url = f"https://{gitlab_host}/api/v4/projects/{project_path}/repository/archive.zip?sha={branch}"
                    headers = {}
                    if gitlab_token:
                        headers["PRIVATE-TOKEN"] = gitlab_token
                else:
                    # 其他平台，跳过 ZIP 下载
                    break

                logger.info(f"📦 尝试下载 ZIP 归档 (分支: {branch})...")
                await emit(f"📦 尝试下载 ZIP 归档 (分支: {branch})")

                try:
                    zip_temp_path = f"/tmp/repo_{task_id}_{branch}.zip"

                    async def download_zip():
                        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                            resp = await client.get(zip_url, headers=headers)
                            if resp.status_code == 200:
                                with open(zip_temp_path, 'wb') as f:
                                    f.write(resp.content)
                                return True, None
                            else:
                                return False, f"HTTP {resp.status_code}"

                    # 使用取消检查循环
                    download_task = asyncio.create_task(download_zip())
                    while not download_task.done():
                        check_cancelled()
                        try:
                            success, error = await asyncio.wait_for(asyncio.shield(download_task), timeout=1.0)
                            break
                        except TimeoutError:
                            continue

                    if download_task.done():
                        success, error = download_task.result()

                    if success and os.path.exists(zip_temp_path):
                        # 解压 ZIP
                        check_cancelled()
                        with zipfile.ZipFile(zip_temp_path, 'r') as zip_ref:
                            # ZIP 内通常有一个根目录如 repo-branch/
                            file_list = zip_ref.namelist()
                            # 找到公共前缀
                            if file_list:
                                common_prefix = file_list[0].split('/')[0] + '/'
                                for i, file_name in enumerate(file_list):
                                    if i % 50 == 0:
                                        check_cancelled()
                                    # 去掉公共前缀
                                    if file_name.startswith(common_prefix):
                                        target_path = file_name[len(common_prefix):]
                                        if target_path:  # 跳过空路径（根目录本身）
                                            full_target = os.path.join(base_path, target_path)
                                            if file_name.endswith('/'):
                                                os.makedirs(full_target, exist_ok=True)
                                            else:
                                                os.makedirs(os.path.dirname(full_target), exist_ok=True)
                                                with zip_ref.open(file_name) as src, open(full_target, 'wb') as dst:
                                                    dst.write(src.read())

                        # 清理临时文件
                        os.remove(zip_temp_path)
                        logger.info(f"✅ ZIP 下载成功 (分支: {branch})")
                        await emit(f"✅ 仓库获取成功 (ZIP下载, 分支: {branch})")
                        download_success = True
                        break
                    else:
                        last_error = error or "下载失败"
                        logger.warning(f"ZIP 下载失败 (分支 {branch}): {last_error}")
                        await emit("⚠️ ZIP 下载失败，尝试其他分支...", "warning")
                        # 清理临时文件
                        if os.path.exists(zip_temp_path):
                            os.remove(zip_temp_path)

                except asyncio.CancelledError:
                    logger.info(f"[Cancel] ZIP download cancelled for task {task_id}")
                    raise
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"ZIP 下载异常 (分支 {branch}): {e}")
                    await emit(f"⚠️ ZIP 下载异常: {str(e)[:50]}...", "warning")

        # ============ 方案2: 回退到 git clone ============
        if not download_success:
            if is_ssh_url:
                # SSH链接直接使用git clone，不是"失败"
                pass  # 已在上面输出提示
            else:
                await emit("🔄 ZIP 下载失败，回退到 Git 克隆...")
                logger.info("ZIP download failed, falling back to git clone")

            # 检查 git 是否可用
            try:
                git_check = subprocess.run(
                    ["git", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if git_check.returncode != 0:
                    await emit("❌ Git 未安装", "error")
                    raise RuntimeError("Git 未安装，无法克隆仓库。")
            except FileNotFoundError:
                await emit("❌ Git 未安装", "error")
                raise RuntimeError("Git 未安装，无法克隆仓库。")
            except subprocess.TimeoutExpired:
                await emit("❌ Git 检测超时", "error")
                raise RuntimeError("Git 检测超时")

            # 构建带认证的 URL
            auth_url = repo_url
            if repo_type == "github" and github_token:
                auth_url = urlunparse((
                    parsed.scheme,
                    f"{github_token}@{parsed.netloc}",
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment
                ))
                await emit("🔐 使用 GitHub Token 认证")
            elif repo_type == "gitlab" and gitlab_token:
                auth_url = urlunparse((
                    parsed.scheme,
                    f"oauth2:{gitlab_token}@{parsed.netloc}",
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment
                ))
                await emit("🔐 使用 GitLab Token 认证")
            elif repo_type == "gitea" and gitea_token:
                auth_url = urlunparse((
                    parsed.scheme,
                    f"{gitea_token}@{parsed.netloc}",
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment
                ))
                await emit("🔐 使用 Gitea Token 认证")
            elif is_ssh_url and ssh_private_key:
                await emit("🔐 使用 SSH Key 认证")

            for branch in branches_to_try:
                check_cancelled()

                if os.path.exists(base_path) and os.listdir(base_path):
                    shutil.rmtree(base_path)
                    os.makedirs(base_path, exist_ok=True)

                logger.info(f"🔄 尝试克隆分支: {branch}")
                await emit(f"🔄 尝试克隆分支: {branch}")

                try:
                    # SSH URL使用GitSSHOperations（支持SSH密钥认证）
                    if is_ssh_url and ssh_private_key:
                        async def run_ssh_clone():
                            return await asyncio.to_thread(
                                GitSSHOperations.clone_repo_with_ssh,
                                repo_url, ssh_private_key, base_path, branch
                            )

                        clone_task = asyncio.create_task(run_ssh_clone())
                        while not clone_task.done():
                            check_cancelled()
                            try:
                                result = await asyncio.wait_for(asyncio.shield(clone_task), timeout=1.0)
                                break
                            except TimeoutError:
                                continue

                        if clone_task.done():
                            result = clone_task.result()

                        # GitSSHOperations返回字典格式
                        if result.get('success'):
                            logger.info(f"✅ Git 克隆成功 (SSH, 分支: {branch})")
                            await emit(f"✅ 仓库获取成功 (SSH克隆, 分支: {branch})")
                            download_success = True
                            break
                        else:
                            last_error = result.get('message', '未知错误')
                            logger.warning(f"SSH克隆失败 (分支 {branch}): {last_error[:200]}")
                            await emit(f"⚠️ 分支 {branch} SSH克隆失败...", "warning")
                    else:
                        # HTTPS URL使用标准git clone
                        async def run_clone():
                            return await asyncio.to_thread(
                                subprocess.run,
                                ["git", "clone", "--depth", "1", "--branch", branch, auth_url, base_path],
                                capture_output=True,
                                text=True,
                                timeout=120,
                            )

                        clone_task = asyncio.create_task(run_clone())
                        while not clone_task.done():
                            check_cancelled()
                            try:
                                result = await asyncio.wait_for(asyncio.shield(clone_task), timeout=1.0)
                                break
                            except TimeoutError:
                                continue

                        if clone_task.done():
                            result = clone_task.result()

                        if result.returncode == 0:
                            logger.info(f"✅ Git 克隆成功 (分支: {branch})")
                            await emit(f"✅ 仓库获取成功 (Git克隆, 分支: {branch})")
                            download_success = True
                            break
                        else:
                            last_error = result.stderr
                            logger.warning(f"克隆失败 (分支 {branch}): {last_error[:200]}")
                            await emit(f"⚠️ 分支 {branch} 克隆失败...", "warning")
                except subprocess.TimeoutExpired:
                    last_error = f"克隆分支 {branch} 超时"
                    logger.warning(last_error)
                    await emit(f"⚠️ 分支 {branch} 克隆超时...", "warning")
                except asyncio.CancelledError:
                    logger.info(f"[Cancel] Git clone cancelled for task {task_id}")
                    raise

            # 尝试默认分支
            if not download_success:
                check_cancelled()
                await emit("🔄 尝试使用仓库默认分支...")

                if os.path.exists(base_path) and os.listdir(base_path):
                    shutil.rmtree(base_path)
                    os.makedirs(base_path, exist_ok=True)

                try:
                    # SSH URL使用GitSSHOperations（不指定分支）
                    if is_ssh_url and ssh_private_key:
                        async def run_default_ssh_clone():
                            return await asyncio.to_thread(
                                GitSSHOperations.clone_repo_with_ssh,
                                repo_url, ssh_private_key, base_path, branch
                            )

                        clone_task = asyncio.create_task(run_default_ssh_clone())
                        while not clone_task.done():
                            check_cancelled()
                            try:
                                result = await asyncio.wait_for(asyncio.shield(clone_task), timeout=1.0)
                                break
                            except TimeoutError:
                                continue

                        if clone_task.done():
                            result = clone_task.result()

                        if result.get('success'):
                            logger.info("✅ Git 克隆成功 (SSH, 默认分支)")
                            await emit("✅ 仓库获取成功 (SSH克隆, 默认分支)")
                            download_success = True
                        else:
                            last_error = result.get('message', '未知错误')
                    else:
                        # HTTPS URL使用标准git clone
                        async def run_default_clone():
                            return await asyncio.to_thread(
                                subprocess.run,
                                ["git", "clone", "--depth", "1", auth_url, base_path],
                                capture_output=True,
                                text=True,
                                timeout=120,
                            )

                        clone_task = asyncio.create_task(run_default_clone())
                        while not clone_task.done():
                            check_cancelled()
                            try:
                                result = await asyncio.wait_for(asyncio.shield(clone_task), timeout=1.0)
                                break
                            except TimeoutError:
                                continue

                        if clone_task.done():
                            result = clone_task.result()

                        if result.returncode == 0:
                            logger.info("✅ Git 克隆成功 (默认分支)")
                            await emit("✅ 仓库获取成功 (Git克隆, 默认分支)")
                            download_success = True
                        else:
                            last_error = result.stderr
                except subprocess.TimeoutExpired:
                    last_error = "克隆超时"
                except asyncio.CancelledError:
                    logger.info(f"[Cancel] Git clone cancelled for task {task_id}")
                    raise

        if not download_success:
            # 分析错误原因
            error_msg = "克隆仓库失败"
            if "Authentication failed" in last_error or "401" in last_error:
                error_msg = "认证失败，请检查 GitHub/GitLab Token 配置"
            elif "not found" in last_error.lower() or "404" in last_error:
                error_msg = "仓库不存在或无访问权限"
            elif "Could not resolve host" in last_error:
                error_msg = "无法解析主机名，请检查网络连接"
            elif "Permission denied" in last_error or "403" in last_error:
                error_msg = "无访问权限，请检查仓库权限或 Token"
            else:
                error_msg = f"克隆仓库失败: {last_error[:200]}"

            logger.error(f"❌ {error_msg}")
            await emit(f"❌ {error_msg}", "error")
            raise RuntimeError(error_msg)

    # 验证目录不为空
    if not os.listdir(base_path):
        await emit("❌ 项目目录为空", "error")
        raise RuntimeError(f"项目目录为空，可能是克隆/解压失败: {base_path}")

    # 🔥 智能检测：如果解压后只有一个子目录（常见于 ZIP 文件），
    # 则使用那个子目录作为真正的项目根目录
    # 例如：/tmp/lanjian/UUID/PHP-Project/ -> 返回 /tmp/lanjian/UUID/PHP-Project
    items = os.listdir(base_path)
    # 过滤掉 macOS 产生的 __MACOSX 目录和隐藏文件
    real_items = [item for item in items if not item.startswith('__') and not item.startswith('.')]

    if len(real_items) == 1:
        single_item_path = os.path.join(base_path, real_items[0])
        if os.path.isdir(single_item_path):
            logger.info(f"🔍 检测到单层嵌套目录，自动调整项目根目录: {base_path} -> {single_item_path}")
            await emit(f"🔍 检测到嵌套目录，自动调整为: {real_items[0]}")
            base_path = single_item_path

    await emit(f"📁 项目准备完成: {base_path}")
    return base_path


# ============ Agent Tree API ============

class AgentTreeNodeResponse(BaseModel):
    """Agent 树节点响应"""
    id: str
    agent_id: str
    agent_name: str
    agent_type: str
    parent_agent_id: str | None = None
    depth: int = 0
    task_description: str | None = None
    knowledge_modules: list[str] | None = None
    status: str = "created"
    result_summary: str | None = None
    findings_count: int = 0
    iterations: int = 0
    tokens_used: int = 0
    tool_calls: int = 0
    duration_ms: int | None = None
    children: list["AgentTreeNodeResponse"] = []

    class Config:
        from_attributes = True


class AgentTreeResponse(BaseModel):
    """Agent 树响应"""
    task_id: str
    root_agent_id: str | None = None
    total_agents: int = 0
    running_agents: int = 0
    completed_agents: int = 0
    failed_agents: int = 0
    total_findings: int = 0
    nodes: list[AgentTreeNodeResponse] = []


@router.get("/{task_id}/agent-tree", response_model=AgentTreeResponse)
async def get_agent_tree(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取任务的 Agent 树结构
    
    返回动态 Agent 树的完整结构，包括：
    - 所有 Agent 节点
    - 父子关系
    - 执行状态
    - 发现统计
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    # 尝试从内存中获取 Agent 树（运行中的任务）
    runner = _running_tasks.get(task_id)
    logger.debug(f"[AgentTree API] task_id={task_id}, runner exists={runner is not None}")

    if runner:
        from app.services.agent.core import agent_registry

        tree = agent_registry.get_task_tree(task_id)
        stats = agent_registry.get_task_statistics(task_id)
        logger.debug(f"[AgentTree API] tree nodes={len(tree.get('nodes', {}))}, root={tree.get('root_agent_id')}")
        logger.debug(f"[AgentTree API] 节点详情: {list(tree.get('nodes', {}).keys())}")

        # 🔥 获取 root agent ID，用于判断是否是 Orchestrator
        root_agent_id = tree.get("root_agent_id")

        # 构建节点列表
        nodes = []
        for agent_id, node_data in tree.get("nodes", {}).items():
            # 🔥 从 Agent 实例获取实时统计数据
            iterations = 0
            tool_calls = 0
            tokens_used = 0
            findings_count = 0

            agent_instance = agent_registry.get_agent(agent_id)
            if agent_instance and hasattr(agent_instance, 'get_stats'):
                agent_stats = agent_instance.get_stats()
                iterations = agent_stats.get("iterations", 0)
                tool_calls = agent_stats.get("tool_calls", 0)
                tokens_used = agent_stats.get("tokens_used", 0)

            # 🔥 FIX: 对于 Orchestrator (root agent)，使用 task 的 findings_count
            # 这确保了正确显示聚合的 findings 总数
            if agent_id == root_agent_id:
                findings_count = task.findings_count or 0
            else:
                # 从结果中获取发现数量（对于子 agent）
                if node_data.get("result"):
                    result = node_data.get("result", {})
                    findings_count = len(result.get("findings", []))

            nodes.append(AgentTreeNodeResponse(
                id=node_data.get("id", agent_id),
                agent_id=agent_id,
                agent_name=node_data.get("name", "Unknown"),
                agent_type=node_data.get("type", "unknown"),
                parent_agent_id=node_data.get("parent_id"),
                task_description=node_data.get("task"),
                knowledge_modules=node_data.get("knowledge_modules", []),
                status=node_data.get("status", "unknown"),
                findings_count=findings_count,
                iterations=iterations,
                tool_calls=tool_calls,
                tokens_used=tokens_used,
                children=[],
            ))

        # 🔥 使用 task.findings_count 作为 total_findings，确保一致性
        return AgentTreeResponse(
            task_id=task_id,
            root_agent_id=root_agent_id,
            total_agents=stats.get("total", 0),
            running_agents=stats.get("running", 0),
            completed_agents=stats.get("completed", 0),
            failed_agents=stats.get("failed", 0),
            total_findings=task.findings_count or 0,
            nodes=nodes,
        )

    # 从数据库获取（已完成的任务）
    from app.models.agent_task import AgentTreeNode

    result = await db.execute(
        select(AgentTreeNode)
        .where(AgentTreeNode.task_id == task_id)
        .order_by(AgentTreeNode.depth, AgentTreeNode.created_at)
    )
    db_nodes = result.scalars().all()

    if not db_nodes:
        return AgentTreeResponse(
            task_id=task_id,
            nodes=[],
        )

    # 构建响应
    nodes = []
    root_id = None
    running = 0
    completed = 0
    failed = 0

    for node in db_nodes:
        if node.parent_agent_id is None:
            root_id = node.agent_id

        if node.status == "running":
            running += 1
        elif node.status == "completed":
            completed += 1
        elif node.status == "failed":
            failed += 1

        # 🔥 FIX: 对于 Orchestrator (root agent)，使用 task 的 findings_count
        # 这确保了正确显示聚合的 findings 总数
        if node.parent_agent_id is None:
            # Root agent uses task's total findings
            node_findings_count = task.findings_count or 0
        else:
            node_findings_count = node.findings_count or 0

        nodes.append(AgentTreeNodeResponse(
            id=node.id,
            agent_id=node.agent_id,
            agent_name=node.agent_name,
            agent_type=node.agent_type,
            parent_agent_id=node.parent_agent_id,
            depth=node.depth,
            task_description=node.task_description,
            knowledge_modules=node.knowledge_modules,
            status=node.status,
            result_summary=node.result_summary,
            findings_count=node_findings_count,
            iterations=node.iterations or 0,
            tokens_used=node.tokens_used or 0,
            tool_calls=node.tool_calls or 0,
            duration_ms=node.duration_ms,
            children=[],
        ))

    # 🔥 使用 task.findings_count 作为 total_findings，确保一致性
    return AgentTreeResponse(
        task_id=task_id,
        root_agent_id=root_id,
        total_agents=len(nodes),
        running_agents=running,
        completed_agents=completed,
        failed_agents=failed,
        total_findings=task.findings_count or 0,
        nodes=nodes,
    )


# ============ Checkpoint API ============

class CheckpointResponse(BaseModel):
    """检查点响应"""
    id: str
    agent_id: str
    agent_name: str
    agent_type: str
    iteration: int
    status: str
    total_tokens: int = 0
    tool_calls: int = 0
    findings_count: int = 0
    checkpoint_type: str = "auto"
    checkpoint_name: str | None = None
    created_at: str | None = None

    class Config:
        from_attributes = True


@router.get("/{task_id}/checkpoints", response_model=list[CheckpointResponse])
async def list_checkpoints(
    task_id: str,
    agent_id: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取任务的检查点列表
    
    用于：
    - 查看执行历史
    - 状态恢复
    - 调试分析
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    from app.models.agent_task import AgentCheckpoint

    query = select(AgentCheckpoint).where(AgentCheckpoint.task_id == task_id)

    if agent_id:
        query = query.where(AgentCheckpoint.agent_id == agent_id)

    query = query.order_by(AgentCheckpoint.created_at.desc()).limit(limit)

    result = await db.execute(query)
    checkpoints = result.scalars().all()

    return [
        CheckpointResponse(
            id=cp.id,
            agent_id=cp.agent_id,
            agent_name=cp.agent_name,
            agent_type=cp.agent_type,
            iteration=cp.iteration,
            status=cp.status,
            total_tokens=cp.total_tokens or 0,
            tool_calls=cp.tool_calls or 0,
            findings_count=cp.findings_count or 0,
            checkpoint_type=cp.checkpoint_type or "auto",
            checkpoint_name=cp.checkpoint_name,
            created_at=serialize_cst(cp.created_at) if cp.created_at else None,
        )
        for cp in checkpoints
    ]


@router.get("/{task_id}/checkpoints/{checkpoint_id}")
async def get_checkpoint_detail(
    task_id: str,
    checkpoint_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取检查点详情
    
    返回完整的 Agent 状态数据
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    from app.models.agent_task import AgentCheckpoint

    checkpoint = await db.get(AgentCheckpoint, checkpoint_id)
    if not checkpoint or checkpoint.task_id != task_id:
        raise HTTPException(status_code=404, detail="检查点不存在")

    # 解析状态数据
    state_data = {}
    if checkpoint.state_data:
        try:
            state_data = json.loads(checkpoint.state_data)
        except json.JSONDecodeError:
            pass

    return {
        "id": checkpoint.id,
        "task_id": checkpoint.task_id,
        "agent_id": checkpoint.agent_id,
        "agent_name": checkpoint.agent_name,
        "agent_type": checkpoint.agent_type,
        "parent_agent_id": checkpoint.parent_agent_id,
        "iteration": checkpoint.iteration,
        "status": checkpoint.status,
        "total_tokens": checkpoint.total_tokens,
        "tool_calls": checkpoint.tool_calls,
        "findings_count": checkpoint.findings_count,
        "checkpoint_type": checkpoint.checkpoint_type,
        "checkpoint_name": checkpoint.checkpoint_name,
        "state_data": state_data,
        "metadata": checkpoint.checkpoint_metadata,
        "created_at": serialize_cst(checkpoint.created_at) if checkpoint.created_at else None,
    }


# ============ AI General Chat API ============

class GeneralChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


@router.post("/chat/general")
async def chat_general(
    request: GeneralChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """通用 AI 对话，不依赖特定审计任务"""
    user_config = await _get_user_config(db, current_user.id)
    llm_service = LLMService(user_config=user_config)

    messages = [
        {
            "role": "system",
            "content": "你是蓝鉴 AI 助手。你可以回答关于代码安全审计、漏洞分析、安全开发的问题。使用简体中文。",
        },
        {"role": "user", "content": request.message},
    ]

    result = await llm_service.chat_completion(messages=messages, temperature=0.3, max_tokens=1500)
    return {"reply": result.get("content") or "AI 未返回有效内容。", "usage": result.get("usage")}


# ============ AI Collaboration API ============

@router.post("/{task_id}/chat", response_model=AgentTaskChatResponse)
async def chat_with_agent_task(
    task_id: str,
    request: AgentTaskChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """基于当前审计任务上下文进行 AI 协同问答"""
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    findings_result = await db.execute(
        select(AgentFinding)
        .where(AgentFinding.task_id == task_id)
        .order_by(AgentFinding.created_at.desc())
        .limit(10)
    )
    findings = findings_result.scalars().all()

    events_result = await db.execute(
        select(AgentEvent)
        .where(AgentEvent.task_id == task_id)
        .order_by(AgentEvent.sequence.desc())
        .limit(20)
    )
    recent_events = list(reversed(events_result.scalars().all()))

    user_config = await _get_user_config(db, current_user.id)
    llm_service = LLMService(user_config=user_config)

    findings_summary = "\n".join([
        f"- [{finding.severity}] {finding.title} @ {finding.file_path or 'unknown'}:{finding.line_start or '?'} (verified={finding.is_verified})"
        for finding in findings[:8]
    ]) or "- 暂无漏洞发现"

    events_summary = "\n".join([
        f"- ({event.event_type}) {event.message or 'no-message'}"
        for event in recent_events[-12:]
    ]) or "- 暂无事件日志"

    messages = [
        {
            "role": "system",
            "content": (
                "你是蓝鉴 AI 实时协同审计助手。"
                "以下是当前审计任务的背景数据，仅作为参考上下文存储在对话中。"
                "不要在回复中提及任何任务数据、漏洞数量、文件路径或事件日志，"
                "除非用户的消息中明确要求你输出这些信息（比如用户说了\"总结\"、\"报告\"、\"列出\"、\"分析\"等关键词）。"
                "只管像正常人聊天一样回复用户的问题。使用简体中文。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"任务名称: {task.name or '未命名任务'}\n"
                f"任务状态: {task.status}\n"
                f"当前阶段: {task.current_phase or 'unknown'}\n"
                f"进度: {task.progress_percentage if hasattr(task, 'progress_percentage') else 0}%\n"
                f"发现总数: {task.findings_count or 0}\n"
                f"已验证数: {task.verified_count or 0}\n\n"
                f"最近漏洞摘要:\n{findings_summary}\n\n"
                f"最近事件摘要:\n{events_summary}\n\n"
                f"用户问题: {request.message}"
            ),
        },
    ]

    result = await llm_service.chat_completion(messages=messages, temperature=0.2, max_tokens=1200)

    orchestrator = _running_orchestrators.get(task_id)
    if orchestrator and task.status == AgentTaskStatus.RUNNING:
        try:
            from app.services.agent.core.graph_controller import send_user_message

            send_user_message(orchestrator.agent_id, request.message)
            logger.info(f"[AgentTaskChat] Injected user instruction into running orchestrator for task {task_id}")
        except Exception as inject_error:
            logger.warning(f"[AgentTaskChat] Failed to inject message into running orchestrator: {inject_error}")

    return AgentTaskChatResponse(
        reply=result.get("content") or "AI 未返回有效内容。",
        context_summary={
            "task_id": task.id,
            "task_name": task.name or "未命名任务",
            "status": task.status,
            "phase": task.current_phase,
            "findings_count": len(findings),
            "verified_count": sum(1 for finding in findings if finding.is_verified),
            "recent_events": len(recent_events),
        },
        usage=result.get("usage"),
    )


# ============ Report Generation API ============

@router.get("/{task_id}/report")
async def generate_audit_report(
    task_id: str,
    format: str = Query("markdown", regex="^(markdown|json)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """
    生成审计报告
    
    支持 Markdown 和 JSON 格式
    """
    task = await db.get(AgentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    project = await db.get(Project, task.project_id)
    assert_can_access_project(current_user, project)

    # User 角色不允许下载报告
    if current_user.role == "user":
        raise HTTPException(status_code=403, detail="您的角色不支持下载报告，请升级为 Admin 或联系管理员")

    # 获取此任务的所有发现
    findings = await db.execute(
        select(AgentFinding)
        .where(AgentFinding.task_id == task_id)
        .order_by(
            case(
                (AgentFinding.severity == 'critical', 1),
                (AgentFinding.severity == 'high', 2),
                (AgentFinding.severity == 'medium', 3),
                (AgentFinding.severity == 'low', 4),
                else_=5
            ),
            AgentFinding.created_at.desc()
        )
    )
    findings = findings.scalars().all()

    # 🔥 Helper function to normalize severity for comparison (case-insensitive)
    def normalize_severity(sev: str) -> str:
        return str(sev).lower().strip() if sev else ""

    # Log findings for debugging
    logger.info(f"[Report] Task {task_id}: Found {len(findings)} findings from database")
    if findings:
        for i, f in enumerate(findings[:3]):  # Log first 3
            logger.debug(f"[Report] Finding {i+1}: severity='{f.severity}', title='{f.title[:50] if f.title else 'N/A'}'")

    if format == "json":
        # Enhanced JSON report with full metadata
        return {
            "report_metadata": {
                "task_id": task.id,
                "project_id": task.project_id,
                "project_name": project.name,
                "generated_at": serialize_cst(datetime.now(UTC)),
                "task_status": task.status,
                "duration_seconds": int((task.completed_at - task.started_at).total_seconds()) if task.completed_at and task.started_at else None,
            },
            "summary": {
                "security_score": task.security_score,
                "total_files_analyzed": task.analyzed_files,
                "total_findings": len(findings),
                "verified_findings": sum(1 for f in findings if f.is_verified),
                "severity_distribution": {
                    "critical": sum(1 for f in findings if normalize_severity(f.severity) == 'critical'),
                    "high": sum(1 for f in findings if normalize_severity(f.severity) == 'high'),
                    "medium": sum(1 for f in findings if normalize_severity(f.severity) == 'medium'),
                    "low": sum(1 for f in findings if normalize_severity(f.severity) == 'low'),
                },
                "agent_metrics": {
                    "total_iterations": task.total_iterations,
                    "tool_calls": task.tool_calls_count,
                    "tokens_used": task.tokens_used,
                }
            },
            "findings": [
                {
                    "id": f.id,
                    "title": f.title,
                    "severity": f.severity,
                    "vulnerability_type": f.vulnerability_type,
                    "description": f.description,
                    "file_path": f.file_path,
                    "line_start": f.line_start,
                    "line_end": f.line_end,
                    "code_snippet": f.code_snippet,
                    "is_verified": f.is_verified,
                    "has_poc": f.has_poc,
                    "poc_code": f.poc_code,
                    "poc_description": f.poc_description,
                    "poc_steps": f.poc_steps,
                    "confidence": f.ai_confidence,
                    "suggestion": f.suggestion,
                    "fix_code": f.fix_code,
                    "verification_status": f.verification_status,
                    "verification_result": f.verification_result,
                    "verification_method": f.verification_method,
                    "sandbox_attempts": f.sandbox_attempts,
                    "created_at": serialize_cst(f.created_at) if f.created_at else None,
                } for f in findings
            ]
        }

    # Generate Enhanced Markdown Report
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Calculate statistics
    total = len(findings)
    critical = sum(1 for f in findings if normalize_severity(f.severity) == 'critical')
    high = sum(1 for f in findings if normalize_severity(f.severity) == 'high')
    medium = sum(1 for f in findings if normalize_severity(f.severity) == 'medium')
    low = sum(1 for f in findings if normalize_severity(f.severity) == 'low')
    verified = sum(1 for f in findings if f.is_verified)
    with_poc = sum(1 for f in findings if f.has_poc)

    # Calculate duration
    duration_str = "N/A"
    if task.completed_at and task.started_at:
        duration = (task.completed_at - task.started_at).total_seconds()
        if duration >= 3600:
            duration_str = f"{duration / 3600:.1f} 小时"
        elif duration >= 60:
            duration_str = f"{duration / 60:.1f} 分钟"
        else:
            duration_str = f"{int(duration)} 秒"

    md_lines = []

    # Header
    md_lines.append("# 蓝鉴 安全审计报告")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")

    # Report Info
    md_lines.append("## 报告信息")
    md_lines.append("")
    md_lines.append("| 属性 | 内容 |")
    md_lines.append("|----------|-------|")
    md_lines.append(f"| **项目名称** | {project.name} |")
    md_lines.append(f"| **任务 ID** | `{task.id[:8]}...` |")
    md_lines.append(f"| **生成时间** | {timestamp} |")
    md_lines.append(f"| **任务状态** | {task.status.upper()} |")
    md_lines.append(f"| **耗时** | {duration_str} |")
    md_lines.append("")

    # Executive Summary
    md_lines.append("## 执行摘要")
    md_lines.append("")

    score = task.security_score
    if score is not None:
        if score >= 80:
            score_assessment = "良好 - 建议进行少量优化"
            score_icon = "通过"
        elif score >= 60:
            score_assessment = "中等 - 存在若干问题需要关注"
            score_icon = "警告"
        else:
            score_assessment = "严重 - 需要立即进行修复"
            score_icon = "未通过"
        md_lines.append(f"**安全评分: {int(score)}/100** [{score_icon}]")
        md_lines.append(f"*{score_assessment}*")
    else:
        md_lines.append("**安全评分:** 未计算")
    md_lines.append("")

    # Findings Summary
    md_lines.append("### 漏洞发现概览")
    md_lines.append("")
    md_lines.append("| 严重程度 | 数量 | 已验证 |")
    md_lines.append("|----------|-------|----------|")
    if critical > 0:
        md_lines.append(f"| **严重 (CRITICAL)** | {critical} | {sum(1 for f in findings if normalize_severity(f.severity) == 'critical' and f.is_verified)} |")
    if high > 0:
        md_lines.append(f"| **高危 (HIGH)** | {high} | {sum(1 for f in findings if normalize_severity(f.severity) == 'high' and f.is_verified)} |")
    if medium > 0:
        md_lines.append(f"| **中危 (MEDIUM)** | {medium} | {sum(1 for f in findings if normalize_severity(f.severity) == 'medium' and f.is_verified)} |")
    if low > 0:
        md_lines.append(f"| **低危 (LOW)** | {low} | {sum(1 for f in findings if normalize_severity(f.severity) == 'low' and f.is_verified)} |")
    md_lines.append(f"| **总计** | {total} | {verified} |")
    md_lines.append("")

    # Audit Metrics
    md_lines.append("### 审计指标")
    md_lines.append("")
    md_lines.append(f"- **分析文件数:** {task.analyzed_files} / {task.total_files}")
    md_lines.append(f"- **Agent 迭代次数:** {task.total_iterations}")
    md_lines.append(f"- **工具调用次数:** {task.tool_calls_count}")
    md_lines.append(f"- **Token 消耗:** {task.tokens_used:,}")
    if with_poc > 0:
        md_lines.append(f"- **生成的 PoC:** {with_poc}")
    md_lines.append("")

    # Detailed Findings
    if not findings:
        md_lines.append("## 漏洞详情")
        md_lines.append("")
        md_lines.append("*本次审计未发现安全漏洞。*")
        md_lines.append("")
    else:
        # Group findings by severity
        severity_map = {
            'critical': '严重 (Critical)',
            'high': '高危 (High)',
            'medium': '中危 (Medium)',
            'low': '低危 (Low)'
        }

        for severity_level, severity_name in severity_map.items():
            severity_findings = [f for f in findings if normalize_severity(f.severity) == severity_level]
            if not severity_findings:
                continue

            md_lines.append(f"## {severity_name} 漏洞")
            md_lines.append("")

            for i, f in enumerate(severity_findings, 1):
                verified_badge = "[已验证]" if f.is_verified else "[未验证]"
                poc_badge = " [含 PoC]" if f.has_poc else ""

                md_lines.append(f"### {severity_level.upper()}-{i}: {f.title}")
                md_lines.append("")
                md_lines.append(f"**{verified_badge}**{poc_badge} | 类型: `{f.vulnerability_type}`")
                md_lines.append("")

                if f.file_path:
                    location = f"`{f.file_path}"
                    if f.line_start:
                        location += f":{f.line_start}"
                        if f.line_end and f.line_end != f.line_start:
                            location += f"-{f.line_end}"
                    location += "`"
                    md_lines.append(f"**位置:** {location}")
                    md_lines.append("")

                if f.ai_confidence:
                    md_lines.append(f"**AI 置信度:** {int(f.ai_confidence * 100)}%")
                    md_lines.append("")

                if f.description:
                    md_lines.append("**漏洞描述:**")
                    md_lines.append("")
                    md_lines.append(f.description)
                    md_lines.append("")

                if f.code_snippet:
                    # 🔥 v2.1: 增强语言检测，避免默认 python 标记错误
                    lang = "text"  # 默认使用 text 而非 python
                    if f.file_path:
                        ext = f.file_path.split('.')[-1].lower()
                        lang_map = {
                            # Python
                            'py': 'python', 'pyw': 'python', 'pyi': 'python',
                            # JavaScript/TypeScript
                            'js': 'javascript', 'mjs': 'javascript', 'cjs': 'javascript',
                            'ts': 'typescript', 'mts': 'typescript',
                            'jsx': 'jsx', 'tsx': 'tsx',
                            # Web
                            'html': 'html', 'htm': 'html',
                            'css': 'css', 'scss': 'scss', 'sass': 'sass', 'less': 'less',
                            'vue': 'vue', 'svelte': 'svelte',
                            # Backend
                            'java': 'java', 'kt': 'kotlin', 'kts': 'kotlin',
                            'go': 'go', 'rs': 'rust',
                            'rb': 'ruby', 'erb': 'erb',
                            'php': 'php', 'phtml': 'php',
                            # C-family
                            'c': 'c', 'h': 'c',
                            'cpp': 'cpp', 'cc': 'cpp', 'cxx': 'cpp', 'hpp': 'cpp',
                            'cs': 'csharp',
                            # Shell/Script
                            'sh': 'bash', 'bash': 'bash', 'zsh': 'zsh',
                            'ps1': 'powershell', 'psm1': 'powershell',
                            # Config
                            'json': 'json', 'yaml': 'yaml', 'yml': 'yaml',
                            'toml': 'toml', 'ini': 'ini', 'cfg': 'ini',
                            'xml': 'xml', 'xhtml': 'xml',
                            # Database
                            'sql': 'sql',
                            # Other
                            'md': 'markdown', 'markdown': 'markdown',
                            'sol': 'solidity',
                            'swift': 'swift',
                            'r': 'r', 'R': 'r',
                            'lua': 'lua',
                            'pl': 'perl', 'pm': 'perl',
                            'ex': 'elixir', 'exs': 'elixir',
                            'erl': 'erlang',
                            'hs': 'haskell',
                            'scala': 'scala', 'sc': 'scala',
                            'clj': 'clojure', 'cljs': 'clojure',
                            'dart': 'dart',
                            'groovy': 'groovy', 'gradle': 'groovy',
                        }
                        lang = lang_map.get(ext, 'text')
                    md_lines.append("**漏洞代码:**")
                    md_lines.append("")
                    md_lines.append(f"```{lang}")
                    md_lines.append(f.code_snippet.strip())
                    md_lines.append("```")
                    md_lines.append("")

                if f.suggestion:
                    md_lines.append("**修复建议:**")
                    md_lines.append("")
                    md_lines.append(f.suggestion)
                    md_lines.append("")

                if f.fix_code:
                    md_lines.append("**参考修复代码:**")
                    md_lines.append("")
                    md_lines.append(f"```{lang if f.file_path else 'text'}")
                    md_lines.append(f.fix_code.strip())
                    md_lines.append("```")
                    md_lines.append("")

                # 🔥 添加 PoC 详情
                if f.has_poc:
                    md_lines.append("**概念验证 (PoC):**")
                    md_lines.append("")

                    if f.poc_description:
                        md_lines.append(f"*{f.poc_description}*")
                        md_lines.append("")

                    if f.poc_steps:
                        md_lines.append("**复现步骤:**")
                        md_lines.append("")
                        for step_idx, step in enumerate(f.poc_steps, 1):
                            md_lines.append(f"{step_idx}. {step}")
                        md_lines.append("")

                    if f.poc_code:
                        md_lines.append("**PoC 代码:**")
                        md_lines.append("")
                        md_lines.append("```")
                        md_lines.append(f.poc_code.strip())
                        md_lines.append("```")
                        md_lines.append("")

                md_lines.append("---")
                md_lines.append("")

    # Remediation Priority
    if critical > 0 or high > 0:
        md_lines.append("## 修复优先级建议")
        md_lines.append("")
        md_lines.append("基于已发现的漏洞，我们建议按以下优先级进行修复：")
        md_lines.append("")
        priority_idx = 1
        if critical > 0:
            md_lines.append(f"{priority_idx}. **立即修复:** 处理 {critical} 个严重漏洞 - 可能造成严重影响")
            priority_idx += 1
        if high > 0:
            md_lines.append(f"{priority_idx}. **高优先级:** 在 1 周内修复 {high} 个高危漏洞")
            priority_idx += 1
        if medium > 0:
            md_lines.append(f"{priority_idx}. **中优先级:** 在 2-4 周内修复 {medium} 个中危漏洞")
            priority_idx += 1
        if low > 0:
            md_lines.append(f"{priority_idx}. **低优先级:** 在日常维护中处理 {low} 个低危漏洞")
            priority_idx += 1
        md_lines.append("")

    # Footer
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("*本报告由 蓝鉴 - AI 驱动的安全分析系统生成*")
    md_lines.append("")
    content = "\n".join(md_lines)

    filename = f"audit_report_{task.id[:8]}_{datetime.now().strftime('%Y%m%d')}.md"

    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )
