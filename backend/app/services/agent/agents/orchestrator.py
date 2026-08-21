"""
Orchestrator Agent (编排层) - LLM 驱动版

LLM 是真正的大脑，全程参与决策！
- LLM 决定下一步做什么
- LLM 决定调度哪个子 Agent
- LLM 决定何时完成
- LLM 根据中间结果动态调整策略

类型: Autonomous Agent with Dynamic Planning
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from ..core.coverage import CoverageMatrix
from ..core.attack_chain import AttackChainAnalyzer
from ..core.cross_round import CrossRoundContext
from ..coverage import CoverageStatus, evaluate_coverage
from ..json_parser import AgentJsonParser
from ..prompts import CORE_SECURITY_PRINCIPLES, MULTI_AGENT_RULES, build_enhanced_prompt
from ..round_strategy import RoundContext
from .base import AgentConfig, AgentPattern, AgentResult, AgentType, BaseAgent, TaskHandoff

logger = logging.getLogger(__name__)


ORCHESTRATOR_SYSTEM_PROMPT = """你是蓝鉴的编排 Agent，负责**自主**协调整个安全审计流程。

## 你的角色
你是整个审计流程的**大脑**，不是一个机械执行者。你需要：
1. 自主思考和决策
2. 根据观察结果动态调整策略
3. 决定何时调用哪个子 Agent
4. 判断何时审计完成

## 你可以调度的子 Agent
1. **recon**: 信息收集 Agent - 分析项目结构、技术栈、入口点
2. **analysis**: 分析 Agent - 深度代码审计、漏洞检测
3. **verification**: 验证 Agent - 验证发现的漏洞、生成 PoC

## 你可以使用的操作

### 1. 调度子 Agent（单个或批量）
# 单个调度：
Action: dispatch_agent
Action Input: {"agent": "recon|analysis|verification", "task": "具体任务描述", "context": "任务上下文"}

# 批量并行调度（推荐同时分析多个独立维度时使用）：
Action: dispatch_agent
Action Input: {"agents": [
  {"agent": "analysis", "task": "审计 D1 注入维度", "context": "..."},
  {"agent": "verification", "task": "验证 JWT 漏洞", "context": "..."}
]}

### 2. 汇总发现
```
Action: summarize
Action Input: {"findings": [...], "analysis": "你的分析"}
```

### 3. 完成审计
```
Action: finish
Action Input: {"conclusion": "审计结论", "findings": [...], "recommendations": [...]}
```

## 工作方式
每一步，你需要：

1. **Thought**: 分析当前状态，思考下一步应该做什么
   - 目前收集到了什么信息？
   - 还需要了解什么？
   - 应该深入分析哪些地方？
   - 有什么发现需要验证？

2. **Action**: 选择一个操作
3. **Action Input**: 提供操作参数

## 输出格式
每一步必须严格按照以下格式：

```
Thought: [你的思考过程]
Action: [dispatch_agent|summarize|finish]
Action Input: [JSON 参数]
```

## 审计策略建议
- 先用 recon Agent 了解项目全貌（只需调度一次）
- 根据 recon 结果，让 analysis Agent 重点审计高风险区域
- 发现可疑漏洞后，用 verification Agent 验证
- 随时根据新发现调整策略，不要机械执行
- 当你认为审计足够全面时，选择 finish

## 重要原则
1. **你是大脑，不是执行器** - 每一步都要思考
2. **动态调整** - 根据发现调整策略
3. **主动决策** - 不要等待，主动推进
4. **质量优先** - 宁可深入分析几个真实漏洞，不要浅尝辄止
5. **避免重复** - 每个 Agent 通常只需要调度一次，如果结果不理想，尝试其他 Agent 或直接完成审计

## 处理子 Agent 结果
- 子 Agent 返回的 Observation 包含它们的分析结果
- 即使结果看起来不完整，也要基于已有信息继续推进
- 不要反复调度同一个 Agent 期望得到不同结果
- 如果 recon 完成后，应该调度 analysis 进行深度分析
- 如果 analysis 完成后有发现，可以调度 verification 验证
- 如果没有更多工作要做，使用 finish 结束审计

现在，基于项目信息开始你的审计工作！"""


# === Semgrep helper functions ===

def _map_semgrep_severity(sev: str) -> str:
    """Map Semgrep severity to internal severity."""
    mapping = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    return mapping.get(sev.upper(), "medium")


def _map_semgrep_to_vuln_type(check_id: str) -> str:
    """Map Semgrep check_id to vulnerability type."""
    cid = check_id.lower()
    if "sql" in cid or "injection" in cid:
        return "injection"
    if "xss" in cid or "cross-site" in cid:
        return "xss"
    if "path-traversal" in cid or "directory" in cid:
        return "path_traversal"
    if "hardcode" in cid or "secret" in cid or "password" in cid:
        return "hardcoded_secret"
    if "ssrf" in cid:
        return "ssrf"
    if "deserial" in cid:
        return "deserialization"
    if "xxe" in cid:
        return "xxe"
    if "crypto" in cid or "cipher" in cid or "hash" in cid:
        return "weak_crypto"
    if "auth" in cid or "jwt" in cid or "session" in cid:
        return "auth_bypass"
    if "command" in cid or "exec" in cid or "subprocess" in cid:
        return "command_injection"
    if "redirect" in cid:
        return "open_redirect"
    if "cors" in cid or "csrf" in cid:
        return "csrf"
    return "other"


@dataclass
class AgentStep:
    """执行步骤"""
    thought: str
    action: str
    action_input: dict[str, Any]
    observation: str | None = None
    sub_agent_result: AgentResult | None = None


class AgentExecutionPaused(Exception):
    def __init__(
        self,
        checkpoint_id: str,
        reason: str = "manual",
        error_code: str | None = None,
    ) -> None:
        super().__init__("agent execution paused")
        self.checkpoint_id = checkpoint_id
        self.reason = reason
        self.error_code = error_code


class OrchestratorAgent(BaseAgent):
    """
    编排 Agent - LLM 驱动版
    
    LLM 全程参与决策：
    1. LLM 思考当前状态
    2. LLM 决定下一步操作
    3. 执行操作，获取结果
    4. LLM 分析结果，决定下一步
    5. 重复直到 LLM 决定完成
    """

    def __init__(
        self,
        llm_service: Any,
        tools: dict[str, Any],
        event_emitter: Any = None,
        sub_agents: dict[str, BaseAgent] | None = None,
        tracer: Any = None,
        task_id: str | None = None,
        llm_rate_per_minute: int | None = None,
    ) -> None:
        # 组合增强的系统提示词，注入多Agent协作规则和核心安全原则
        # 🔥 v3.1: 使用 build_enhanced_prompt 注入防幻觉、覆盖率矩阵、控制驱动审计等方法论
        full_system_prompt = build_enhanced_prompt(
            base_prompt=f"{ORCHESTRATOR_SYSTEM_PROMPT}\n\n{MULTI_AGENT_RULES}",
            include_principles=True,
            include_priorities=False,   # Orchestrator 不直接分析漏洞，不需要优先级
            include_tools=False,        # Orchestrator 不直接使用工具
            include_validation=True,    # 文件验证规则
            include_anti_hallucination=True,   # ✅ P0-1: 防幻觉规则
            include_coverage_matrix=True,      # ✅ P0-2: D1-D10 覆盖率矩阵
            include_control_driven=True,       # ✅ P0-3: 控制驱动审计方法论
            include_contract=True,             # Agent 合约
        )

        config = AgentConfig(
            name="Orchestrator",
            agent_type=AgentType.ORCHESTRATOR,
            pattern=AgentPattern.REACT,  # 改为 ReAct 模式！
            max_iterations=20,
            system_prompt=full_system_prompt,
        )
        super().__init__(
            config,
            llm_service,
            tools,
            event_emitter,
            task_id=task_id,
            llm_rate_per_minute=llm_rate_per_minute,
        )

        self.sub_agents = sub_agents or {}
        self._conversation_history: list[dict[str, str]] = []
        self._steps: list[AgentStep] = []
        self._all_findings: list[dict] = []

        # 🔥 Tracer 遥测支持
        self.tracer = tracer

        # 🔥 存储运行时上下文，用于传递给子 Agent
        self._runtime_context: dict[str, Any] = {}

        # 🔥 跟踪已调度的 Agent 任务，避免重复调度
        self._dispatched_tasks: dict[str, int] = {}  # agent_name -> dispatch_count

        # 🔥 保存各个 Agent 的完整结果，用于传递给后续 Agent
        self._agent_results: dict[str, dict[str, Any]] = {}  # agent_name -> full result data
        self._sub_agent_total_iterations: int = 0
        self._sub_agent_total_tool_calls: int = 0
        self._sub_agent_total_tokens: int = 0

        # 🔥 保存各个 Agent 返回的 TaskHandoff，用于 Agent 间通信
        self._agent_handoffs: dict[str, TaskHandoff] = {}  # agent_name -> TaskHandoff

        # 🔥 弹性终止门禁状态
        self._verification_retry_count: int = 0
        self._verification_max_retries: int = 2
        self._verification_all_confirmed: bool = False
        self._turn_reserve_prompted: bool = False
        self._coverage_gap_prompted: bool = False
        self._recon_initial_findings: list[dict] = []
        self._search_registry: dict[str, set] = {
            "files_read": set(),
            "grep_patterns": set(),
        }
        self._hard_coverage_block_count: int = 0
        self._coverage_bypassed: bool = False  # 安全阀是否放行（覆盖率不足但超过拦截上限）
        self._coverage_bypass_info: dict[str, Any] = {}  # 放行时的覆盖率缺口信息
        self._semgrep_force_verified: bool = False  # P3: Semgrep 发现是否已强制通过验证门禁
        self._semgrep_hot_files: list[str] = []
        self._semgrep_findings: list[dict[str, Any]] = []
        self._full_verification_dispatched: bool = False
        # R4: 连续被"无沙箱证据"门禁拒绝 finish 的次数；达上限后停止强制重派
        self._finish_gate_rejections: int = 0
        # R6: 门禁拒绝/兜底原因，收尾时写入 agent_tasks.observations
        self._gate_observations: list[dict[str, Any]] = []

        self._pause_requested: bool = False
        self._pause_future: asyncio.Future[str] | None = None
        self._pause_db_session_factory: Any = None
        self._pause_task_id: str | None = None
        self._loop_index: int = 0

    async def request_pause(
        self,
        task_id: str,
        db_session_factory: Any,
        timeout_seconds: float = 30.0,
    ) -> str:
        if self._pause_future and not self._pause_future.done():
            return await asyncio.wait_for(self._pause_future, timeout=timeout_seconds)

        self._pause_task_id = task_id
        self._pause_db_session_factory = db_session_factory
        self._pause_requested = True
        self._pause_future = asyncio.get_running_loop().create_future()

        try:
            return await asyncio.wait_for(self._pause_future, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            # 兜底：超时也强制落 checkpoint，保证手动暂停最终成功（不再抛 504）。
            # 即使 orchestrator 卡在 LLM 调用，暂停请求也能在 timeout 内完成。
            try:
                checkpoint_id = await self._flush_pause_checkpoint()
            except Exception:
                checkpoint_id = None
            self._pause_requested = False
            if self._pause_future and not self._pause_future.done():
                # 用 checkpoint_id 兜底 fulfilled，避免 future 仍 pending
                self._pause_future.set_result(checkpoint_id)
            return checkpoint_id

    def export_resume_state(self) -> dict[str, Any]:
        search_registry = self._search_registry or {}
        return {
            "iteration_index": int(self._loop_index),
            "conversation_history": list(self._conversation_history or []),
            "steps": [
                {
                    "thought": step.thought,
                    "action": step.action,
                    "action_input": step.action_input,
                    "observation": step.observation,
                }
                for step in (self._steps or [])
            ],
            "all_findings": list(self._all_findings or []),
            "agent_results": dict(self._agent_results or {}),
            "dispatched_tasks": dict(self._dispatched_tasks or {}),
            "sub_agent_total_iterations": int(getattr(self, "_sub_agent_total_iterations", 0)),
            "sub_agent_total_tool_calls": int(getattr(self, "_sub_agent_total_tool_calls", 0)),
            "sub_agent_total_tokens": int(getattr(self, "_sub_agent_total_tokens", 0)),
            "search_registry": {
                "files_read": list(search_registry.get("files_read", set())),
                "grep_patterns": list(search_registry.get("grep_patterns", set())),
            },
            "hard_coverage_block_count": int(getattr(self, "_hard_coverage_block_count", 0)),
            "coverage_bypassed": bool(getattr(self, "_coverage_bypassed", False)),
            "coverage_bypass_info": dict(getattr(self, "_coverage_bypass_info", {}) or {}),
            "semgrep_force_verified": bool(getattr(self, "_semgrep_force_verified", False)),
            "semgrep_hot_files": list(getattr(self, "_semgrep_hot_files", []) or []),
            "semgrep_findings": list(getattr(self, "_semgrep_findings", []) or []),
        }

    def load_resume_state(self, state: dict[str, Any]) -> int:
        self._conversation_history = list(state.get("conversation_history") or [])
        self._steps = [
            AgentStep(
                thought=str(s.get("thought") or ""),
                action=str(s.get("action") or ""),
                action_input=dict(s.get("action_input") or {}),
                observation=s.get("observation"),
            )
            for s in (state.get("steps") or [])
            if isinstance(s, dict)
        ]
        self._all_findings = list(state.get("all_findings") or [])
        self._agent_results = dict(state.get("agent_results") or {})
        self._dispatched_tasks = dict(state.get("dispatched_tasks") or {})
        self._sub_agent_total_iterations = int(state.get("sub_agent_total_iterations") or 0)
        self._sub_agent_total_tool_calls = int(state.get("sub_agent_total_tool_calls") or 0)
        self._sub_agent_total_tokens = int(state.get("sub_agent_total_tokens") or 0)

        search_registry = state.get("search_registry") or {}
        self._search_registry = {
            "files_read": set(search_registry.get("files_read") or []),
            "grep_patterns": set(search_registry.get("grep_patterns") or []),
        }

        self._hard_coverage_block_count = int(state.get("hard_coverage_block_count") or 0)
        self._coverage_bypassed = bool(state.get("coverage_bypassed") or False)
        self._coverage_bypass_info = dict(state.get("coverage_bypass_info") or {})
        self._semgrep_force_verified = bool(state.get("semgrep_force_verified") or False)
        self._semgrep_hot_files = list(state.get("semgrep_hot_files") or [])
        self._semgrep_findings = list(state.get("semgrep_findings") or [])

        return int(state.get("iteration_index") or 0)

    async def _flush_pause_checkpoint(self) -> str:
        if not self._pause_db_session_factory or not self._pause_task_id:
            raise RuntimeError("pause db session factory or task_id missing")

        from app.models.agent_task import AgentCheckpoint

        async with self._pause_db_session_factory() as session:
            checkpoint = AgentCheckpoint(
                task_id=self._pause_task_id,
                agent_id=self.agent_id,
                agent_name=self.name,
                agent_type=self.agent_type.value,
                parent_agent_id=None,
                state_data=self.state.model_dump_json(),
                iteration=int(self._iteration or 0),
                status="paused",
                total_tokens=int(self._total_tokens or 0),
                tool_calls=int(self._tool_calls or 0),
                findings_count=len(self._all_findings or []),
                checkpoint_type="manual",
                checkpoint_name="pause",
                checkpoint_metadata={"resume_state": self.export_resume_state()},
            )
            session.add(checkpoint)
            await session.commit()
            await session.refresh(checkpoint)
            return checkpoint.id

    async def _maybe_pause(self) -> None:
        if not self._pause_requested or not self._pause_future:
            return
        if self._pause_future.done():
            return

        try:
            checkpoint_id = await self._flush_pause_checkpoint()
            self._pause_requested = False
            self._pause_future.set_result(checkpoint_id)
            for agent in (self.sub_agents or {}).values():
                if hasattr(agent, "cancel"):
                    agent.cancel()
            raise AgentExecutionPaused(checkpoint_id)
        except Exception as e:
            self._pause_requested = False
            if not self._pause_future.done():
                self._pause_future.set_exception(e)
            raise

    async def _pause_for_recoverable_error(
        self,
        reason: str,
        error_code: str,
        user_message: str,
    ) -> None:
        checkpoint_id = await self._flush_pause_checkpoint()
        await self.emit_event("warning", user_message)
        raise AgentExecutionPaused(
            checkpoint_id=checkpoint_id,
            reason=reason,
            error_code=error_code,
        )

    def _has_valid_sandbox_evidence(self) -> bool:
        """检查是否至少有一条发现具有有效沙箱验证证据。

        Bug C fix: removed is_verified=True bypass. Only accept:
        1. confirmed with actual sandbox_attempts evidence, or
        2. static_confirmed (code reasoning, B3 strict standard)
        """
        for finding in self._all_findings:
            if not isinstance(finding, dict):
                continue
            if finding.get("verification_status") == "confirmed":
                sandbox_attempts = finding.get("sandbox_attempts", [])
                if isinstance(sandbox_attempts, list) and len(sandbox_attempts) > 0:
                    has_success = any(
                        isinstance(a, dict) and a.get("success") is True and a.get("exit_code") == 0
                        and not a.get("fabricated")  # R3: 伪造证据不计入有效证据
                        for a in sandbox_attempts
                    )
                    if has_success:
                        return True
                # Bug C fix: confirmed without sandbox evidence is not enough
            if finding.get("verification_status") == "static_confirmed":
                return True
        return False

    def _record_gate_observation(self, gate: str, reason: str) -> None:
        """R6: 记录门禁拒绝/兜底原因，收尾时写入 agent_tasks.observations。"""
        from datetime import datetime, timezone
        self._gate_observations.append({
            "gate": gate,
            "reason": reason,
            "time": datetime.now(timezone.utc).isoformat(),
        })

    def _evaluate_current_coverage(self) -> Any:
        """基于当前 findings 与文本证据评估软覆盖率。"""
        text_evidence: list[str] = []
        text_evidence.extend(str(step.thought) for step in self._steps if step.thought)
        text_evidence.extend(str(step.observation) for step in self._steps if step.observation)
        text_evidence.extend(
            json.dumps(result, ensure_ascii=False)
            for result in self._agent_results.values()
        )
        return evaluate_coverage(self._all_findings, text_evidence)

    def _convert_recon_high_risk_area_to_finding(self, area: Any) -> dict[str, Any] | None:
        """Recon 高风险区是 Analysis 的上下文线索，不作为漏洞 findings。"""
        return None

    def register_sub_agent(self, name: str, agent: BaseAgent) -> None:
        """注册子 Agent"""
        self.sub_agents[name] = agent

    def cancel(self) -> None:
        """
        取消执行 - 同时取消所有子 Agent
        
        重写父类方法，确保取消信号传播到所有子 Agent
        """
        self._cancelled = True
        logger.info(f"[{self.name}] Cancel requested, propagating to {len(self.sub_agents)} sub-agents")

        # 🔥 传播取消信号到所有子 Agent
        for name, agent in self.sub_agents.items():
            if hasattr(agent, 'cancel'):
                agent.cancel()
                logger.info(f"[{self.name}] Cancelled sub-agent: {name}")

    def _build_coverage_bypass_info(
        self,
        reason: str,
        covered_count: int,
        total_dimensions: int,
        gaps,
        block_count: int,
        extra: dict | None = None,
    ) -> dict:
        """P2: 统一构造 coverage_bypass_info，确保所有放行分支携带完整字段
        (reason/covered_count/total_dimensions/gaps/block_count)，供前端低覆盖率告警。"""
        info = {
            "reason": reason,
            "covered_count": covered_count,
            "total_dimensions": total_dimensions,
            "gaps": list(gaps) if gaps else [],
            "block_count": block_count,
        }
        if extra:
            info.update(extra)
        return info

    async def run(self, input_data: dict[str, Any]) -> AgentResult:
        """
        执行编排任务 - LLM 全程参与！
        
        Args:
            input_data: {
                "project_info": 项目信息,
                "config": 审计配置,
                "project_root": 项目根目录,
                "task_id": 任务ID,
            }
        """
        import time
        start_time = time.time()

        project_info = input_data.get("project_info", {})
        config = input_data.get("config", {})

        # 🔥 保存运行时上下文，用于传递给子 Agent
        self._runtime_context = {
            "project_info": project_info,
            "config": config,
            "project_root": input_data.get("project_root", project_info.get("root", ".")),
            "task_id": input_data.get("task_id"),
        }

        # 🧠 历史审计记忆（同项目往次已确认漏洞线索）
        self._audit_memory: list[dict[str, Any]] = input_data.get("audit_memory") or []

        resume_state = input_data.get("resume_checkpoint")
        start_iteration = 0
        if resume_state and isinstance(resume_state, dict):
            start_iteration = self.load_resume_state(resume_state)
        else:
            initial_message = self._build_initial_message(project_info, config)
            self._conversation_history = [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": initial_message},
            ]

            self._steps = []
            self._all_findings = []
            self._semgrep_hot_files: list[str] = []
            self._semgrep_findings: list[dict[str, Any]] = []
            self._agent_results = {}
            self._sub_agent_total_iterations = 0
            self._sub_agent_total_tool_calls = 0
            self._sub_agent_total_tokens = 0
            self._agent_handoffs = {}
        final_result = None
        error_message = None  # 🔥 跟踪错误信息

        # 🔥 问题二修复：Orchestrator 自身注册到 registry，确保 _save_agent_tree 能获取到节点
        self._register_to_registry(task='Orchestrator 编排审计流程')
        await self.emit_thinking("🧠 Orchestrator Agent 启动，LLM 开始自主编排决策...")

        # Wave 2 §3.2 心跳协程：每 5 秒刷新 Redis 中的 alive_at 字段。
        # 上层前端通过 GET /agent-tasks/{id} 响应的 orchestrator_alive 字段
        # 判定 stale running 任务。心跳失败非致命（fallback 到进程内 dict）。
        _heartbeat_task_id = input_data.get("task_id")
        _heartbeat_alive_task: asyncio.Task | None = None
        if _heartbeat_task_id:
            _heartbeat_alive_task = asyncio.create_task(
                self._pump_orchestrator_alive(_heartbeat_task_id)
            )

        try:

            if not resume_state:
                semgrep_result = {"findings": [], "hot_files": [], "scan_success": False}
                try:
                    logger.info("[Orchestrator] Starting Semgrep prescan...")
                    semgrep_result = await self._run_semgrep_prescan()
                    if semgrep_result.get("scan_success"):
                        n_findings = len(semgrep_result["findings"])
                        n_hot = len(semgrep_result["hot_files"])
                        logger.info(f"[Orchestrator] Semgrep prescan found {n_findings} findings, {n_hot} hot files")
                        await self.emit_event("info",
                            f"Semgrep pre-scan complete: found {n_findings} potential issues, {n_hot} hot files"
                        )
                        self._semgrep_findings = semgrep_result.get("findings", [])
                        self._semgrep_hot_files = semgrep_result.get("hot_files", [])
                except Exception as e:
                    logger.warning(f"[Orchestrator] Semgrep prescan failed (non-fatal): {e}")

                if self._semgrep_hot_files:
                    hot_files_summary = ", ".join(self._semgrep_hot_files[:20])
                    semgrep_lead = (
                        f"## 🔍 Semgrep 预扫描线索\n\n"
                        f"Semgrep 已完成确定性扫描，识别出 {len(self._semgrep_hot_files)} 个热点文件（含潜在安全问题）。\n"
                        f"**热点文件列表**（前20个）:\n{hot_files_summary}\n\n"
                        f"**重要**：这些是 Semgrep 的初步发现，必须由 Analysis Agent 深度验证后才能确认为漏洞。\n"
                        f"请调度 Recon Agent 收集这些热点文件的结构信息，再调度 Analysis Agent 进行深度审计。"
                    )
                    self._conversation_history.append({
                        "role": "user",
                        "content": semgrep_lead,
                    })
                    logger.info(f"[Orchestrator] Injected {len(self._semgrep_hot_files)} Semgrep hot files as leads into context")

                # 🧠 注入历史审计记忆（同项目往次已确认漏洞，作为复查线索）
                if self._audit_memory:
                    try:
                        from app.services.agent.audit_memory import format_memory_lead
                        memory_lead = format_memory_lead(self._audit_memory)
                        if memory_lead:
                            await self.emit_event(
                                "info",
                                f"🧠 历史审计记忆: 注入 {len(self._audit_memory)} 条往次已确认漏洞作为复查线索",
                            )
                            self._conversation_history.append({
                                "role": "user",
                                "content": memory_lead,
                            })
                            logger.info(
                                f"[Orchestrator] Injected {len(self._audit_memory)} "
                                f"historical memory entries into context"
                            )
                    except Exception as e:
                        logger.warning(f"[Orchestrator] Inject audit memory failed (non-fatal): {e}")

            for iteration in range(start_iteration, self.config.max_iterations):
                self._loop_index = iteration
                self._iteration = iteration + 1
                await self._maybe_pause()
                if self.is_cancelled:
                    break

                # P1: token 预算硬门禁 —— 超限优雅降级为 COMPLETED_WITH_GAPS
                if self._check_token_budget_exceeded():
                    from app.services.agent.config import get_agent_config
                    _budget = get_agent_config().token_budget
                    _total = self._total_tokens + self._sub_agent_total_tokens
                    logger.warning(
                        f"[Orchestrator] Token budget exhausted: {_total} tokens, "
                        f"marking as COMPLETED_WITH_GAPS (reason=token_budget_exhausted)"
                    )
                    self._coverage_bypassed = True
                    self._coverage_bypass_info = self._build_coverage_bypass_info(
                        reason="token_budget_exhausted",
                        covered_count=0,
                        total_dimensions=10,
                        gaps=[],
                        block_count=0,
                        extra={"tokens_used": _total, "budget": _budget},
                    )
                    break

                pending_messages = self.check_messages()
                if pending_messages:
                    user_messages = [
                        msg for msg in pending_messages
                        if msg.from_agent == "user" and msg.content.strip()
                    ]
                    for msg in user_messages:
                        self._conversation_history.append({
                            "role": "user",
                            "content": f"用户实时协同指令:\n{msg.content}",
                        })
                    if user_messages:
                        await self.emit_event(
                            "info",
                            f"📨 收到 {len(user_messages)} 条用户协同指令，已并入当前编排上下文"
                        )

                # 🔥 再次检查取消标志（在LLM调用之前）
                if self.is_cancelled:
                    await self.emit_thinking("🛑 任务已取消，停止执行")
                    break

                # 🔥 LLM 调用入口检查暂停请求，避免长调用阻塞手动暂停
                await self._maybe_pause()

                # 调用 LLM 进行思考和决策（流式输出）
                try:
                    llm_output, tokens_this_round = await self.stream_llm_call(
                        self._conversation_history,
                        # 🔥 frequency_penalty/presence_penalty 通过 LLMConfig -> litellm_adapter.stream_complete 生效
                    )
                except asyncio.CancelledError:
                    logger.info(f"[{self.name}] LLM call cancelled")
                    break

                self._total_tokens += tokens_this_round

                # 🔥 检测空响应
                if not llm_output or not llm_output.strip():
                    logger.warning(f"[{self.name}] Empty LLM response")
                    empty_retry_count = getattr(self, '_empty_retry_count', 0) + 1
                    self._empty_retry_count = empty_retry_count
                    if empty_retry_count >= 5:  # 🔥 增加重试次数到5次
                        logger.error(f"[{self.name}] Too many empty responses, stopping")
                        error_message = "连续收到空响应，停止编排"
                        await self.emit_event("error", error_message)
                        break

                    # 🔥 添加短暂延迟，避免快速重试
                    await asyncio.sleep(1.0)

                    # 🔥 更详细的重试提示
                    retry_prompt = f"""收到空响应（第 {empty_retry_count} 次）。请严格按照以下格式输出你的决策：

Thought: [你对当前审计状态的思考]
Action: [dispatch_agent|summarize|finish]
Action Input: {{"参数": "值"}}

当前可调度的子 Agent: {list(self.sub_agents.keys())}
当前已收集发现: {len(self._all_findings)} 个

请立即输出你的下一步决策。"""

                    self._conversation_history.append({
                        "role": "user",
                        "content": retry_prompt,
                    })
                    continue

                # 重置空响应计数器
                self._empty_retry_count = 0

                # 🔥 检查是否是 API 错误（而非格式错误）
                if llm_output.startswith("[API_ERROR:"):
                    # 提取错误类型和消息
                    match = re.match(r"\[API_ERROR:(\w+)\]\s*(.*)", llm_output)
                    if match:
                        error_type = match.group(1)
                        error_message = match.group(2)

                        if error_type == "rate_limit":
                            # 速率限制 - 等待后重试
                            api_retry_count = getattr(self, '_api_retry_count', 0) + 1
                            self._api_retry_count = api_retry_count
                            if api_retry_count >= 3:
                                logger.error(f"[{self.name}] Too many rate limit errors, pausing")
                                await self._pause_for_recoverable_error(
                                    reason="llm_error",
                                    error_code="rate_limit",
                                    user_message=f"API 速率限制重试次数过多，任务已暂停。修复配置或稍后点击继续。详情：{error_message}",
                                )
                            logger.warning(f"[{self.name}] Rate limit hit, waiting before retry ({api_retry_count}/3)")
                            await self.emit_event("warning", f"API 速率限制，等待后重试 ({api_retry_count}/3)")
                            await asyncio.sleep(30)  # 等待 30 秒后重试
                            continue

                        elif error_type == "quota_exceeded":
                            # 配额用尽 - 终止任务
                            logger.error(f"[{self.name}] API quota exceeded, pausing: {error_message}")
                            await self._pause_for_recoverable_error(
                                reason="llm_error",
                                error_code="quota_exceeded",
                                user_message=f"API 配额已用尽，任务已暂停。修复额度后点击继续。详情：{error_message}",
                            )

                        elif error_type == "authentication":
                            # 认证错误 - 终止任务
                            logger.error(f"[{self.name}] API authentication failure, pausing: {error_message}")
                            await self._pause_for_recoverable_error(
                                reason="llm_error",
                                error_code="authentication",
                                user_message=f"API 认证失败，任务已暂停。修复 LLM 配置后点击继续。详情：{error_message}",
                            )

                        elif error_type == "connection":
                            # 连接错误 - 重试
                            api_retry_count = getattr(self, '_api_retry_count', 0) + 1
                            self._api_retry_count = api_retry_count
                            if api_retry_count >= 3:
                                logger.error(f"[{self.name}] Too many connection errors, pausing")
                                await self._pause_for_recoverable_error(
                                    reason="llm_error",
                                    error_code="connection",
                                    user_message=f"API 连接错误重试次数过多，任务已暂停。修复网络后点击继续。详情：{error_message}",
                                )
                            logger.warning(f"[{self.name}] Connection error, retrying ({api_retry_count}/3)")
                            await self.emit_event("warning", f"API 连接错误，重试中 ({api_retry_count}/3)")
                            await asyncio.sleep(5)  # 等待 5 秒后重试
                            continue
                        elif error_type == "circuit_open":
                            logger.error(f"[{self.name}] LLM circuit open, pausing")
                            await self._pause_for_recoverable_error(
                                reason="llm_error",
                                error_code="circuit_open",
                                user_message="LLM 服务熔断中，任务已暂停。修复配置或等待恢复后点击继续。",
                            )

                # 重置 API 重试计数器（成功获取响应后）
                self._api_retry_count = 0

                # 解析 LLM 的决策
                step = self._parse_llm_response(llm_output)

                if not step:
                    # LLM 输出格式不正确，提示重试（放宽阈值，减少误杀）
                    format_retry_count = getattr(self, '_format_retry_count', 0) + 1
                    self._format_retry_count = format_retry_count
                    if format_retry_count >= 5:
                        logger.error(f"[{self.name}] Too many format errors, pausing")
                        await self._pause_for_recoverable_error(
                            reason="format_error",
                            error_code="format_error",
                            user_message="连续格式错误，任务已暂停。请调整模型或提示词后点击继续。",
                        )
                    if format_retry_count >= 3:
                        # 第三次失败时先尝试 LLM 重触发生成，而非直接计数
                        logger.warning(f"[{self.name}] Format error #{format_retry_count}, requesting LLM retry")
                        await self.emit_event("warning", f"格式错误（第{format_retry_count}次），尝试重新生成...")
                        self._conversation_history.append({
                            "role": "user",
                            "content": "之前的格式仍然不正确。请重新思考并严格按照格式输出：只包含 Thought、Action、Action Input 三个字段，Action Input 必须是有效的 JSON。不要包含其他内容。",
                        })
                        continue
                    await self.emit_llm_decision("格式错误", "需要重新输出")
                    self._conversation_history.append({
                        "role": "assistant",
                        "content": llm_output,
                    })
                    self._conversation_history.append({
                        "role": "user",
                        "content": "请按照规定格式输出：Thought + Action + Action Input",
                    })
                    continue

                # 重置格式重试计数器
                self._format_retry_count = 0

                self._steps.append(step)

                # 🔥 发射 LLM 思考内容事件 - 展示编排决策的思考过程
                if step.thought:
                    await self.emit_llm_thought(step.thought, iteration + 1)

                # 添加 LLM 响应到历史
                self._conversation_history.append({
                    "role": "assistant",
                    "content": llm_output,
                })

                # 执行 LLM 决定的操作
                if step.action == "finish":
                    # 🔥 弹性终止门禁：三层门禁保障审计质量
                    has_findings = len(self._all_findings) > 0
                    verification_dispatched = "verification" in self._dispatched_tasks
                    verification_count = self._dispatched_tasks.get("verification", 0)
                    has_sandbox_evidence = self._has_valid_sandbox_evidence()
                    # R4: 连续被门禁拒绝达上限后，停止强制重派 verification，直接放行按覆盖率收尾。
                    # 根治历史"拒绝→重派→再拒绝"的 token 黑洞循环（生产任务 8 轮失控）。
                    try:
                        from app.services.agent.config import get_agent_config
                        max_redispatch = getattr(
                            get_agent_config(), "verification_max_force_redispatch", 3
                        )
                    except Exception:
                        max_redispatch = 3
                    if has_findings and (not verification_dispatched or (verification_count > 0 and not has_sandbox_evidence)):
                        self._finish_gate_rejections += 1
                        self._record_gate_observation(
                            "verification_evidence_gate",
                            f"发现 {len(self._all_findings)} 个漏洞但无有效沙箱证据（第 {self._finish_gate_rejections} 次拒绝）",
                        )
                    if has_findings and (not verification_dispatched or (verification_count > 0 and not has_sandbox_evidence)) and self._finish_gate_rejections < max_redispatch:
                        if not verification_dispatched:
                            await self.emit_event(
                                "warning",
                                f"⚠️ 系统强制干预：发现 {len(self._all_findings)} 个漏洞但未调度沙箱验证，拒绝完成审计"
                            )
                            await self.emit_llm_decision("拒绝完成", "系统强制要求先调度 verification Agent 进行沙箱验证")
                            prompt_suffix = (
                                "请立即调度 verification Agent:\n"
                                "Thought: [我需要调度 verification Agent 进行沙箱验证]\n"
                                "Action: dispatch_agent\n"
                                f"Action Input: {{\"agent\": \"verification\", \"task\": \"验证所有发现的漏洞，使用 sandbox_exec 在沙箱中执行 PoC\", \"context\": \"共有 {len(self._all_findings)} 个漏洞需要验证\"}}"
                            )
                        else:
                            await self.emit_event(
                                "warning",
                                f"⚠️ 系统强制干预：发现 {len(self._all_findings)} 个漏洞，已调度 {verification_count} 次验证但无有效沙箱证据（0/{len(self._all_findings)} 通过验证），拒绝完成审计"
                            )
                            await self.emit_llm_decision("拒绝完成", f"已调度 {verification_count} 次验证但无有效沙箱证据，必须再次调度并确保 sandbox_exec 执行")
                            prompt_suffix = (
                                f"你已调度 {verification_count} 次验证但 0 条发现通过沙箱确认。\n"
                                "请再次调度 verification Agent，并确保使用 sandbox_exec 工具执行 PoC。\n"
                                "仅凭代码分析判断漏洞是不够的，必须在沙箱中实际验证。\n\n"
                                "Action: dispatch_agent\n"
                                f"Action Input: {{\"agent\": \"verification\", \"task\": \"再次验证所有发现的漏洞，必须使用 sandbox_exec 在沙箱中执行 PoC\", \"context\": \"已调度 {verification_count} 次但无沙箱证据，共 {len(self._all_findings)} 个漏洞\"}}"
                            )
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **系统强制干预**: 你发现了 {len(self._all_findings)} 个漏洞但还没有有效的沙箱验证证据！\n\n"
                                "这是不可跳过的步骤。每个漏洞必须通过沙箱验证才能确认其真实性。\n"
                                "仅凭代码分析判断漏洞是不够的，必须使用 sandbox_exec 在 Docker 沙箱中实际验证。\n\n"
                                f"{prompt_suffix}\n\n"
                                "如果你不完成沙箱验证，系统将持续拒绝 finish 操作。"
                            ),
                        })
                        continue
                    elif has_findings and self._finish_gate_rejections >= max_redispatch:
                        # R4 放行：达到上限后不再强制重派，fall-through 到后续门禁链并完成收尾。
                        # 不 continue，避免"再输出 finish → 再 +1 → 再放行"的二次循环。
                        await self.emit_event(
                            "warning",
                            f"⚠️ 验证门禁已达最大重试次数（{max_redispatch} 次），不再强制重派 verification，按覆盖率收尾"
                        )
                        await self.emit_llm_decision(
                            "放行完成",
                            f"已连续拒绝 {max_redispatch} 次仍无沙箱证据，停止强制重派，按当前结果收尾",
                        )
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **系统提示**: 验证门禁已连续拒绝 {max_redispatch} 次，"
                                "沙箱证据仍不可得。系统不再强制你重试 verification，直接完成审计。"
                            ),
                        })

                    # 🔥 P3: Semgrep 发现强制验证 — 工具扫描发现的漏洞也必须经过沙箱确认
                    # 检查未验证的 Semgrep 发现（matched_rule_code 非空 或 matched_pattern 非空）
                    semgrep_findings = [
                        f for f in self._all_findings
                        if f.get("matched_rule_code") or f.get("matched_pattern")
                    ]
                    unverified_semgrep = [
                        f for f in semgrep_findings
                        if f.get("verification_status") not in ("confirmed", "verified", "not_reproducible", "false_positive")
                        and f.get("is_verified") != True
                    ]
                    if unverified_semgrep and not self._semgrep_force_verified:
                        self._semgrep_force_verified = True
                        await self.emit_event(
                            "warning",
                            f"⚠️ Semgrep 门禁: {len(semgrep_findings)} 个 Semgrep 发现中 {len(unverified_semgrep)} 个未验证，强制调度 Verification"
                        )
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **Semgrep 验证门禁**: 工具扫描发现了 {len(semgrep_findings)} 个漏洞，"
                                f"其中 {len(unverified_semgrep)} 个尚未经过沙箱验证。\n\n"
                                "Semgrep 是确定性扫描工具，其发现具有较高的准确率。这些发现必须优先验证。\n\n"
                                "请立即调度 verification Agent 验证这些 Semgrep 发现：\n"
                                f"Action: dispatch_agent\n"
                                f"Action Input: {{\"agent\": \"verification\", "
                                f"\"task\": \"优先验证 Semgrep 发现的 {len(unverified_semgrep)} 个漏洞，"
                                f"使用 sandbox_exec 在沙箱中执行 PoC\"}}\n\n"
                                "你不允许在 Semgrep 发现被验证前完成审计。"
                            ),
                        })
                        continue
                    # 如果 Semgrep 发现仍未验证，持续阻止 finish（不依赖一次性 flag）
                    if unverified_semgrep and self._semgrep_force_verified:
                        await self.emit_event(
                            "warning",
                            f"⚠️ Semgrep 门禁持续拦截: {len(unverified_semgrep)} 个 Semgrep 发现仍未验证（已触发过一次强制调度），拒绝完成"
                        )
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **Semgrep 验证门禁（持续拦截）**: 上一轮已要求你验证 Semgrep 发现，"
                                f"但仍有 {len(unverified_semgrep)} 个未完成验证。\n\n"
                                "你必须调度 verification Agent 并确保 sandbox_exec 实际执行。"
                                "在 Semgrep 发现被验证前，审计不允许完成。"
                            ),
                        })
                        continue

                    # Bug D fix: 全量验证门禁 - 确保所有 findings 都被发送给 Verification
                    # R5: 判定修正——needs_context（未确认/未尝试）视为未验证。
                    # 原逻辑 `not verification_status` 被 analysis 默认写入的 needs_context 击穿，
                    # 导致"确保所有 finding 都送去验证"永不触发（生产任务 4/5 发现从未送验）。
                    UNVERIFIED_TERMINAL = {"confirmed", "static_confirmed", "not_reproducible", "false_positive"}
                    unverified_findings = [
                        f for f in self._all_findings
                        if f.get("verification_status") not in UNVERIFIED_TERMINAL
                        and f.get("is_verified") is not True
                    ]
                    if unverified_findings and verification_count > 0 and not self._full_verification_dispatched:
                        self._full_verification_dispatched = True
                        await self.emit_event(
                            "warning",
                            f"⚠️ 发现 {len(unverified_findings)} 个未验证的漏洞，强制调度 Verification"
                        )
                        unverified_summary = "\n".join(
                            f"- {f.get('file_path', '?')}:{f.get('line_start', 0)} "
                            f"[{f.get('vulnerability_type', '?')}] {f.get('title', '')[:60]}"
                            for f in unverified_findings
                        )
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **全量验证门禁**: 你已发现 {len(self._all_findings)} 个漏洞，"
                                f"但其中 {len(unverified_findings)} 个尚未经过沙箱验证。\n\n"
                                f"未验证的漏洞:\n{unverified_summary}\n\n"
                                "请立即调度 verification Agent 验证这些未验证的漏洞。\n"
                                "Action: dispatch_agent\n"
                                f"Action Input: {{\"agent\": \"verification\", "
                                f"\"task\": \"验证剩余 {len(unverified_findings)} 个未验证的漏洞，"
                                f"必须使用 sandbox_exec\"}}"
                            ),
                        })
                        continue

                    # 🔥 P3: 覆盖率过低时，禁止无休止的 Verification 重试，优先补充 Analysis
                    coverage_check = self._evaluate_current_coverage()
                    if (coverage_check.covered_count < 4
                        and verification_count >= 2
                        and not has_sandbox_evidence):
                        await self.emit_event(
                            "warning",
                            f"⚠️ 覆盖率仅 {coverage_check.covered_count}/10，"
                            f"已调度 {verification_count} 次 Verification 无沙箱通过，"
                            f"强制优先补充 Analysis 覆盖（而非继续重试验证）"
                        )
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **覆盖率优化干预**: 当前仅覆盖 {coverage_check.covered_count}/10 个安全维度。"
                                f"已调度 {verification_count} 次 Verification Agent 但无沙箱通过。"
                                f"继续重试验证不会提升覆盖率。\n\n"
                                "请立即调度 **analysis Agent** 补充审计未覆盖的安全维度：\n"
                                f"Action: dispatch_agent\n"
                                f"Action Input: {{\"agent\": \"analysis\", "
                                f"\"task\": \"补充审计未覆盖的安全维度(D1-D10)，当前仅覆盖 "
                                f"{coverage_check.covered_count}/10。请使用 read_file + search_code +"
                                f"semgrep_scan 系统性地覆盖缺失维度\"}}\n\n"
                                "在覆盖率提升前，不要再调度 verification Agent。"
                            ),
                        })
                        continue

                    coverage_report = self._evaluate_current_coverage()
                    # ✅ FIX: 如果 auto-bypass 已触发（_hard_coverage_block_count >= 3），跳过软门禁
                    if not coverage_report.is_sufficient and self._hard_coverage_block_count < 3:
                        round_context = RoundContext.from_coverage(
                            coverage_report,
                            previous_findings=self._all_findings,
                        )
                        await self.emit_event(
                            "warning",
                            f"⚠️ 覆盖率不足：{coverage_report.covered_count}/10，要求补漏后再完成（第{self._hard_coverage_block_count + 1}次提醒）",
                        )
                        self._conversation_history.append({
                            "role": "user",
                            "content": f"{coverage_report.to_prompt()}\n\n{round_context.to_agent_prompt()}\n\n**注意：你必须调度 Analysis Agent 补充未覆盖的维度后再 finish。当前未覆盖维度已列出，请逐一排查。**",
                        })
                        continue

                    # 硬性覆盖率门禁 - 不可被 LLM 跳过（带逃逸路径）
                    coverage_matrix = CoverageMatrix()
                    for finding in self._all_findings:
                        dim = CoverageMatrix.map_finding_to_dimension(finding.get("vulnerability_type", ""))
                        if dim:
                            coverage_matrix.mark_covered(dim, evidence=finding.get("title", ""))
                    for pattern in self._search_registry.get("grep_patterns", set()):
                        dim = CoverageMatrix.map_pattern_to_dimension(pattern)
                        if dim:
                            coverage_matrix.mark_shallow(dim, evidence=f"grep: {pattern}")
                    hard_coverage = coverage_matrix.to_report()

                    if len(self._all_findings) > 0 and not hard_coverage.is_sufficient and self._hard_coverage_block_count < 3:
                        self._hard_coverage_block_count += 1
                        try:
                            await self.emit_event(
                                "warning",
                                f"⚠️ 覆盖率不足：{hard_coverage.covered_count}/10，"
                                f"D1/D2/D3 必须全部覆盖，要求补漏后再完成（第{self._hard_coverage_block_count}次拦截，最多3次）"
                            )
                        except Exception:
                            logger.warning("Failed to emit coverage warning event")
                        all_gaps = hard_coverage.gaps()
                        gap_detail = "\n".join(f"  - {g}" for g in all_gaps)

                        # 根据缺口数量计算建议的 Agent 数量和任务拆分
                        gap_count = len(all_gaps)
                        if gap_count <= 1:
                            agent_plan = "调度 **1 个** Analysis Agent（20 turns），集中处理所有未覆盖维度。"
                            task_split = [all_gaps]
                        elif gap_count <= 3:
                            agent_plan = f"调度 **2 个** Analysis Agent（各 20 turns），分工处理 {gap_count} 个维度。"
                            split_point = gap_count // 2 + gap_count % 2
                            task_split = [all_gaps[:split_point], all_gaps[split_point:]]
                        else:
                            agent_plan = f"调度 **3 个** Analysis Agent（各 20 turns），分工处理 {gap_count} 个维度。"
                            third = gap_count // 3
                            remainder = gap_count % 3
                            s1 = third + remainder
                            s2 = s1 + third
                            task_split = [all_gaps[:s1], all_gaps[s1:s2], all_gaps[s2:]]

                        # 生成具体的调度指令
                        dispatch_instructions = []
                        for group in task_split:
                            if not group:
                                continue
                            dim_list = ", ".join(group)
                            dispatch_instructions.append(
                                f'```json\n{{"agent": "analysis", "task": "深度审计以下安全维度: {dim_list}。'
                                "请使用 read_file 读取相关代码，使用 search_code 搜索危险函数，"
                                "使用 semgrep_scan 进行精确扫描。每个维度至少找到 1 个 Sink 并追踪数据流。"
                                f'", "context": "补漏轮次: 第{self._hard_coverage_block_count}轮, 重点维度: {dim_list}"}}\n```'
                            )

                        dispatch_examples = "\n\n".join(dispatch_instructions)

                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **覆盖率门禁拦截（第{self._hard_coverage_block_count}/5次）**: "
                                f"当前覆盖率 {hard_coverage.covered_count}/10，未达标。\n\n"
                                f"未覆盖维度（共 {gap_count} 个）:\n{gap_detail}\n\n"
                                f"📊 **调度计划**: {agent_plan}\n\n"
                                f"**请按以下方式调度（可以使用批量并行调度）**:\n\n{dispatch_examples}\n\n"
                                "不允许在覆盖率未达标时直接 finish。"
                            ),
                        })
                        continue
                    elif len(self._all_findings) > 0 and not hard_coverage.is_sufficient and self._hard_coverage_block_count >= 3:
                        logger.warning(
                            f"Coverage gate bypassed after {self._hard_coverage_block_count} blocks: "
                            f"{hard_coverage.covered_count}/10 covered"
                        )
                        try:
                            await self.emit_event("warning", "覆盖率未达标但已超过最大拦截次数，允许完成审计")
                        except Exception:
                            pass
                        # 标记安全阀放行，供 AgentResult.metadata 使用
                        self._coverage_bypassed = True
                        self._coverage_bypass_info = self._build_coverage_bypass_info(
                            reason="coverage_gate_max_blocks_exceeded",
                            covered_count=hard_coverage.covered_count,
                            total_dimensions=10,
                            gaps=hard_coverage.gaps(),
                            block_count=self._hard_coverage_block_count,
                        )
                        # R6: 记录覆盖率兜底原因
                        self._record_gate_observation(
                            "coverage_gate",
                            f"覆盖率 {hard_coverage.covered_count}/10 未达标，"
                            f"连续拦截 {self._hard_coverage_block_count} 次后放行",
                        )

                    # 🔥 LLM 决定完成审计（已通过门禁或无发现）
                    await self.emit_llm_decision("完成审计", "LLM 判断审计已充分完成")
                    await self.emit_llm_complete(
                        f"编排完成，发现 {len(self._all_findings)} 个漏洞",
                        self._total_tokens
                    )
                    final_result = step.action_input
                    break

                elif step.action == "dispatch_agent":
                    # 🔥 LLM 决定调度子 Agent
                    agent_name = step.action_input.get("agent", "unknown")
                    task_desc = step.action_input.get("task", "")
                    await self.emit_llm_decision(
                        f"调度 {agent_name} Agent",
                        f"任务: {task_desc[:100]}"
                    )
                    await self.emit_llm_action("dispatch_agent", step.action_input)

                    observation = await self._dispatch_agent(step.action_input)
                    step.observation = observation

                    # 🔥 子 Agent 执行完成后检查取消状态
                    if self.is_cancelled:
                        logger.info(f"[{self.name}] Cancelled after sub-agent dispatch")
                        break

                    # ✅ FIX: 注入去重提示 - 告诉 LLM 已有哪些发现，避免重复
                    if self._all_findings:
                        dedup_hint = "\n\n## ⚠️ 已有发现（禁止重复报告）\n"
                        for i, ef in enumerate(self._all_findings[-10:], 1):
                            if isinstance(ef, dict):
                                dedup_hint += f"{i}. [{ef.get('severity','?')}] {ef.get('title','?')} @ {ef.get('file_path','?')}:{ef.get('line_start',0)}\n"
                        observation += dedup_hint

                    # ✅ FIX: 注入去重提示 - 告诉 LLM 已有哪些发现，避免重复
                    if self._all_findings:
                        dedup_hint = "\n\n## ⚠️ 已有发现（禁止重复报告）\n"
                        for i, ef in enumerate(self._all_findings[-10:], 1):
                            if isinstance(ef, dict):
                                dedup_hint += f"{i}. [{ef.get('severity','?')}] {ef.get('title','?')} @ {ef.get('file_path','?')}:{ef.get('line_start',0)}\n"
                        observation += dedup_hint

                    # 🔥 发射观察事件
                    await self.emit_llm_observation(observation)

                elif step.action == "summarize":
                    # LLM 要求汇总
                    await self.emit_llm_decision("汇总发现", "LLM 请求查看当前发现汇总")
                    observation = self._summarize_findings()
                    step.observation = observation
                    await self.emit_llm_observation(observation)

                else:
                    observation = f"未知操作: {step.action}，可用操作: dispatch_agent, summarize, finish"
                    await self.emit_llm_decision("未知操作", observation)

                # 添加观察结果到历史
                self._conversation_history.append({
                    "role": "user",
                    "content": f"Observation:\n{step.observation}",
                })

            # 生成最终结果
            duration_ms = int((time.time() - start_time) * 1000)

            # 🔥 如果被取消，返回取消结果
            if self.is_cancelled:
                await self.emit_event(
                    "info",
                    f"🛑 Orchestrator 已取消: {len(self._all_findings)} 个发现, {self._iteration} 轮决策"
                )
                return AgentResult(
                    success=False,
                    error="任务已取消",
                    data={
                        "findings": self._all_findings,
                        "steps": [
                            {
                                "thought": s.thought,
                                "action": s.action,
                                "action_input": s.action_input,
                                "observation": s.observation[:500] if s.observation else None,
                            }
                            for s in self._steps
                        ],
                    },
                    iterations=self._iteration + self._sub_agent_total_iterations,
                    tool_calls=self._tool_calls + self._sub_agent_total_tool_calls,
                    tokens_used=self._total_tokens + self._sub_agent_total_tokens,
                    duration_ms=duration_ms,
                )

            # 🔥 如果有错误，返回失败结果
            if error_message:
                await self.emit_event(
                    "error",
                    f"❌ Orchestrator 失败: {error_message}"
                )
                return AgentResult(
                    success=False,
                    error=error_message,
                    data={
                        "findings": self._all_findings,
                        "steps": [
                            {
                                "thought": s.thought,
                                "action": s.action,
                                "action_input": s.action_input,
                                "observation": s.observation[:500] if s.observation else None,
                            }
                            for s in self._steps
                        ],
                    },
                    iterations=self._iteration + self._sub_agent_total_iterations,
                    tool_calls=self._tool_calls + self._sub_agent_total_tool_calls,
                    tokens_used=self._total_tokens + self._sub_agent_total_tokens,
                    duration_ms=duration_ms,
                )

            await self.emit_event(
                "info",
                f"🎯 Orchestrator 完成: {len(self._all_findings)} 个发现, {self._iteration} 轮决策"
            )

            # 🔥 CRITICAL: Log final findings count before returning

            # 🔥 Semgrep findings 仅作为线索注入子 Agent 上下文，不直接合并到最终结果
            # 避免 Semgrep 原始规则 ID 直接作为 findings 灌水（问题三修复）
            if self._semgrep_findings:
                logger.info(f"[Orchestrator] {len(self._semgrep_findings)} Semgrep findings kept as leads only (not merged into final results)")
            logger.info(f"[Orchestrator] Final result: {len(self._all_findings)} findings collected")
            if len(self._all_findings) == 0:
                logger.warning(f"[Orchestrator] ⚠️ No findings collected! Dispatched agents: {list(self._dispatched_tasks.keys())}, Iterations: {self._iteration}")
            for i, f in enumerate(self._all_findings[:5]):  # Log first 5 for debugging
                logger.debug(f"[Orchestrator] Finding {i+1}: {f.get('title', 'N/A')} - {f.get('vulnerability_type', 'N/A')}")

            # Compute total stats (Orchestrator + all sub-agents)
            _total_iter = self._iteration + self._sub_agent_total_iterations
            _total_tools = self._tool_calls + self._sub_agent_total_tool_calls
            _total_tokens = self._total_tokens + self._sub_agent_total_tokens
            logger.info(f"[Orchestrator] Total stats: iter={_total_iter} (orch={self._iteration}+sub={self._sub_agent_total_iterations}), tools={_total_tools}, tokens={_total_tokens}")


            # 🔥 覆盖率兜底检查：20轮耗尽时安全阀可能未触发，需在此兜底
            if not self._coverage_bypassed and self._all_findings:
                final_coverage = self._evaluate_current_coverage()
                if not final_coverage.is_sufficient:
                    self._coverage_bypassed = True
                    self._coverage_bypass_info = self._build_coverage_bypass_info(
                        reason="orchestrator_max_iterations_exhausted",
                        covered_count=final_coverage.covered_count,
                        total_dimensions=10,
                        gaps=final_coverage.gaps,
                        block_count=self._hard_coverage_block_count,
                    )
                    logger.warning(
                        f"[Orchestrator] Coverage insufficient ({final_coverage.covered_count}/10) "
                        f"after {self._iteration} iterations, marking as coverage_bypassed"
                    )
                    try:
                        await self.emit_event("warning",
                            f"⚠️ 审计轮次已耗尽，覆盖率 {final_coverage.covered_count}/10 未达标，标记为覆盖率不足完成"
                        )
                    except Exception:
                        pass

            # ✅ P1-1: 攻击链分析 - 评估漏洞组合风险
            attack_chains = []
            if len(self._all_findings) >= 2:
                try:
                    chain_analyzer = AttackChainAnalyzer()
                    attack_chains = chain_analyzer.analyze(self._all_findings)
                    if attack_chains:
                        logger.info(f"[Orchestrator] 发现 {len(attack_chains)} 条攻击链")
                        await self.emit_event("info", f"🔗 攻击链分析完成: 发现 {len(attack_chains)} 条组合攻击路径")
                except Exception as e:
                    logger.warning(f"[Orchestrator] Attack chain analysis failed (non-fatal): {e}")

            return AgentResult(
                success=True,
                data={
                    "findings": self._all_findings,
                    "attack_chains": attack_chains,  # ✅ P1-1: 攻击链结果
                    "summary": final_result or self._generate_default_summary(),
                    "observations": list(self._gate_observations),  # R6: 门禁拒绝/兜底原因
                    "steps": [
                        {
                            "thought": s.thought,
                            "action": s.action,
                            "action_input": s.action_input,
                            "observation": s.observation[:500] if s.observation else None,
                        }
                        for s in self._steps
                    ],
                },
                iterations=_total_iter,
                tool_calls=_total_tools,
                tokens_used=_total_tokens,
                duration_ms=duration_ms,
                metadata={
                    "coverage_bypassed": self._coverage_bypassed,
                    "coverage_info": self._coverage_bypass_info,
                },
            )

        except AgentExecutionPaused:
            raise
        except Exception as e:
            logger.error(f"Orchestrator failed: {e}", exc_info=True)
            return AgentResult(
                success=False,
                error=str(e),
            )
        finally:
            # Wave 2 §3.2 停止心跳协程 + 清理 Redis registry key
            if _heartbeat_alive_task is not None and not _heartbeat_alive_task.done():
                _heartbeat_alive_task.cancel()
                try:
                    await _heartbeat_alive_task
                except (asyncio.CancelledError, Exception):
                    pass
            if _heartbeat_task_id:
                try:
                    from app.services.agent.core.orchestrator_registry import get_registry
                    registry = await get_registry()
                    await registry.clear(_heartbeat_task_id)
                except Exception as e:
                    logger.warning(f"[Orchestrator] Failed to clear registry for {_heartbeat_task_id}: {e}")


    async def _pump_orchestrator_alive(self, task_id: str, interval_seconds: int = 5) -> None:
        """Wave 2 §3.2 心跳协程：每 interval_seconds 秒调用 registry.set_alive 刷新 TTL。

        Redis 键 lanjian:orch:{task_id} TTL 为 60 秒（远大于 interval），
        任务进程被杀 / uvicorn --reload 重启后，key 会自然过期，前端通过
        orchestrator_alive=false 感知 stale running。
        """
        try:
            from app.services.agent.core.orchestrator_registry import get_registry
            registry = await get_registry()
        except Exception as e:
            logger.warning(f"[Orchestrator] Alive heartbeat cannot init registry: {e}")
            return

        while True:
            try:
                # Wave 2 Review Finding 2: 单次 set_alive 加 2s 超时，避免 Redis 慢
                # 时 finally 块 cancel 心跳后 await 阻塞过久，拖慢 pause/异常响应
                await asyncio.wait_for(
                    registry.set_alive(task_id, event_manager_local=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[Orchestrator] Alive heartbeat set_alive timed out (>2s) for task {task_id}"
                )
            except asyncio.CancelledError:
                # 取消发生在 set_alive 中：立即退出，不继续循环
                return
            except Exception as e:
                # 单次失败不阻断心跳循环（内部已 fallback）
                logger.debug(f"[Orchestrator] Alive heartbeat set_alive error: {e}")
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                return


    async def _run_semgrep_prescan(self) -> dict[str, Any]:
        """Phase 0: Semgrep full scan before Recon (async subprocess, not sandbox)."""
        import json as _json
        import os
        project_root = self._runtime_context.get("project_root", ".")

        # Build clean env: remove empty proxy vars that crash semgrep OCaml runtime
        _proxy_keys = ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"]
        clean_env = {k: v for k, v in os.environ.items() if not (k in _proxy_keys and not v.strip())}

        # 版本检查：异步子进程，避免阻塞事件循环
        try:
            proc = await asyncio.create_subprocess_exec(
                "semgrep", "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=clean_env,
            )
            try:
                stdout_bytes, _stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=10
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode != 0:
                logger.warning("[Orchestrator] Semgrep not installed, skipping prescan")
                return {"findings": [], "hot_files": [], "scan_success": False}
            semgrep_ver = stdout_bytes.decode(errors="replace").strip() if stdout_bytes else ""
            logger.info(f"[Orchestrator] Semgrep version: {semgrep_ver}")
            await self.emit_event("info", f"Semgrep v{semgrep_ver} detected, starting pre-scan...")
        except FileNotFoundError:
            logger.warning("[Orchestrator] Semgrep not found in PATH, skipping prescan")
            return {"findings": [], "hot_files": [], "scan_success": False}
        except asyncio.TimeoutError:
            logger.warning("[Orchestrator] Semgrep version check timed out, skipping prescan")
            return {"findings": [], "hot_files": [], "scan_success": False}

        all_raw = []
        # 规则集扩展（问题三修复 a）：在 security-audit / owasp-top-ten 基础上
        # 增加 secrets / xss / sql-injection 三个专项规则集，提升预扫描覆盖面。
        # 单个规则集失败/超时已被 try/except 兜住，不影响其余规则集执行。
        rulesets = [
            "p/security-audit",
            "p/owasp-top-ten",
            "p/secrets",
            "p/xss",
            "p/sql-injection",
        ]
        for ruleset in rulesets:
            ruleset_tool_name = f"semgrep_prescan_{ruleset.replace('/', '_').replace('-', '_')}"
            await self.emit_event(
                "tool_call_start",
                f"运行 Semgrep 规则集: {ruleset}",
                metadata={"tool": {"name": ruleset_tool_name, "input": {"ruleset": ruleset}}},
            )
            findings_count = 0
            try:
                findings = await self._run_single_semgrep_ruleset(
                    ruleset, project_root, clean_env
                )
                all_raw.extend(findings)
                findings_count = len(findings)
                await self.emit_event(
                    "info", f"Semgrep {ruleset}: {len(findings)} findings"
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[Orchestrator] Semgrep {ruleset} timed out (180s)"
                )
            except Exception as e:
                logger.warning(
                    f"[Orchestrator] Semgrep {ruleset} failed: {e}"
                )
            finally:
                await self.emit_event(
                    "tool_call_end",
                    f"Semgrep {ruleset} 完成",
                    metadata={
                        "tool": {
                            "name": ruleset_tool_name,
                            "findings_count": findings_count,
                        }
                    },
                )

        seen = set()
        unique = []
        for f in all_raw:
            if not isinstance(f, dict):
                continue
            start = f.get("start", {}) if isinstance(f.get("start"), dict) else {}
            key = (f.get("path", ""), f.get("check_id", ""), start.get("line", 0))
            if key not in seen:
                seen.add(key)
                unique.append(f)

        hot_files = list(set(f.get("path", "") for f in unique if f.get("path")))
        if hot_files:
            await self.emit_event("info", f"Semgrep identified {len(hot_files)} hot files: {', '.join(hot_files[:5])}")

        semgrep_findings = []
        for f in unique:
            start = f.get("start", {}) if isinstance(f.get("start"), dict) else {}
            end = f.get("end", {}) if isinstance(f.get("end"), dict) else {}
            extra = f.get("extra", {}) if isinstance(f.get("extra"), dict) else {}
            semgrep_findings.append({
                "title": f.get("check_id", "unknown"),
                "file_path": f.get("path", ""),
                "line_start": start.get("line", 0),
                "line_end": end.get("line", 0),
                "severity": _map_semgrep_severity(extra.get("severity", "WARNING")),
                "description": extra.get("message", ""),
                "vulnerability_type": _map_semgrep_to_vuln_type(f.get("check_id", "")),
                "code_snippet": (extra.get("lines", "") or "")[:1000],
                "source": "semgrep",
                "verification_method": "semgrep_static_analysis",
                "is_verified": False,
            })

        return {"findings": semgrep_findings, "hot_files": hot_files[:30], "scan_success": True}

    async def _run_single_semgrep_ruleset(
        self, ruleset: str, project_root: str, env: dict
    ) -> list[dict]:
        """异步跑单个 Semgrep 规则集，返回 findings 列表。

        使用 asyncio.create_subprocess_exec 替代同步 subprocess.run，
        避免阻塞事件循环导致 SSE 心跳断连。
        """
        import json as _json

        cmd = [
            "semgrep", "--config", ruleset, "--json", "--quiet",
            "--max-target-bytes", "1000000", project_root,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=180
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        if stdout:
            results = _json.loads(
                stdout[stdout.find("{"):] if "{" in stdout else "{}"
            )
            findings = results.get("results", [])
            logger.info(
                f"[Orchestrator] Semgrep {ruleset}: {len(findings)} findings"
            )
            return findings
        if proc.returncode not in (0, 1):
            logger.warning(
                f"[Orchestrator] Semgrep {ruleset} exit code "
                f"{proc.returncode}: {stderr[:200]}"
            )
        return []

    def _build_initial_message(
        self,
        project_info: dict[str, Any],
        config: dict[str, Any],
    ) -> str:
        """构建初始消息"""
        structure = project_info.get('structure', {})

        # 🔥 检查是否是限定范围的审计
        scope_limited = structure.get('scope_limited', False)
        scope_message = structure.get('scope_message', '')

        msg = f"""请开始对以下项目进行安全审计。

## 项目信息
- 名称: {project_info.get('name', 'unknown')}
- 语言: {project_info.get('languages', [])}
- 文件数量: {project_info.get('file_count', 0)}
"""

        # 🔥 根据是否限定范围显示不同的结构信息
        if scope_limited:
            msg += f"""
## ⚠️ 审计范围限定
**{scope_message}**

### 目标文件列表
"""
            for f in structure.get('files', []):
                msg += f"- {f}\n"

            if structure.get('directories'):
                msg += f"""
### 相关目录
{structure.get('directories', [])}
"""
        else:
            msg += f"""
## 目录结构
{json.dumps(structure, ensure_ascii=False, indent=2)}
"""

        # 🔥 如果配置了 target_files，也明确显示
        target_files = config.get('target_files', [])
        if target_files:
            msg += f"""
## ⚠️ 重要提示
用户指定了 **{len(target_files)}** 个目标文件进行审计。
请确保你的分析集中在这些指定的文件上，不要浪费时间分析其他文件。
"""

        msg += f"""
## 用户配置
- 目标漏洞: {config.get('target_vulnerabilities', ['all'])}
- 验证级别: {config.get('verification_level', 'sandbox')}
- 排除模式: {config.get('exclude_patterns', [])}

## 可用子 Agent
{', '.join(self.sub_agents.keys()) if self.sub_agents else '(暂无子 Agent)'}

## ⚠️ 重复检测指令
**禁止重复之前的发现**：在报告任何漏洞前，必须检查 Observation 中是否已包含相同漏洞。同文件+同行号+同类型的漏洞不得重复报告。
- 搜索关键词时，避免重复已执行过的搜索模式
- 每次 dispatch_agent 时，提供与之前不同的任务描述和目标

请开始你的审计工作。首先思考应该如何开展，然后决定第一步做什么。"""

        return msg

    def _parse_llm_response(self, response: str) -> AgentStep | None:
        """解析 LLM 响应"""
        # 🔥 v2.1: 预处理 - 移除 Markdown 格式标记（LLM 有时会输出 **Action:** 而非 Action:）
        cleaned_response = response
        cleaned_response = re.sub(r'\*\*Action:\*\*', 'Action:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Action Input:\*\*', 'Action Input:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Thought:\*\*', 'Thought:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Observation:\*\*', 'Observation:', cleaned_response)

        # 提取 Thought
        thought_match = re.search(r'Thought:\s*(.*?)(?=Action:|$)', cleaned_response, re.DOTALL)
        thought = thought_match.group(1).strip() if thought_match else ""

        # 提取 Action
        action_match = re.search(r'Action:\s*(\w+)', cleaned_response)
        if not action_match:
            return None
        action = action_match.group(1).strip()

        # 提取 Action Input
        input_match = re.search(r'Action Input:\s*(.*?)(?=Thought:|Observation:|$)', cleaned_response, re.DOTALL)
        if not input_match:
            return None

        input_text = input_match.group(1).strip()
        # 移除 markdown 代码块
        input_text = re.sub(r'```json\s*', '', input_text)
        input_text = re.sub(r'```\s*', '', input_text)

        # 使用增强的 JSON 解析器
        action_input = AgentJsonParser.parse(
            input_text,
            default={"raw": input_text}
        )

        return AgentStep(
            thought=thought,
            action=action,
            action_input=action_input,
        )

    async def _dispatch_agent(self, params: dict[str, Any]) -> str:
        """调度子 Agent（支持单个和批量并行）"""

        # 支持批量并行调度
        if "agents" in params:
            return await self._dispatch_agents_parallel(params["agents"])

        agent_name = params.get("agent", "")
        task = params.get("task", "")
        context = params.get("context", "")

        logger.debug(f"[Orchestrator] _dispatch_agent 被调用: agent_name='{agent_name}', task='{task[:50]}...'")

        # 🔥 尝试大小写不敏感匹配
        agent = self.sub_agents.get(agent_name)
        if not agent:
            # 尝试小写匹配
            agent_name_lower = agent_name.lower()
            agent = self.sub_agents.get(agent_name_lower)
            if agent:
                agent_name = agent_name_lower
                logger.debug(f"[Orchestrator] 使用小写匹配: {agent_name}")

        if not agent:
            available = list(self.sub_agents.keys())
            logger.warning(f"[Orchestrator] Agent '{agent_name}' 不存在，可用: {available}")
            return f"错误: Agent '{agent_name}' 不存在。可用的 Agent: {available}"


        # 🔥 检查是否重复调度同一个 Agent
        dispatch_count = self._dispatched_tasks.get(agent_name, 0)
        # 动态调度上限：覆盖率门禁拦截 ≥3 次时提升上限到 4，给 LLM 补漏机会
        max_dispatch = 3
        if dispatch_count >= max_dispatch:
            # ✅ FIX: 当重复调度时，自动提升 _hard_coverage_block_count 到 5，放行 finish
            if agent_name == "analysis" and self._hard_coverage_block_count < 3:
                self._hard_coverage_block_count = 3
                logger.info(f"[Orchestrator] Analysis dispatched {dispatch_count} times, auto-bypassing coverage gate")
            return f"""## ⚠️ 重复调度警告

你已经调度 {agent_name} Agent {dispatch_count} 次了。

如果之前的调度没有返回有用的结果，请考虑：
1. 直接使用 finish 操作结束审计并汇总已有发现（覆盖率门禁已自动放行）
2. 提供更具体的任务描述

当前已收集的发现数量: {len(self._all_findings)}
注意：覆盖率门禁已自动放行，你可以直接 finish。
"""

        self._dispatched_tasks[agent_name] = dispatch_count + 1

        # 🔥 标准化阶段事件，让前端能看到清晰的流程推进
        phase_map = {
            "recon": "reconnaissance",
            "analysis": "analysis",
            "verification": "verification",
        }
        current_phase = phase_map.get(agent_name, agent_name)
        await self.emit_event(
            "phase_start",
            f"▶️ 开始 {agent_name} 阶段",
            phase=current_phase,
            agent=agent_name,
        )

        # 🔥 设置父 Agent ID 并注册到注册表（动态 Agent 树）
        logger.debug(f"[Orchestrator] 准备调度 {agent_name} Agent, agent._registered={agent._registered}")
        agent.set_parent_id(self._agent_id)
        logger.debug(f"[Orchestrator] 设置 parent_id 完成，准备注册 {agent_name}")
        agent._register_to_registry(task=task)
        logger.debug(f"[Orchestrator] {agent_name} 注册完成，agent._registered={agent._registered}")

        await self.emit_event(
            "dispatch",
            f"📤 调度 {agent_name} Agent: {task[:100]}...",
            agent=agent_name,
            task=task,
        )

        self._tool_calls += 1

        try:
            # 🔥 构建子 Agent 输入 - 传递完整的运行时上下文
            project_info = self._runtime_context.get("project_info", {}).copy()
            # 确保 project_info 包含 root 路径
            if "root" not in project_info:
                project_info["root"] = self._runtime_context.get("project_root", ".")

            # 🔥 FIX: 构建完整的 previous_results，包含所有已执行 Agent 的结果
            previous_results = {
                "findings": self._all_findings,  # 传递已收集的发现
                # ✅ P1-4: 传递 Semgrep 精确定位信息给 Analysis Agent
                "semgrep_findings": self._semgrep_findings,
            }

            # 🔥 将之前 Agent 的完整结果传递给后续 Agent
            for prev_agent, prev_data in self._agent_results.items():
                previous_results[prev_agent] = {"data": prev_data}

            # ✅ P1-2: 构建 CrossRoundContext 并注入子 Agent
            # 当已有 findings 或 coverage 数据时，构建跨轮传递结构
            if self._all_findings or agent_name == "analysis":
                try:
                    cross_round = CrossRoundContext()
                    # 从 findings 构建已覆盖维度
                    coverage_report = self._evaluate_current_coverage()
                    # 🔥 B3b 修复 (code-review 发现): status_info 是 CoverageStatus 枚举
                    # （str, Enum），不是 dict。原 isinstance(status_info, dict) 恒 False，
                    # 导致 cross_round.covered 永不填充，R2 收不到已覆盖维度信息。
                    for dim, status_info in coverage_report.statuses.items():
                        if status_info == CoverageStatus.COVERED:
                            cross_round.covered[dim] = "✅ 已覆盖"
                        elif status_info == CoverageStatus.SHALLOW:
                            cross_round.covered[dim] = "⚠️ 浅覆盖"
                    # 🔥 B3 修复: CoverageReport.gaps 是 @property，不可用 () 调用
                    # （误用会触发 'list' object is not callable，导致 CrossRoundContext 构建失败）
                    for gap in coverage_report.gaps:
                        cross_round.gaps.append(gap)
                    # 收集已读文件和已执行搜索（从子 Agent 结果中提取）
                    for prev_agent, prev_data in self._agent_results.items():
                        agent_data = prev_data if isinstance(prev_data, dict) else {}
                        if isinstance(agent_data, dict):
                            for f in agent_data.get("files_read", []):
                                if f not in cross_round.files_read:
                                    cross_round.files_read.append(f)
                            for g in agent_data.get("grep_patterns", []):
                                if g not in cross_round.grep_done:
                                    cross_round.grep_done.append(g)
                    cross_round_text = cross_round.to_prompt()
                    if cross_round_text:
                        previous_results["cross_round_context"] = cross_round_text
                        logger.info(f"[Orchestrator] CrossRoundContext built for {agent_name}: {len(cross_round.covered)} covered, {len(cross_round.gaps)} gaps")
                except Exception as e:
                    logger.warning(f"[Orchestrator] CrossRoundContext build failed (non-fatal): {e}")

            # 🔥 构建 TaskHandoff - Agent 间的结构化通信协议
            handoff = self._build_handoff_for_agent(agent_name, task, context)

            sub_input = {
                "task": task,
                "task_context": context,
                "project_info": project_info,
                "config": self._runtime_context.get("config", {}),
                "project_root": self._runtime_context.get("project_root", "."),
                "previous_results": previous_results,
                "handoff": handoff.to_dict() if handoff else None,  # 🔥 传递 TaskHandoff
            }

            # 🔥 执行子 Agent 前检查取消状态
            if self.is_cancelled:
                return f"## {agent_name} Agent 执行取消\n\n任务已被用户取消"

            # 🔥 执行子 Agent - 支持取消和超时
            # 使用用户配置的子Agent超时时间
            default_sub_agent_timeout = self._timeout_config.get('sub_agent_timeout', 600)
            # 设置子 Agent 超时（根据 Agent 类型，recon稍短）
            agent_timeouts = {
                "recon": min(300, default_sub_agent_timeout),  # recon 通常较快
                "analysis": default_sub_agent_timeout,
                "verification": default_sub_agent_timeout,
            }
            timeout = agent_timeouts.get(agent_name, default_sub_agent_timeout)

            async def run_with_cancel_check() -> AgentResult:
                """包装子 Agent 执行，定期检查取消状态"""
                run_task = asyncio.create_task(agent.run(sub_input))
                try:
                    while not run_task.done():
                        if self.is_cancelled:
                            # 🔥 传播取消到子 Agent
                            logger.info(f"[{self.name}] Cancelling sub-agent {agent_name} due to parent cancel")
                            if hasattr(agent, 'cancel'):
                                agent.cancel()
                            run_task.cancel()
                            try:
                                await run_task
                            except asyncio.CancelledError:
                                pass
                            raise asyncio.CancelledError("任务已取消")

                        # Use asyncio.wait to poll without cancelling the task
                        done, pending = await asyncio.wait(
                            [run_task],
                            timeout=0.5,
                            return_when=asyncio.FIRST_COMPLETED
                        )
                        if run_task in done:
                            return run_task.result()
                        # If not done, continue loop
                        continue

                    return await run_task
                except asyncio.CancelledError:
                    # 🔥 确保子任务被取消
                    if not run_task.done():
                        if hasattr(agent, 'cancel'):
                            agent.cancel()
                        run_task.cancel()
                        try:
                            await run_task
                        except asyncio.CancelledError:
                            pass
                    raise

            try:
                result = await asyncio.wait_for(
                    run_with_cancel_check(),
                    timeout=timeout
                )
            except TimeoutError:
                logger.warning(f"[{self.name}] Sub-agent {agent_name} timed out after {timeout}s")
                # R7: 中断收口——补发 dispatch_complete/phase_complete，保证事件流完整
                await self.emit_event(
                    "dispatch_complete",
                    f"⏹️ {agent_name} Agent 执行超时",
                    agent=agent_name,
                    interrupted=True,
                )
                await self.emit_event(
                    "phase_complete",
                    f"⏹️ {agent_name} 阶段超时终止",
                    phase=current_phase,
                    agent=agent_name,
                )
                return f"## {agent_name} Agent 执行超时\n\n子 Agent 执行超过 {timeout} 秒，已强制终止。请尝试更具体的任务或使用其他 Agent。"
            except asyncio.CancelledError:
                logger.info(f"[{self.name}] Sub-agent {agent_name} was cancelled")
                # R7: 中断收口
                await self.emit_event(
                    "dispatch_complete",
                    f"⏹️ {agent_name} Agent 被取消",
                    agent=agent_name,
                    interrupted=True,
                )
                await self.emit_event(
                    "phase_complete",
                    f"⏹️ {agent_name} 阶段取消",
                    phase=current_phase,
                    agent=agent_name,
                )
                return f"## {agent_name} Agent 执行取消\n\n任务已被用户取消"

            # 🔥 执行后再次检查取消状态
            if self.is_cancelled:
                # R7: 中断收口
                await self.emit_event(
                    "dispatch_complete",
                    f"⏹️ {agent_name} Agent 执行中断",
                    agent=agent_name,
                    interrupted=True,
                )
                await self.emit_event(
                    "phase_complete",
                    f"⏹️ {agent_name} 阶段中断",
                    phase=current_phase,
                    agent=agent_name,
                )
                return f"## {agent_name} Agent 执行中断\n\n任务已被用户取消"

            await self.emit_event(
                "phase_complete",
                f"⏹️ {agent_name} 阶段完成",
                phase=current_phase,
                agent=agent_name,
            )

            # 🔥 处理子 Agent 结果 - 不同 Agent 返回不同的数据结构
            # 🔥 DEBUG: 添加诊断日志
            logger.info(f"[Orchestrator] Processing {agent_name} result: success={result.success}, data_type={type(result.data).__name__}, data_keys={list(result.data.keys()) if isinstance(result.data, dict) else 'N/A'}")

            if result.success and result.data:
                data = result.data

                # 🔥 FIX: 保存 Agent 的完整结果，供后续 Agent 使用
                self._agent_results[agent_name] = data
                logger.info(f"[Orchestrator] Saved {agent_name} result with keys: {list(data.keys())}")

                # Accumulate sub-agent stats
                self._sub_agent_total_iterations += result.iterations or 0
                self._sub_agent_total_tool_calls += result.tool_calls or 0
                self._sub_agent_total_tokens += result.tokens_used or 0
                logger.info(f"[Orchestrator] {agent_name} stats: iter={result.iterations}, tools={result.tool_calls}, tokens={result.tokens_used}")

                # 🔥 保存 Agent 返回的 handoff，用于传递给后续 Agent
                if result.handoff:
                    if not hasattr(self, '_agent_handoffs'):
                        self._agent_handoffs = {}
                    self._agent_handoffs[agent_name] = result.handoff
                    logger.info(
                        f"[Orchestrator] Saved {agent_name} handoff: "
                        f"summary={result.handoff.summary[:50]}..."
                    )

                # 🔥 CRITICAL FIX: 收集发现 - 支持多种字段名
                # findings 字段通常来自 Analysis/Verification Agent
                # initial_findings 来自 Recon Agent
                # A2-fix: extract search keywords from agent results into _search_registry
                agent_search_patterns = data.get("search_patterns") or data.get("grep_patterns") or []
                if isinstance(agent_search_patterns, list):
                    for p in agent_search_patterns:
                        if isinstance(p, str) and p.strip():
                            self._search_registry["grep_patterns"].add(p.strip())
                    if agent_search_patterns:
                        logger.info(f"[Orchestrator] {agent_name} contributed {len(agent_search_patterns)} search patterns to coverage registry")

                # Also extract from handoff if available
                if result.handoff and hasattr(result.handoff, "search_patterns"):
                    for p in (result.handoff.search_patterns or []):
                        if isinstance(p, str) and p.strip():
                            self._search_registry["grep_patterns"].add(p.strip())

                raw_findings = data.get("findings", [])
                logger.info(f"[Orchestrator] {agent_name} returned data with {len(raw_findings)} findings in 'findings' field")

                # 🔥 ENHANCED: Also check for initial_findings (from Recon) - 改进逻辑
                # 即使 findings 为空列表，也检查 initial_findings
                if "initial_findings" in data:
                    initial = data.get("initial_findings", [])
                    logger.info(f"[Orchestrator] {agent_name} has {len(initial)} initial_findings, types: {[type(f).__name__ for f in initial[:3]]}")
                    for f in initial:
                        if isinstance(f, dict):
                            # 🔥 Normalize finding format - 处理 Recon 返回的格式
                            normalized = self._normalize_finding(f)
                            if normalized not in raw_findings:
                                raw_findings.append(normalized)
                                logger.info("[Orchestrator] Added dict finding from initial_findings")
                        elif isinstance(f, str) and f.strip():
                            # 🔥 FIX: Convert string finding to dict format instead of skipping
                            # Recon Agent 有时候会返回字符串格式的发现
                            # 尝试从字符串中提取文件路径（格式如 "app.py:36 - 描述"）
                            file_path = ""
                            line_start = 0
                            if ":" in f:
                                parts = f.split(":", 1)
                                potential_file = parts[0].strip()
                                # 检查是否像文件路径
                                if "." in potential_file and "/" not in potential_file[:3]:
                                    file_path = potential_file
                                    # 尝试提取行号
                                    if len(parts) > 1:
                                        remaining = parts[1].strip()
                                        line_match = remaining.split()[0] if remaining else ""
                                        if line_match.isdigit():
                                            line_start = int(line_match)

                            string_finding = {
                                "title": f[:100] if len(f) > 100 else f,
                                "description": f,
                                "file_path": file_path,
                                "line_start": line_start,
                                "severity": "medium",  # 默认中等严重度，Analysis 会重新评估
                                "vulnerability_type": "potential_issue",
                                "source": "recon",
                                "needs_verification": True,
                                "confidence": 0.5,  # 较低置信度，需要进一步分析
                            }
                            logger.info(f"[Orchestrator] Converted string finding to dict: {f[:80]}... (file={file_path}, line={line_start})")
                            raw_findings.append(string_finding)
                else:
                    logger.info(f"[Orchestrator] {agent_name} has no 'initial_findings' key in data")

                # 🔥 Also check high_risk_areas from Recon for potential findings
                if agent_name == "recon" and "high_risk_areas" in data:
                    high_risk = data.get("high_risk_areas", [])
                    logger.info(f"[Orchestrator] {agent_name} identified {len(high_risk)} high risk areas")
                    # 🔥 FIX: 将 high_risk_areas 也转换为发现
                    for area in high_risk:
                        if isinstance(area, str) and area.strip():
                            # 尝试从描述中提取文件路径和漏洞类型
                            file_path = ""
                            line_start = 0
                            vuln_type = "potential_issue"

                            # 🔥 FIX: 改进文件路径提取逻辑
                            # 格式1: "file.py:36 - 描述" -> 提取 file.py 和 36
                            # 格式2: "描述性文本" -> 不提取文件路径
                            if ":" in area:
                                parts = area.split(":", 1)
                                potential_file = parts[0].strip()
                                # 只有当 parts[0] 看起来像文件路径时才提取
                                # 文件路径通常包含 . 且没有空格（或只在结尾有扩展名）
                                if ("." in potential_file and
                                    " " not in potential_file and
                                    len(potential_file) < 100 and
                                    any(potential_file.endswith(ext) for ext in ['.py', '.js', '.ts', '.java', '.go', '.php', '.rb', '.c', '.cpp', '.h'])):
                                    file_path = potential_file
                                    # 尝试提取行号
                                    if len(parts) > 1:
                                        remaining = parts[1].strip()
                                        line_match = remaining.split()[0] if remaining else ""
                                        if line_match.isdigit():
                                            line_start = int(line_match)

                            # 推断漏洞类型
                            area_lower = area.lower()
                            if "command" in area_lower or "命令" in area_lower or "subprocess" in area_lower:
                                vuln_type = "command_injection"
                            elif "sql" in area_lower:
                                vuln_type = "sql_injection"
                            elif "xss" in area_lower:
                                vuln_type = "xss"
                            elif "path" in area_lower or "traversal" in area_lower or "路径" in area_lower:
                                vuln_type = "path_traversal"
                            elif "ssrf" in area_lower:
                                vuln_type = "ssrf"
                            elif "secret" in area_lower or "密钥" in area_lower or "key" in area_lower:
                                vuln_type = "hardcoded_secret"

                            high_risk_finding = {
                                "title": area[:100] if len(area) > 100 else area,
                                "description": area,
                                "file_path": file_path,
                                "line_start": line_start,
                                "severity": "high",  # 高风险区域默认高严重度
                                "vulnerability_type": vuln_type,
                                "source": "recon_high_risk",
                                "needs_verification": True,
                                "confidence": 0.6,
                            }
                            raw_findings.append(high_risk_finding)
                            logger.info(f"[Orchestrator] Converted high_risk_area to finding: {area[:60]}... (file={file_path}, type={vuln_type})")

                # 🔥 初始化 valid_findings，确保后续代码可以访问
                valid_findings = []

                if raw_findings:
                    # 只添加字典格式的发现
                    valid_findings = [f for f in raw_findings if isinstance(f, dict)]

                    logger.info(f"[Orchestrator] {agent_name} returned {len(valid_findings)} valid findings")

                    # 🔥 ENHANCED: Merge findings with better deduplication
                    for new_f in valid_findings:
                        # Normalize the finding first
                        normalized_new = self._normalize_finding(new_f)

                        # Skip if normalization rejected the finding (e.g., file not found)
                        if normalized_new is None:
                            continue

                        # Create fingerprint for deduplication (file + description similarity)
                        new_file = normalized_new.get("file_path", "").lower().strip()
                        new_desc = (normalized_new.get("description", "") or "").lower()[:100]
                        new_type = (normalized_new.get("vulnerability_type", "") or "").lower()
                        new_line = normalized_new.get("line_start") or normalized_new.get("line", 0)

                        # Check if exists (more flexible matching)
                        found = False
                        for i, existing_f in enumerate(self._all_findings):
                            existing_file = (existing_f.get("file_path", "") or existing_f.get("file", "")).lower().strip()
                            existing_desc = (existing_f.get("description", "") or "").lower()[:100]
                            existing_type = (existing_f.get("vulnerability_type", "") or existing_f.get("type", "")).lower()
                            existing_line = existing_f.get("line_start") or existing_f.get("line", 0)

                            # Match if same file AND (same line OR similar description OR same vulnerability type)
                            same_file = new_file and existing_file and (
                                new_file == existing_file or
                                new_file.endswith(existing_file) or
                                existing_file.endswith(new_file)
                            )
                            same_line = new_line and existing_line and new_line == existing_line
                            similar_desc = new_desc and existing_desc and (
                                new_desc in existing_desc or existing_desc in new_desc
                            )
                            same_type = new_type and existing_type and (
                                new_type == existing_type or
                                (new_type in existing_type) or (existing_type in new_type)
                            )
                            # 🔥 问题三修复：file_path 为空时用 title+type 去重
                            no_file_path = not new_file and not existing_file
                            title_match = (normalized_new.get('title', '').lower().strip() ==
                                           (existing_f.get('title', '') or '').lower().strip())

                            if (same_file and (same_line or similar_desc or same_type)) or (no_file_path and title_match and same_type):
                                # Update existing with new info (e.g. verification results)
                                # 🔥 FIX: Smart merge - don't overwrite good data with empty values
                                merged = dict(existing_f)  # Start with existing data
                                for key, value in normalized_new.items():
                                    # Bug B fix: is_verified uses explicit priority (Verification > Analysis)
                                    # Python False == 0 is True, so the generic guard skips False values.
                                    if key == "is_verified":
                                        if normalized_new.get("verification_status") or normalized_new.get("verdict"):
                                            merged[key] = value
                                        continue
                                    # Bug B fix: verification_status also uses explicit priority
                                    if key == "verification_status":
                                        if value is not None and value != "":
                                            merged[key] = value
                                        continue
                                    # Bug B fix: sandbox_attempts merge (list, not scalar)
                                    if key == "sandbox_attempts" and isinstance(value, list) and len(value) > 0:
                                        merged[key] = (merged.get(key) or []) + value
                                        continue
                                    # Default: skip None/empty/zero
                                    if value is not None and value != "" and value != 0:
                                        merged[key] = value
                                    elif key not in merged or merged[key] is None:
                                        # Fill in missing fields even with empty values
                                        merged[key] = value

                                # Keep the better title
                                if normalized_new.get("title") and len(normalized_new.get("title", "")) > len(existing_f.get("title", "")):
                                    merged["title"] = normalized_new["title"]
                                # Bug B fix: removed forced is_verified=True override; Verification priority handled in merge guard above
                                # 🔥 FIX: Preserve non-zero line numbers
                                if existing_f.get("line_start") and not normalized_new.get("line_start"):
                                    merged["line_start"] = existing_f["line_start"]
                                # 🔥 FIX: Preserve vulnerability_type
                                if existing_f.get("vulnerability_type") and not normalized_new.get("vulnerability_type"):
                                    merged["vulnerability_type"] = existing_f["vulnerability_type"]

                                self._all_findings[i] = merged
                                found = True
                                logger.info(f"[Orchestrator] Merged finding: {new_file}:{merged.get('line_start', 0)} ({merged.get('vulnerability_type', '')})")
                                break

                        if not found:
                            self._all_findings.append(normalized_new)
                            logger.info(f"[Orchestrator] Added new finding: {new_file}:{new_line} ({new_type})")

                    logger.info(f"[Orchestrator] Total findings now: {len(self._all_findings)}")
                else:
                    logger.info(f"[Orchestrator] {agent_name} returned no findings")

                await self.emit_event(
                    "dispatch_complete",
                    f"✅ {agent_name} Agent 完成",
                    agent=agent_name,
                    findings_count=len(self._all_findings),  # 🔥 Use total findings count
                )

                # 🔥 根据 Agent 类型构建不同的观察结果
                if agent_name == "recon":
                    # Recon Agent 返回项目信息
                    observation = f"""## Recon Agent 执行结果

**状态**: 成功
**迭代次数**: {result.iterations}
**耗时**: {result.duration_ms}ms

### 项目结构
{json.dumps(data.get('project_structure', {}), ensure_ascii=False, indent=2)}

### 技术栈
- 语言: {data.get('tech_stack', {}).get('languages', [])}
- 框架: {data.get('tech_stack', {}).get('frameworks', [])}
- 数据库: {data.get('tech_stack', {}).get('databases', [])}

### 入口点 ({len(data.get('entry_points', []))} 个)
"""
                    for i, ep in enumerate(data.get('entry_points', [])[:10]):
                        if isinstance(ep, dict):
                            observation += f"{i+1}. [{ep.get('type', 'unknown')}] {ep.get('file', '')}:{ep.get('line', '')}\n"

                    observation += f"""
### 高风险区域
{data.get('high_risk_areas', [])}

### 初步发现 ({len(data.get('initial_findings', []))} 个)
"""
                    for finding in data.get('initial_findings', [])[:5]:
                        if isinstance(finding, str):
                            observation += f"- {finding}\n"
                        elif isinstance(finding, dict):
                            observation += f"- {finding.get('title', finding)}\n"

                else:
                    # Analysis/Verification Agent 返回漏洞发现
                    observation = f"""## {agent_name} Agent 执行结果

**状态**: 成功
**发现数量**: {len(valid_findings)}
**迭代次数**: {result.iterations}
**耗时**: {result.duration_ms}ms

### 发现摘要
"""
                    for i, f in enumerate(valid_findings[:10]):
                        if not isinstance(f, dict):
                            continue
                        observation += f"""
{i+1}. [{f.get('severity', 'unknown')}] {f.get('title', 'Unknown')}
   - 类型: {f.get('vulnerability_type', 'unknown')}
   - 文件: {f.get('file_path', 'unknown')}
   - 描述: {f.get('description', '')[:200]}...
"""

                    if len(valid_findings) > 10:
                        observation += f"\n... 还有 {len(valid_findings) - 10} 个发现"

                if data.get("summary"):
                    observation += f"\n\n### Agent 总结\n{data['summary']}"

                return observation
            else:
                return f"## {agent_name} Agent 执行失败\n\n错误: {result.error}"

        except Exception as e:
            logger.error(f"Sub-agent dispatch failed: {e}", exc_info=True)
            return f"## 调度失败\n\n错误: {str(e)}"

    async def _dispatch_agents_parallel(self, agent_specs: list[dict[str, Any]]) -> str:
        """
        并行调度多个子 Agent。

        安全策略：
        - 同类型 Agent 不并行（避免同一 Agent 实例的状态冲突），退化为串行
        - 不同类型 Agent 才真正并行（如 1 个 analysis + 1 个 verification）
        - 最多 3 个并行
        """
        if not agent_specs:
            return "错误: 未指定任何 Agent"

        # 限制并行数量
        MAX_PARALLEL = 3
        if len(agent_specs) > MAX_PARALLEL:
            agent_specs = agent_specs[:MAX_PARALLEL]

        # 检查同类型 Agent 冲突：同类型不并行，退化为串行
        agent_names = [s.get("agent", "") for s in agent_specs]
        name_counts: dict[str, int] = {}
        for name in agent_names:
            name_counts[name] = name_counts.get(name, 0) + 1

        has_duplicate_type = any(c > 1 for c in name_counts.values())
        if has_duplicate_type:
            # 存在同类型 Agent，退化为串行以确保安全
            logger.info(
                f"[Orchestrator] Parallel dispatch degraded to sequential due to duplicate agent types: {name_counts}"
            )
            results = []
            for spec in agent_specs:
                result = await self._dispatch_agent(spec)
                results.append(result)
            return "\n\n---\n\n".join(results)

        # 不同类型 Agent，真正并行执行
        try:
            await self.emit_event(
                "info",
                f"🚀 并行调度 {len(agent_specs)} 个 Agent: {', '.join(agent_names)}",
            )
        except Exception:
            pass

        tasks = [self._dispatch_agent(spec) for spec in agent_specs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 汇总结果
        observations = []
        for i, result in enumerate(results):
            agent_name = agent_specs[i].get("agent", "unknown")
            if isinstance(result, Exception):
                logger.error(
                    f"[Orchestrator] Parallel agent {agent_name} failed: {result}",
                    exc_info=result,
                )
                observations.append(f"## {agent_name} Agent 执行异常\n\n错误: {result}")
            else:
                observations.append(result)

        return "\n\n---\n\n".join(observations)

    def _validate_file_path(self, file_path: str) -> bool:
        """
        🔥 v2.1: 验证文件路径是否真实存在

        Args:
            file_path: 相对或绝对文件路径（可能包含行号，如 "app.py:36"）

        Returns:
            bool: 文件是否存在
        """
        if not file_path or not file_path.strip():
            return False

        # 获取项目根目录
        project_root = self._runtime_context.get("project_root", "")
        if not project_root:
            # 没有项目根目录时，无法验证，返回 True 以避免误判
            return True

        # 清理路径（移除可能的行号）
        clean_path = file_path.split(":")[0].strip() if ":" in file_path else file_path.strip()

        # 尝试相对路径
        full_path = os.path.join(project_root, clean_path)
        if os.path.isfile(full_path):
            return True

        # 尝试绝对路径
        if os.path.isabs(clean_path) and os.path.isfile(clean_path):
            return True

        return False

    def _normalize_finding(self, finding: dict[str, Any]) -> dict[str, Any] | None:
        """
        标准化发现格式

        不同 Agent 可能返回不同格式的发现，这个方法将它们标准化为统一格式

        🔥 v2.1: 添加文件路径验证，返回 None 表示发现无效（幻觉）
        """
        normalized = dict(finding)  # 复制原始数据

        # 🔥 处理 location 字段 -> file_path + line_start
        if "location" in normalized and "file_path" not in normalized:
            location = normalized["location"]
            if isinstance(location, str) and ":" in location:
                parts = location.split(":")
                normalized["file_path"] = parts[0]
                try:
                    normalized["line_start"] = int(parts[1])
                except (ValueError, IndexError):
                    pass
            elif isinstance(location, str):
                normalized["file_path"] = location

        # 🔥 处理 file 字段 -> file_path
        if "file" in normalized and "file_path" not in normalized:
            normalized["file_path"] = normalized["file"]

        # 🔥 处理 line 字段 -> line_start
        if "line" in normalized and "line_start" not in normalized:
            normalized["line_start"] = normalized["line"]

        # 🔥 处理 type 字段 -> vulnerability_type
        if "type" in normalized and "vulnerability_type" not in normalized:
            # 不是所有 type 都是漏洞类型，比如 "Vulnerability" 只是标记
            type_val = normalized["type"]
            if type_val and type_val.lower() not in ["vulnerability", "finding", "issue"]:
                normalized["vulnerability_type"] = type_val
            elif "description" in normalized:
                # 尝试从描述中推断漏洞类型
                desc = normalized["description"].lower()
                if "command injection" in desc or "rce" in desc or "system(" in desc:
                    normalized["vulnerability_type"] = "command_injection"
                elif "sql injection" in desc or "sqli" in desc:
                    normalized["vulnerability_type"] = "sql_injection"
                elif "xss" in desc or "cross-site scripting" in desc:
                    normalized["vulnerability_type"] = "xss"
                elif "path traversal" in desc or "directory traversal" in desc:
                    normalized["vulnerability_type"] = "path_traversal"
                elif "ssrf" in desc:
                    normalized["vulnerability_type"] = "ssrf"
                elif "xxe" in desc:
                    normalized["vulnerability_type"] = "xxe"
                else:
                    normalized["vulnerability_type"] = "other"

        # 🔥 确保 severity 字段存在且为小写
        if "severity" in normalized:
            normalized["severity"] = str(normalized["severity"]).lower()
        else:
            normalized["severity"] = "medium"

        # 🔥 处理 risk 字段 -> severity
        if "risk" in normalized and "severity" not in normalized:
            normalized["severity"] = str(normalized["risk"]).lower()

        # 🔥 生成 title 如果不存在
        if "title" not in normalized:
            vuln_type = normalized.get("vulnerability_type", "Unknown")
            file_path = normalized.get("file_path", "")
            if file_path:
                import os
                normalized["title"] = f"{vuln_type.replace('_', ' ').title()} in {os.path.basename(file_path)}"
            else:
                normalized["title"] = f"{vuln_type.replace('_', ' ').title()} Vulnerability"

        # 🔥 处理 code 字段 -> code_snippet
        if "code" in normalized and "code_snippet" not in normalized:
            normalized["code_snippet"] = normalized["code"]

        # 🔥 处理 recommendation -> suggestion
        if "recommendation" in normalized and "suggestion" not in normalized:
            normalized["suggestion"] = normalized["recommendation"]

        # 🔥 处理 impact -> 添加到 description
        if "impact" in normalized and normalized.get("description"):
            if "impact" not in normalized["description"].lower():
                normalized["description"] += f"\n\nImpact: {normalized['impact']}"

        # 🔥 v2.1: 验证文件路径存在性
        file_path = normalized.get("file_path", "")
        if file_path and not self._validate_file_path(file_path):
            logger.warning(
                f"[Orchestrator] 🚫 过滤幻觉发现: 文件不存在 '{file_path}' "
                f"(title: {normalized.get('title', 'N/A')[:50]})"
            )
            return None  # 返回 None 表示发现无效

        # ✅ FIX: Confidence 阈值过滤 (阈值=0.7)
        confidence = normalized.get("confidence", 0)
        if isinstance(confidence, (int, float)) and confidence < 0.7:
            logger.info(
                f"[Orchestrator] 🚫 低置信度过滤: confidence={confidence} < 0.7 "
                f"(title: {normalized.get('title', 'N/A')[:50]})"
            )
            return None

        # ✅ FIX: Confidence 阈值过滤 (阈值=0.7)
        confidence = normalized.get("confidence", 0)
        if isinstance(confidence, (int, float)) and confidence < 0.7:
            logger.info(
                f"[Orchestrator] 🚫 低置信度过滤: confidence={confidence} < 0.7 "
                f"(title: {normalized.get('title', 'N/A')[:50]})"
            )
            return None

        return normalized

    def _summarize_findings(self) -> str:
        """汇总当前发现"""
        if not self._all_findings:
            return "目前还没有发现任何漏洞。"

        # 统计
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        type_counts: dict[str, int] = {}

        for f in self._all_findings:
            if not isinstance(f, dict):
                continue

            sev = f.get("severity", "low")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

            vtype = f.get("vulnerability_type", "other")
            type_counts[vtype] = type_counts.get(vtype, 0) + 1

        summary = f"""## 当前发现汇总

**总计**: {len(self._all_findings)} 个漏洞

### 严重程度分布
- Critical: {severity_counts['critical']}
- High: {severity_counts['high']}
- Medium: {severity_counts['medium']}
- Low: {severity_counts['low']}

### 漏洞类型分布
"""
        for vtype, count in type_counts.items():
            summary += f"- {vtype}: {count}\n"

        summary += "\n### 详细列表\n"
        for i, f in enumerate(self._all_findings):
            if isinstance(f, dict):
                summary += f"{i+1}. [{f.get('severity')}] {f.get('title')} ({f.get('file_path')})\n"

        return summary

    def _generate_default_summary(self) -> dict[str, Any]:
        """生成默认摘要"""
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

        for f in self._all_findings:
            if isinstance(f, dict):
                sev = f.get("severity", "low")
                severity_counts[sev] = severity_counts.get(sev, 0) + 1

        return {
            "total_findings": len(self._all_findings),
            "severity_distribution": severity_counts,
            "conclusion": "审计完成（未通过 LLM 生成结论）",
        }

    def get_conversation_history(self) -> list[dict[str, str]]:
        """获取对话历史"""
        return self._conversation_history

    def get_steps(self) -> list[AgentStep]:
        """获取执行步骤"""
        return self._steps

    def _build_handoff_for_agent(
        self,
        target_agent: str,
        task: str,
        context: str,
    ) -> TaskHandoff | None:
        """
        为目标 Agent 构建 TaskHandoff

        根据目标 Agent 类型，从之前的 Agent 结果中提取相关信息，
        构建结构化的任务交接协议。

        优先使用前序 Agent 返回的 handoff（如果存在），否则从 _agent_results 构建。

        Args:
            target_agent: 目标 Agent 名称 (recon/analysis/verification)
            task: 任务描述
            context: 任务上下文

        Returns:
            TaskHandoff 对象，如果没有前序信息则返回 None
        """
        # 🔥 如果是第一个 Agent (recon)，没有前序信息
        if target_agent == "recon" and not self._agent_results:
            return None

        # 🔥 优先使用前序 Agent 返回的 handoff
        # Analysis Agent 需要 Recon 的 handoff
        if target_agent == "analysis" and "recon" in self._agent_handoffs:
            recon_handoff = self._agent_handoffs["recon"]
            logger.info("[Orchestrator] Using Recon's handoff for Analysis Agent")
            # 更新目标 Agent
            return TaskHandoff(
                from_agent=recon_handoff.from_agent,
                to_agent=target_agent,
                summary=recon_handoff.summary,
                work_completed=recon_handoff.work_completed,
                key_findings=recon_handoff.key_findings,
                insights=recon_handoff.insights,
                suggested_actions=recon_handoff.suggested_actions,
                attention_points=recon_handoff.attention_points,
                priority_areas=recon_handoff.priority_areas,
                context_data=recon_handoff.context_data,
                confidence=recon_handoff.confidence,
            )

        # Verification Agent 需要 Analysis 的 handoff（也可能需要 Recon 的信息）
        if target_agent == "verification" and "analysis" in self._agent_handoffs:
            analysis_handoff = self._agent_handoffs["analysis"]
            logger.info("[Orchestrator] Using Analysis's handoff for Verification Agent")

            # 合并 Recon 的上下文信息（如果有）
            context_data = dict(analysis_handoff.context_data)
            if "recon" in self._agent_handoffs:
                recon_handoff = self._agent_handoffs["recon"]
                context_data["recon_tech_stack"] = recon_handoff.context_data.get("tech_stack", {})
                context_data["recon_entry_points"] = recon_handoff.context_data.get("entry_points", [])

            return TaskHandoff(
                from_agent=analysis_handoff.from_agent,
                to_agent=target_agent,
                summary=analysis_handoff.summary,
                work_completed=analysis_handoff.work_completed,
                key_findings=analysis_handoff.key_findings,
                insights=analysis_handoff.insights,
                suggested_actions=analysis_handoff.suggested_actions,
                attention_points=analysis_handoff.attention_points,
                priority_areas=analysis_handoff.priority_areas,
                context_data=context_data,
                confidence=analysis_handoff.confidence,
            )

        # 🔥 如果没有前序 Agent 的 handoff，从 _agent_results 构建（回退逻辑）
        logger.info(f"[Orchestrator] Building handoff from _agent_results for {target_agent}")

        # 🔥 收集工作摘要和关键发现
        work_completed = []
        key_findings = []
        insights = []
        suggested_actions = []
        attention_points = []
        priority_areas = []
        context_data = {}

        # 从 Recon 结果构建 handoff（给 Analysis）
        if target_agent == "analysis" and "recon" in self._agent_results:
            recon_data = self._agent_results["recon"]

            work_completed.append("完成项目信息收集和技术栈识别")

            # 提取技术栈信息
            tech_stack = recon_data.get("tech_stack", {})
            if tech_stack:
                work_completed.append(
                    f"识别技术栈: {', '.join(tech_stack.get('languages', []))} / "
                    f"{', '.join(tech_stack.get('frameworks', []))}"
                )
                context_data["tech_stack"] = tech_stack

            # 提取入口点
            entry_points = recon_data.get("entry_points", [])
            if entry_points:
                work_completed.append(f"发现 {len(entry_points)} 个入口点")
                context_data["entry_points"] = entry_points[:20]  # 限制数量
                for ep in entry_points[:10]:
                    if isinstance(ep, dict):
                        attention_points.append(
                            f"[{ep.get('type', 'unknown')}] {ep.get('file', '')}:{ep.get('line', '')}"
                        )

            # 提取高风险区域
            high_risk_areas = recon_data.get("high_risk_areas", [])
            if high_risk_areas:
                insights.append(f"发现 {len(high_risk_areas)} 个高风险区域需要重点分析")
                priority_areas.extend(high_risk_areas[:15])

            # 提取初步发现
            initial_findings = recon_data.get("initial_findings", [])
            if initial_findings:
                for f in initial_findings[:10]:
                    if isinstance(f, dict):
                        key_findings.append(f)
                        suggested_actions.append({
                            "action": "deep_analysis",
                            "target": f.get("file_path", ""),
                            "reason": f.get("title", "需要深入分析")
                        })

            # 推荐的工具
            recommended_tools = recon_data.get("recommended_tools", {})
            if recommended_tools:
                context_data["recommended_tools"] = recommended_tools

        # 从 Analysis 结果构建 handoff（给 Verification）
        elif target_agent == "verification":
            # 先添加 Recon 的信息（如果有）
            if "recon" in self._agent_results:
                recon_data = self._agent_results["recon"]
                context_data["tech_stack"] = recon_data.get("tech_stack", {})
                context_data["entry_points"] = recon_data.get("entry_points", [])[:10]

            # 添加 Analysis 的信息
            if "analysis" in self._agent_results:
                analysis_data = self._agent_results["analysis"]

                work_completed.append("完成代码深度分析")

                findings = analysis_data.get("findings", [])
                if findings:
                    work_completed.append(f"发现 {len(findings)} 个潜在漏洞")

                    # 按严重程度排序，优先验证高危漏洞
                    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
                    sorted_findings = sorted(
                        findings,
                        key=lambda x: severity_order.get(x.get("severity", "low"), 3)
                    )

                    for f in sorted_findings[:15]:
                        if isinstance(f, dict):
                            key_findings.append(f)
                            suggested_actions.append({
                                "action": "verify",
                                "target": f.get("file_path", ""),
                                "vulnerability_type": f.get("vulnerability_type", "unknown"),
                                "priority": "high" if f.get("severity") in ["critical", "high"] else "normal"
                            })

                    # 统计严重程度分布
                    severity_counts: dict[str, int] = {}
                    for f in findings:
                        sev = f.get("severity", "unknown")
                        severity_counts[sev] = severity_counts.get(sev, 0) + 1

                    insights.append(
                        f"漏洞分布: Critical={severity_counts.get('critical', 0)}, "
                        f"High={severity_counts.get('high', 0)}, "
                        f"Medium={severity_counts.get('medium', 0)}, "
                        f"Low={severity_counts.get('low', 0)}"
                    )

            # 也包含已有的发现（可能来自多个 Agent）
            if self._all_findings:
                context_data["all_findings"] = self._all_findings[:20]

        # 如果没有任何工作记录，说明没有前序信息
        if not work_completed and not key_findings:
            return None

        # 构建 TaskHandoff
        summary = f"任务: {task[:100]}"
        if work_completed:
            summary = f"前序工作已完成: {', '.join(work_completed[:3])}"

        return TaskHandoff(
            from_agent="Orchestrator",
            to_agent=target_agent,
            summary=summary,
            work_completed=work_completed,
            key_findings=key_findings,
            insights=insights,
            suggested_actions=suggested_actions,
            attention_points=attention_points,
            priority_areas=priority_areas,
            context_data=context_data,
            confidence=0.85,
        )
