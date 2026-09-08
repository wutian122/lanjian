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
import time
from dataclasses import dataclass
from typing import Any

from ..core.coverage import CoverageMatrix
from ..core.attack_chain import AttackChainAnalyzer
from ..core.cross_round import CrossRoundContext
from ..coverage import CoverageStatus, evaluate_coverage
from ..json_parser import AgentJsonParser
from ..prompts import CORE_SECURITY_PRINCIPLES, MULTI_AGENT_RULES, build_enhanced_prompt
from ..round_strategy import RoundContext
from ..strict_finding import (
    MIN_CANDIDATE_CONFIDENCE,
    MIN_CONFIDENCE_THRESHOLD,
    is_context_only_finding,
    is_verification_work_item,
)
from .base import AgentConfig, AgentPattern, AgentResult, AgentType, BaseAgent, TaskHandoff
from app.services.agent.config import get_agent_config

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


# F1（Task 11 follow-up）：Semgrep 兜底只保留"有确定性 PoC 专用模板"的漏洞
# 类型——与 verification._gen_sandbox_command 的 cmd_templates 专用模板集合
# 保持一致。配置类（.github/Dockerfile 等，vulnerability_type=other）与
# weak_crypto/xxe 等走 default 通用模板的类型，确定性 PoC 只能输出
# NO_SINK/STATIC_CONFIRMED，无验证价值却占用沙箱验证预算（Task 19 生产实证：
# 40 条 semgrep_fallback 候选全为配置类，拖垮 Verification LLM 循环，时间
# 预算烧穿导致 verification 未跑、attempt 无法落库验收）。
VERIFIABLE_SEMGREP_TYPES: frozenset[str] = frozenset({
    "sql_injection",
    "command_injection",
    "xss",
    "path_traversal",
    "ssrf",
    "auth_missing",
    "tenant_isolation",
    "idor",
    "hardcoded_secret",
    "deserialization",
})

# _map_semgrep_to_vuln_type 第 1 分支（"injection" in cid）截胡了第 156 行
# 的 command_injection 分支：SQL 类与命令注入类 check_id 都含 "injection"，
# 一律被泛化为 "injection"——该名称在 verification 模板表中无专用键，且
# 一刀切削为 sql_injection 会让命令注入候选误走 SQL 模板（sink 硬编码
# execute/raw/query，subprocess/os.system 0 命中 → NO_SINK 假阴）。兜底
# 落库前按 rule_id 二次分流：命令注入关键词（command/exec/subprocess）
# 且不含 sql → command_injection；否则 → sql_injection。
def _canonicalize_semgrep_fallback_type(raw_type: Any, rule_id: str) -> str:
    """规范化兜底候选 vulnerability_type；仅对泛化 "injection" 二次分流。

    非 "injection" 类型原样返回（不干预 _map 的其他映射结果）。
    """
    vtype = str(raw_type or "").strip().lower()
    if vtype != "injection":
        return raw_type
    cid = str(rule_id or "").lower()
    if (
        "command" in cid or "exec" in cid or "subprocess" in cid
    ) and "sql" not in cid:
        return "command_injection"
    return "sql_injection"


# A1（Task 19 follow-up）：EL 表达式注入 / SSTI / 模板注入类"非标准注入类"
# 识别。生产实证（tomcat 审计）：ELProcessor.java 的 Semgrep 规则 check_id 含
# "injection"（如 javax.el-expression-injection），_map 泛化为 "injection" 后
# 经 _canonicalize 误分流为 sql_injection → 走 SQL 专用模板（sink 硬编码
# execute/raw/query/sql），EL/模板代码零命中 → NO_SINK → not_reproducible，
# 沙箱白跑烧验证预算。这类漏洞在 verification._gen_sandbox_command 的
# cmd_templates 中无专用 PoC 模板，确定性验证不成立——兜底构建层直接归
# "unverifiable" 排除出沙箱（记独立 observation，不与 F1 配置类 filtered 混档）。
# 匹配一律对小写字符串做词边界（\b）匹配：短词 "el" 不得误伤 model/level/
# panel/cancel 等含 "el" 子串的词（\b 要求两侧为非字母数字边界）。
_UNVERIFIABLE_EXPR_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # 引擎/方言专名优先（最具体信号），通用族名（ssti/template_injection/el/
    # expression）依次兜底——返回首个命中类别，保证 observation 分布可定位。
    ("ognl", re.compile(r"\bognl\b")),
    ("spel", re.compile(r"\bspel\b")),
    ("mvel", re.compile(r"\bmvel\b")),
    ("freemarker", re.compile(r"\bfreemarker\b")),
    ("thymeleaf", re.compile(r"\bthymeleaf\b")),
    ("velocity", re.compile(r"\bvelocity\b")),
    ("ssti", re.compile(r"\bssti\b")),
    ("template_injection", re.compile(r"template[\s._-]injection\b")),
    ("el", re.compile(r"\bel\b")),
    ("expression", re.compile(r"\bexpression\b")),
)


def _classify_unverifiable_semgrep_fallback(raw_type: Any, rule_id: str) -> str | None:
    """识别 EL 表达式/SSTI/模板注入等无确定性 PoC 模板的 Semgrep 命中。

    命中返回特征类别名（el/expression/ssti/template_injection/ognl/spel/mvel/
    freemarker/thymeleaf/velocity），否则 None。判定信号为 rule_id（check_id）
    与预扫映射的 raw vuln_type 拼接小写串；词边界匹配防短词误伤。
    """
    haystack = f"{rule_id or ''} {raw_type or ''}".lower()
    for kind, pattern in _UNVERIFIABLE_EXPR_PATTERNS:
        if pattern.search(haystack):
            return kind
    return None


# 兜底候选严重度下限：INFO/LOW 不送沙箱（低严重度命中无确定性验证价值）。
_SEMGREP_FALLBACK_SEVERITY_ORDER: dict[str, int] = {
    "info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4,
}
_SEMGREP_FALLBACK_MIN_SEVERITY_RANK = 2  # medium


def _is_verifiable_semgrep_candidate(candidate: dict[str, Any]) -> bool:
    """F1: 兜底候选是否值得送沙箱——类型有确定性 PoC 专用模板且 severity ≥ medium。

    入参为 _build_semgrep_fallback_candidates 产物（vulnerability_type 已做
    别名规范化）；未知 severity 按 medium 处理（预扫仅产出 high/medium/low）。
    """
    vuln_type = str(candidate.get("vulnerability_type") or "").strip().lower()
    if vuln_type not in VERIFIABLE_SEMGREP_TYPES:
        return False
    severity = str(candidate.get("severity") or "medium").strip().lower()
    rank = _SEMGREP_FALLBACK_SEVERITY_ORDER.get(severity, _SEMGREP_FALLBACK_MIN_SEVERITY_RANK)
    return rank >= _SEMGREP_FALLBACK_MIN_SEVERITY_RANK


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

        # 🔥 fix-audit-time-budget-2026-08: 任务时间预算治理
        self._deadline: float | None = None
        self._deadline_hit: bool = False
        self._dispatch_failures: int = 0

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
        # Task 11 (finding-output-floor): Analysis 强制总结产出下限违规（粘滞，
        # 任一轮 0 候选 0 豁免即置位）；Semgrep 兜底落库一次性标志
        self._output_floor_violated: bool = False
        self._semgrep_fallback_applied: bool = False
        self._full_verification_dispatched: bool = False
        # T6 (REQ-VC-2): R4 放行前程序化补验的一次性标志（防重复调度）

        # 🔥 v3.0: 审计追踪文件系统
        self.trace_manager = None
        if get_agent_config().audit_trace_enabled and task_id:
            from app.services.agent.audit_trace import AuditTraceManager
            # Task 13：base_dir 不传，由 AuditTraceManager 默认读 settings.AUDIT_TRACE_DIR
            # （env AUDIT_TRACE_DIR 覆盖；compose bind mount /app/audit_traces 持久化）。
            self.trace_manager = AuditTraceManager(
                task_id=task_id,
                project_name="unknown",  # 将在 run() 中更新
            )
            logger.info(f"[{self.name}] 审计追踪已启用")
            # sandbox-verification-hard-gate Task 14：trace_manager 注入子 Agent——
            # 工具/LLM/验证三类写点（execute_tool/stream_llm_call/验证收尾）共享
            # 同一任务追踪文件；子 Agent 先于 Orchestrator 构造，故在此回填。
            for _sub in self.sub_agents.values():
                _sub.trace_manager = self.trace_manager

        # 🔥 v3.0: 智能上下文管理器
        self.context_manager = None
        if get_agent_config().context_compression_enabled:
            from app.services.agent.context_manager import ContextManager, ContextWindow
            self.context_manager = ContextManager(
                llm_service=llm_service,
                trace_manager=self.trace_manager,
                window_config=ContextWindow(
                    max_messages=get_agent_config().context_max_messages,
                    compression_threshold=get_agent_config().context_compression_threshold,
                    keep_recent=get_agent_config().context_keep_recent,
                )
            )
            logger.info(f"[{self.name}] 智能上下文管理已启用")
        self._force_verification_dispatched: bool = False
        # R4: 连续被"无沙箱证据"门禁拒绝 finish 的次数；达上限后停止强制重派
        self._finish_gate_rejections: int = 0
        # R6: 门禁拒绝/兜底原因，收尾时写入 agent_tasks.observations
        self._gate_observations: list[dict[str, Any]] = []
        # sandbox-verification-hard-gate Task 8: R4 达限放行时记录放行原因；
        # _finish_accepted 标记主循环是否经 LLM finish 正常 break（否则为轮次耗尽退出）
        self._gate_release_reason: str | None = None
        self._finish_accepted: bool = False

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
            "output_floor_violated": bool(getattr(self, "_output_floor_violated", False)),
            "semgrep_fallback_applied": bool(getattr(self, "_semgrep_fallback_applied", False)),
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
        self._output_floor_violated = bool(state.get("output_floor_violated") or False)
        self._semgrep_fallback_applied = bool(state.get("semgrep_fallback_applied") or False)

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

        Task 12: 遍历口径与门禁统一用 _actionable_findings（recon 上下文线索
        不参与证据判定——它们从不进验证队列，不可能携带验证状态）。
        """
        for finding in self._actionable_findings():
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
                # T8 (REQ-VP-2): static_confirmed 必须有 sandbox_attempts 证据才计入沙箱验证
                sandbox_attempts = finding.get("sandbox_attempts")
                if isinstance(sandbox_attempts, list) and len(sandbox_attempts) > 0:
                    return True
                continue
        return False

    def _record_gate_observation(self, gate: str, reason: str) -> None:
        """R6: 记录门禁拒绝/兜底原因，收尾时写入 agent_tasks.observations。"""
        from datetime import datetime, timezone
        self._gate_observations.append({
            "gate": gate,
            "reason": reason,
            "time": datetime.now(timezone.utc).isoformat(),
        })

    async def _maybe_dispatch_force_verification(self) -> None:
        """T6 (REQ-VC-2): R4 放行前的程序化收口——补发一次 verification 调度。

        当验证门禁连续拒绝达上限即将放行收尾时，若仍存在未验证 finding，
        程序化补发一次 verification 调度（LLM 可能已放弃重派），兜底不丢未验证项。
        判定与全量验证门禁同款：非 confirmed/static_confirmed 等终态，或整体无有效
        沙箱证据，即视为未验证；一次性标志 _force_verification_dispatched 防重复。
        """
        if self._force_verification_dispatched:
            return
        unverified = [
            f for f in self._all_findings
            if is_verification_work_item(f)
            and (
                f.get("verification_status")
                not in ("confirmed", "static_confirmed", "not_reproducible", "false_positive")
                or not self._has_valid_sandbox_evidence()
            )
        ]
        if not unverified:
            return
        self._force_verification_dispatched = True
        await self._dispatch_agent({
            "agent": "verification",
            "task": "系统收口：验证剩余未验证漏洞",
            "context": f"{len(unverified)} 个未验证漏洞",
        })

    # Task 8: 视为"已沙箱验证收口"的终态集合（与 finish 门禁口径一致）
    _SANDBOX_TERMINAL_VERIFIED_STATUSES = (
        "confirmed", "static_confirmed", "not_reproducible", "false_positive",
    )

    def _mark_released_unverified_findings(self, reason: str) -> int:
        """sandbox-verification-hard-gate Task 8: 放行收尾时逐 finding 写
        sandbox_skip_reason（显式豁免标记）。

        仅标记仍满足"未沙箱验证"的 finding：非验证终态、is_verified 非 True、
        零 sandbox_attempts、且无既有 sandbox_skip_reason。已有尝试（含全部
        infra_error）属"沙箱执行过"，不属跳过；elastic_exit/no_poc_template 等
        既有豁免不覆盖。标记不升级 verification_status/is_verified（与 Task 7
        elastic_exit 同语义：硬门禁算豁免，状态机不升级）。
        """
        marked = 0
        for finding in self._all_findings:
            if not isinstance(finding, dict):
                continue
            # Task 11: recon 上下文线索从来不是验证对象，不写"放行未验证"标记
            if is_context_only_finding(finding):
                continue
            if finding.get("sandbox_skip_reason"):
                continue
            if finding.get("is_verified") is True:
                continue
            if finding.get("verification_status") in self._SANDBOX_TERMINAL_VERIFIED_STATUSES:
                continue
            attempts = finding.get("sandbox_attempts")
            if isinstance(attempts, list) and len(attempts) > 0:
                continue
            finding["sandbox_skip_reason"] = reason
            marked += 1
        return marked

    def _apply_gate_release_marking(self) -> int:
        """Task 8: 收口前对放行路径上仍未沙箱验证的 finding 强制写豁免标记。

        两条放行路径收敛于此（补验结果合入后调用，补验已 confirmed 的不标记）：
        - R4 达限放行（finish 门禁连续拒绝达上限，spec d 条）：
          reason=gate_release_after_max_redispatch；
        - 主循环轮次耗尽退出（不经过任何 finish 门禁，T6 注释 ec0985ad
          生产回归：analysis 3 轮后轮次耗尽 5 finding 全零证据）：
          reason=orchestrator_max_iterations_exhausted。
        LLM 正常 finish 通过门禁链（无 R4 放行）不标记。标记数 > 0 时补一条
        gate_release observation（含放行原因与未验证数量）。
        """
        reason = getattr(self, "_gate_release_reason", None)
        if reason is None and not getattr(self, "_finish_accepted", False):
            reason = "orchestrator_max_iterations_exhausted"
        if not reason:
            return 0
        marked = self._mark_released_unverified_findings(reason)
        if marked:
            self._record_gate_observation(
                "gate_release",
                f"放行收口（{reason}）：{marked} 个 finding 未沙箱验证，"
                "已强制标记 sandbox_skip_reason 并纳入报告未沙箱验证清单",
            )
            logger.warning(
                f"[Orchestrator] Gate release ({reason}): {marked} findings marked "
                f"sandbox_skip_reason without sandbox verification"
            )
        return marked

    def _evaluate_current_coverage(self) -> Any:
        """基于当前 findings 与文本证据评估软覆盖率。"""
        text_evidence: list[str] = []
        text_evidence.extend(str(step.thought) for step in self._steps if step.thought)
        text_evidence.extend(str(step.observation) for step in self._steps if step.observation)
        text_evidence.extend(
            json.dumps(result, ensure_ascii=False)
            for result in self._agent_results.values()
        )
        # Task 11: recon 上下文线索不计覆盖维度（避免侦察线索冒充已覆盖产出）
        return evaluate_coverage(self._actionable_findings(), text_evidence)

    def _convert_recon_high_risk_area_to_finding(self, area: Any) -> dict[str, Any] | None:
        """Recon 高风险区是 Analysis 的上下文线索，不作为漏洞 findings。"""
        return None

    # sandbox-verification-hard-gate Task 11（Task 9 Important 交接）/ Task 12 口径统一：
    # recon 侦察线索（initial_findings 字符串 finding / high_risk_areas 转换项）
    # 虽经 Task 9 候选豁免流入 _all_findings，但承接本方法既有裁决——它们是
    # Analysis 的上下文线索，不是漏洞发现：不进 Verification 验证队列与 handoff
    # key_findings、不计门禁产出口径、不落库（agent_tasks._save_findings 用同一
    # 谓词过滤）——报告与前端不呈现为漏洞；仅保留在本次编排的内存状态中作上下文。
    # 口径谓词统一由 strict_finding 承载（三处消费者共用，禁止另写 source 元组副本）。

    def _actionable_findings(self) -> list[dict[str, Any]]:
        """可验证产出口径：_all_findings 排除 recon 上下文线索。

        Semgrep 兜底候选（source=semgrep_fallback）与 Analysis 产出
        （含 needs_verification=true 低置信候选）均计入。
        """
        return [
            f for f in (self._all_findings or [])
            if is_verification_work_item(f)
        ]

    def _build_semgrep_fallback_candidates(self) -> list[dict[str, Any]]:
        """Task 11: Semgrep 预扫发现映射为待验证候选。

        预扫存储格式见 _run_semgrep_prescan：title 存 check_id（规则 ID），
        description 存规则 message，severity/vulnerability_type 已映射。
        去重键 file_path + rule_id（spec：同文件同规则只落一条候选）。
        """
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for sf in self._semgrep_findings or []:
            if not isinstance(sf, dict):
                continue
            file_path = sf.get("file_path", "") or ""
            rule_id = str(sf.get("semgrep_rule_id") or sf.get("check_id") or sf.get("title", "") or "")
            key = (file_path, rule_id)
            if key in seen:
                continue
            seen.add(key)
            message = sf.get("description") or rule_id or "Semgrep 静态扫描发现"
            title = message if len(message) <= 120 else message[:120]
            raw_type = sf.get("vulnerability_type") or _map_semgrep_to_vuln_type(rule_id)
            # A1: EL/SSTI/模板注入类无确定性 PoC 模板且易被误分流为
            # sql_injection 白跑沙箱——在 canonicalize 之前拦截，归
            # unverifiable（unverifiable_kind 标记特征类别），不送沙箱。
            unverifiable_kind = _classify_unverifiable_semgrep_fallback(raw_type, rule_id)
            if unverifiable_kind is not None:
                vuln_type: Any = "unverifiable"
            else:
                vuln_type = _canonicalize_semgrep_fallback_type(raw_type, rule_id)
            candidate = {
                "title": title or rule_id,
                "description": f"[静态扫描兜底候选] {message}（Semgrep 规则: {rule_id}）",
                "file_path": file_path,
                "line_start": sf.get("line_start", 0),
                "line_end": sf.get("line_end", 0),
                "severity": sf.get("severity") or "medium",
                "vulnerability_type": vuln_type,
                "code_snippet": sf.get("code_snippet", ""),
                "confidence": 0.5,
                "needs_verification": True,
                "source": "semgrep_fallback",
                "semgrep_rule_id": rule_id,
                "is_verified": False,
            }
            if unverifiable_kind is not None:
                candidate["unverifiable_kind"] = unverifiable_kind
            candidates.append(candidate)
        return candidates

    async def _apply_semgrep_fallback(self) -> int:
        """Task 11: Analysis 全部派发后 0 可验证产出 → Semgrep 发现兜底落库。

        幂等（_semgrep_fallback_applied）；已有可验证产出（含已落库候选）时不
        触发。候选走既有 _normalize_finding + _merge_or_append_finding 管线
        （幻觉文件过滤、去重、合并），落库后由既有 finish/全量验证门禁强制送
        沙箱验证。返回新增候选数；任何异常 warning 后保持现状（非致命）。
        """
        if self._semgrep_fallback_applied:
            return 0
        self._semgrep_fallback_applied = True
        try:
            if self._actionable_findings():
                return 0
            candidates = self._build_semgrep_fallback_candidates()
            # F1: 落库前过滤——只保留有确定性 PoC 专用模板且 severity ≥ medium
            # 的候选；配置类/default 模板类型不送沙箱（Task 19 生产实证：配置类
            # 候选只能产出 NO_SINK，白烧验证预算拖垮 Verification 循环）。
            # A1: EL/SSTI/模板注入类先于 F1 分流——无确定性 PoC 模板且会误走
            # SQL 模板白跑，归 unverifiable 单独记 observation（不混入 F1
            # filtered 的配置类口径）。
            verifiable: list[dict[str, Any]] = []
            filtered: list[dict[str, Any]] = []
            unverifiable: list[dict[str, Any]] = []
            for candidate in candidates:
                if candidate.get("unverifiable_kind"):
                    unverifiable.append(candidate)
                elif _is_verifiable_semgrep_candidate(candidate):
                    verifiable.append(candidate)
                else:
                    filtered.append(candidate)
            if unverifiable:
                kind_counts: dict[str, int] = {}
                for c in unverifiable:
                    kind = str(c.get("unverifiable_kind") or "unknown")
                    kind_counts[kind] = kind_counts.get(kind, 0) + 1
                kind_breakdown = ", ".join(f"{k}×{n}" for k, n in sorted(kind_counts.items()))
                self._record_gate_observation(
                    "semgrep_fallback_unverifiable",
                    f"{len(unverifiable)} 条 Semgrep 兜底候选为 EL 表达式/模板注入（SSTI）"
                    f"类，无确定性 PoC 专用模板（特征分布: {kind_breakdown}），"
                    "不进验证队列、不送沙箱（误走 SQL/命令模板必出 NO_SINK 白跑）；"
                    "仅保留静态扫描结论，不进 semgrep_fallback_filtered 口径",
                )
                logger.info(
                    f"[Orchestrator] Semgrep fallback excluded {len(unverifiable)} "
                    f"unverifiable EL/SSTI candidates (kinds: {kind_breakdown})"
                )
            if filtered:
                type_counts: dict[str, int] = {}
                low_sev_counts: dict[str, int] = {}
                for c in filtered:
                    ctype = str(c.get("vulnerability_type") or "unknown")
                    type_counts[ctype] = type_counts.get(ctype, 0) + 1
                    sev = str(c.get("severity") or "medium").strip().lower()
                    if _SEMGREP_FALLBACK_SEVERITY_ORDER.get(
                        sev, _SEMGREP_FALLBACK_MIN_SEVERITY_RANK
                    ) < _SEMGREP_FALLBACK_MIN_SEVERITY_RANK:
                        low_sev_counts[sev] = low_sev_counts.get(sev, 0) + 1
                breakdown = ", ".join(f"{t}×{n}" for t, n in sorted(type_counts.items()))
                sev_detail = (
                    "；低严重度分布: "
                    + ", ".join(f"{s}×{n}" for s, n in sorted(low_sev_counts.items()))
                    if low_sev_counts
                    else ""
                )
                self._record_gate_observation(
                    "semgrep_fallback_filtered",
                    f"{len(filtered)} 条 Semgrep 兜底候选无确定性 PoC 验证条件"
                    f"（类型分布: {breakdown}{sev_detail}），"
                    "不进验证队列、不送沙箱、不入兜底候选报告段落",
                )
                logger.info(
                    f"[Orchestrator] Semgrep fallback filtered {len(filtered)} non-verifiable "
                    f"candidates (types: {breakdown}, low-severity: {sum(low_sev_counts.values())})"
                )
            added = 0
            for candidate in verifiable:
                normalized = self._normalize_finding(candidate)
                if normalized is None:
                    continue
                # 直接追加：兜底仅在 0 可验证产出时触发，候选已按 file_path+
                # rule_id 去重；走模糊合并反而会把不同规则的同位置命中吞并，
                # 或把 source 覆盖进 recon 上下文线索（使其脱离 context-only 口径）
                self._all_findings.append(normalized)
                added += 1
            if added:
                self._record_gate_observation(
                    "semgrep_fallback",
                    f"Analysis 全部派发后 0 产出，{added} 条 Semgrep 预扫发现作为兜底候选"
                    "落库（source=semgrep_fallback，confidence=0.5），交沙箱验证",
                )
                logger.warning(
                    f"[Orchestrator] Semgrep fallback: {added} static findings persisted "
                    "as verification candidates after zero Analysis output"
                )
            return added
        except Exception as e:
            logger.warning(f"[Orchestrator] Semgrep fallback failed (non-fatal): {e}")
            return 0

    def _ingest_analysis_floor_signal(self, data: Any) -> None:
        """Task 11: 读取 Analysis 强制总结的产出下限信号（Task 10 data 字段）。

        output_floor_violated=true 粘滞置位（任一轮强制总结 0 候选 0 豁免即
        留证）；收口判定在 max_dispatch/finish 处结合"最终是否仍 0 可验证产出"。
        """
        if isinstance(data, dict) and data.get("output_floor_violated"):
            if not self._output_floor_violated:
                logger.warning(
                    "[Orchestrator] Analysis output floor violated: forced summary "
                    "produced 0 candidates and 0 exemptions"
                )
            self._output_floor_violated = True

    def _apply_output_floor_closeout(self) -> bool:
        """Task 11: 产出下限违规且兜底后仍 0 可验证产出 → 覆盖不足语义收口。

        记 gate="output_floor" observation，置 coverage_bypassed（reason=
        output_floor_violated，completed_with_gaps）并放开覆盖率拦截让 LLM 能
        finish（否则 0 findings 下软/硬覆盖率门禁与 analysis max_dispatch 形成
        死循环）。有兜底候选可送沙箱验证时不收口，返回 False 由验证门禁接管。
        """
        if not self._output_floor_violated:
            return False
        if self._actionable_findings():
            return False
        if not any(o.get("gate") == "output_floor" for o in self._gate_observations):
            self._record_gate_observation(
                "output_floor",
                "Analysis 强制总结 0 候选 0 豁免（产出下限违规），Semgrep 兜底亦无发现，"
                "按覆盖不足收口（completed_with_gaps），报告呈现"
                "“分析未按要求产出候选”",
            )
        if not self._coverage_bypassed:
            self._coverage_bypassed = True
            self._coverage_bypass_info = self._build_coverage_bypass_info(
                reason="output_floor_violated",
                covered_count=0,
                total_dimensions=10,
                gaps=[],
                block_count=self._hard_coverage_block_count,
            )
        if self._hard_coverage_block_count < 3:
            self._hard_coverage_block_count = 3
        logger.warning(
            "[Orchestrator] Output floor violated with no verifiable output, "
            "closing with coverage gaps (completed_with_gaps)"
        )
        return True

    async def _finalize_output_floor_gate(self) -> None:
        """Task 11（修复轮 1 I2）：轮次耗尽收尾路径的兜底/收口统一入口。

        主循环 20 轮耗尽退出不经过 finish 门禁与 max_dispatch 分支，收尾段须
        幂等补跑 Semgrep 兜底落库与产出下限收口（两者各自带幂等守卫），杜绝
        "0 findings 收尾时兜底候选未落库、output_floor 违规未置 coverage_bypassed"
        的路径漏洞。
        """
        try:
            await self._apply_semgrep_fallback()
        except Exception as e:
            logger.warning(f"[Orchestrator] Semgrep fallback on finalize failed (non-fatal): {e}")
        try:
            self._apply_output_floor_closeout()
        except Exception as e:
            logger.warning(f"[Orchestrator] Output floor closeout on finalize failed (non-fatal): {e}")

    def register_sub_agent(self, name: str, agent: BaseAgent) -> None:
        """注册子 Agent"""
        self.sub_agents[name] = agent
        # Task 14：后注册的子 Agent 同步注入 trace_manager（与 __init__ 注入闭环）
        if getattr(self, "trace_manager", None):
            agent.trace_manager = self.trace_manager

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

    # 🔥 fix-audit-time-budget-2026-08: 任务时间预算治理
    def _resolve_task_timeout(self, input_data: dict[str, Any]) -> float:
        """任务时间预算（秒）：input_data.task_timeout_seconds > agent_timeout 配置 > 1800。"""
        raw = input_data.get("task_timeout_seconds")
        try:
            if raw is not None and float(raw) > 0:
                return float(raw)
        except (TypeError, ValueError):
            pass
        from app.core.config import settings

        return float(
            self._timeout_config.get("agent_timeout")
            or getattr(settings, "AGENT_TIMEOUT_SECONDS", 1800)
            or 1800
        )

    def _init_task_deadline(self, input_data: dict[str, Any]) -> None:
        """记录任务时间预算 deadline：从 run() 入口起算，与外部 wait_for 时钟一致。

        resume 重建 orchestrator 后自然重置，不存在跨次预算残留。
        """
        self._deadline = time.time() + self._resolve_task_timeout(input_data)

    def _remaining_seconds(self) -> float:
        if self._deadline is None:
            return float("inf")
        return self._deadline - time.time()

    def _resolve_dispatch_timeout(self, agent_name: str) -> int:
        """子 Agent 调度超时 = min(类型上限, 剩余任务预算)。

        类型上限沿用既有语义：recon=min(300, sub_agent_timeout)、
        analysis=sub_agent_timeout、verification=max(sub_agent_timeout, 1800)。
        """
        default_sub_agent_timeout = self._timeout_config.get("sub_agent_timeout", 600)
        agent_timeouts = {
            "recon": min(300, default_sub_agent_timeout),  # recon 通常较快
            "analysis": default_sub_agent_timeout,
            # REQ-ER-3: verification 验证 PoC（含启动服务/框架）耗时高，独立放宽超时
            # （生产 cade28a4：LLM 启动完整 Tomcat 验证 1200s 超时中断，丢已执行证据）
            "verification": max(default_sub_agent_timeout, 1800),
        }
        cap = agent_timeouts.get(agent_name, default_sub_agent_timeout)
        remaining = self._remaining_seconds()
        if remaining >= cap:
            return int(cap)  # 预算充足（含 deadline 未初始化的 inf）时等于类型上限
        return max(1, int(remaining))

    def _budget_refusal(self, agent_name: str) -> str | None:
        """剩余预算不足以支撑该类型子 Agent 的最小有效工作时长时拒发新调度。

        阈值类型化（analysis/verification=300s、recon=120s，settings 可覆盖，
        未知类型保守取 300s）：旧统一 30s 阈值对 100-600s 的子任务无意义，且会
        出现"派发成功后同一轮询周期立即软停止"的无效派发（nacos 任务实证空转
        137s）。拒发阈值(300s)高于软停止阈值(180s)，该矛盾自然消除。
        返回 None 表示可派发；返回收口文案时同时记 _gate_observations
        （gate=dispatch_budget，remaining/required/agent_name 编入 reason 文本）。
        """
        from app.core.config import settings

        min_effective = {
            "analysis": int(getattr(settings, "TIME_BUDGET_MIN_EFFECTIVE_ANALYSIS", 300)),
            "verification": int(getattr(settings, "TIME_BUDGET_MIN_EFFECTIVE_VERIFICATION", 300)),
            "recon": int(getattr(settings, "TIME_BUDGET_MIN_EFFECTIVE_RECON", 120)),
        }
        required = min_effective.get(agent_name, 300)  # 未知类型保守默认 300s
        remaining = self._remaining_seconds()
        if remaining <= required:
            message = (
                f"⏰ 任务时间预算将尽（剩余 {remaining:.0f}s），{agent_name} 子 Agent "
                f"最小有效工作时长需 {required}s，预算不足以完成有效工作，"
                f"不再发起新的子 Agent 调度，请立即总结收口"
            )
            self._record_gate_observation(
                "dispatch_budget",
                f"拒发 {agent_name}：剩余 {remaining:.0f}s <= 最小有效工作时长 {required}s，提前收口",
            )
            return message

        # B1 (F3): verification 预算预留——Analysis 已产出待验证项后，剩余预算低于
        # 预留量时拒发新 analysis：生产实证 verification 深入验证被主循环耗尽预算
        # 掐断，半途 findings 丢失、无 attempt 落库。仅约束 analysis；verification
        # 自身照常派发（预留窗口内完成验证，超时由 watchdog/弹性退出收口）。
        if agent_name == "analysis":
            reserve_seconds = int(getattr(settings, "VERIFICATION_RESERVE_SECONDS", 900))
            if remaining < reserve_seconds:
                actionable = self._actionable_findings()
                if actionable:
                    message = (
                        f"⏰ 任务时间预算将尽（剩余 {remaining:.0f}s），需为 verification "
                        f"验证阶段预留 {reserve_seconds}s 预算：不再发起新的 analysis 调度，"
                        f"请立即派发 verification 验证已产出的 {len(actionable)} 项发现，"
                        f"或 finish 交卷"
                    )
                    self._record_gate_observation(
                        "verification_reserve",
                        f"拒发 analysis：剩余 {remaining:.0f}s < 验证预留 {reserve_seconds}s，"
                        f"待验证产出 {len(actionable)} 条，预算预留验证阶段",
                    )
                    return message
        return None

    def _maybe_request_soft_stop(self, agent: BaseAgent, agent_name: str) -> bool:
        """剩余预算低于软停止阈值时，对 in-flight analysis 幂等请求软停止。"""
        if agent_name != "analysis" or agent.is_soft_stopped:
            return False
        from app.core.config import settings

        soft_stop_seconds = int(getattr(settings, "TIME_BUDGET_SOFT_STOP_SECONDS", 180))
        if self._remaining_seconds() < soft_stop_seconds:
            agent.request_soft_stop()
            return True
        return False

    def mark_deadline_hit(self) -> None:
        """标记任务时间预算到点并传播取消（由 agent_tasks 超时 watchdog 调用）。"""
        self._deadline_hit = True
        self.cancel()

    async def _hard_interrupt(self) -> None:
        """F2 补丁③：watchdog hard-cancel 兜底——强制关闭本任务所有 agent 的
        in-flight LLM 流迭代器。

        run_task.cancel 的 CancelledError 被某层吞掉/收口协程又发起新流时，
        本方法在资源层直接 aclose 各 agent 的 _stream_iter（线程桥 stop_event
        随之置位，工作线程在下个 chunk 边界退出），把卡在等下一个 chunk 的
        协程解除。best-effort：任何异常不外抛（watchdog 兜底不得被二次故障
        阻断）；__anext__ 在飞时 aclose 抛 RuntimeError 由底层 _safe_aclose
        吞掉（见 BaseAgent.hard_interrupt_stream 限制说明）。
        """
        from app.services.agent.core.registry import agent_registry

        try:
            await self.hard_interrupt_stream()
        except Exception:
            logger.warning(
                f"[{self.name}] _hard_interrupt self aclose failed", exc_info=True
            )

        task_id = (getattr(self, "_runtime_context", None) or {}).get("task_id")
        if not task_id:
            return
        try:
            for agent_id in agent_registry.get_task_agent_ids(task_id):
                inst = agent_registry.get_agent(agent_id)
                if inst is None or inst is self:
                    continue
                close = getattr(inst, "hard_interrupt_stream", None)
                if callable(close):
                    try:
                        await close()
                    except Exception:
                        logger.warning(
                            f"[{self.name}] _hard_interrupt aclose failed "
                            f"for agent {agent_id}",
                            exc_info=True,
                        )
        except Exception:
            logger.warning(
                f"[{self.name}] _hard_interrupt registry walk failed", exc_info=True
            )

    def _apply_deadline_bypass(self, reason: str) -> None:
        """按预算治理语义设置覆盖率安全阀 metadata（复用 5 字段唯一构造点）。"""
        if self._coverage_bypassed:
            return
        self._coverage_bypassed = True
        self._coverage_bypass_info = self._build_coverage_bypass_info(
            reason=reason,
            covered_count=0,
            total_dimensions=10,
            gaps=[],
            block_count=getattr(self, "_hard_coverage_block_count", 0),
        )

    def _finalize_budget_metadata(self) -> None:
        """收口统一预算 metadata（fix-audit-time-budget-2026-08）。

        优先级：task_timeout（watchdog 到点）> 已置 bypass（token/覆盖率门禁/
        task_deadline_exhausted 先写先赢）> dispatch_budget_exhausted（0 发现且
        存在调度失败）。
        """
        if self._deadline_hit:
            self._coverage_bypassed = True
            self._coverage_bypass_info = self._build_coverage_bypass_info(
                reason="task_timeout",
                covered_count=0,
                total_dimensions=10,
                gaps=[],
                block_count=getattr(self, "_hard_coverage_block_count", 0),
            )
            return
        if self._coverage_bypassed:
            return
        if not self._all_findings and self._dispatch_failures > 0:
            self._coverage_bypassed = True
            self._coverage_bypass_info = self._build_coverage_bypass_info(
                reason="dispatch_budget_exhausted",
                covered_count=0,
                total_dimensions=10,
                gaps=[],
                block_count=getattr(self, "_hard_coverage_block_count", 0),
            )

    def _merge_failed_result_findings(self, agent_name: str, result: AgentResult) -> int:
        """失败子 Agent（success=False）data 中已声明的发现保全（spec R6）。

        一次调度超时/取消后实例锁存会返回失败结果，若其 data.findings 非空
        （如取消前已产出），仍归一化合并入 _all_findings，不随失败丢弃。
        返回合并条数。
        """
        merged = 0
        if not isinstance(result.data, dict):
            return 0
        for finding in result.data.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            normalized = self._normalize_finding(finding)
            if normalized is not None:
                self._merge_or_append_finding(normalized)
                merged += 1
        if merged:
            logger.info(
                f"[Orchestrator] Preserved {merged} findings from failed {agent_name} result"
            )
        return merged

    def _salvage_dispatched_findings(
        self,
        agent_name: str,
        run_task: "asyncio.Task | None",
        result: AgentResult | None = None,
    ) -> None:
        """超时/取消早退前抢救子 Agent 已声明的 findings（生产 9344d5dd 断点 B）。

        调度超时/用户取消的早退分支直接返回文本，子 Agent 取消收口
        （verification._finalize_findings_without_final_answer：LLM 调用收到
        CancelledError -> break -> 证据绑定收口）返回的
        AgentResult(success=False, data.findings 已绑 sandbox_attempts) 走不到
        正常 merge 段，已执行的 mechanism 结果整体丢弃（生产两次超时 1800s/551s
        后 40 findings 证据全 null）。wait_for 超时/取消传播前，run_with_cancel_check
        的 except 块已 await run_task 收口完成，故此处可从 run_task.result() 取回。
        复用 _merge_failed_result_findings 最小 merge 管线（normalize +
        _merge_or_append_finding），至少保住 sandbox_attempts；任何异常非致命。
        """
        try:
            agent_result = result
            if (
                agent_result is None
                and run_task is not None
                and run_task.done()
                and not run_task.cancelled()
            ):
                agent_result = run_task.result()
            if not isinstance(agent_result, AgentResult):
                return
            data = getattr(agent_result, "data", None)
            if not isinstance(data, dict) or not data.get("findings"):
                return
            merged = self._merge_failed_result_findings(agent_name, agent_result)
            if merged:
                logger.warning(
                    f"[Orchestrator] {agent_name} 早退抢救：{merged} 条已声明 findings"
                    f"（含沙箱证据）已 merge，未随超时/取消丢弃"
                )
        except Exception as e:
            logger.warning(f"[Orchestrator] {agent_name} 早退抢救 merge 失败（非致命）: {e}")

    @staticmethod
    def _merge_attempts_lists_deduped(existing: list, incoming: list) -> list:
        """sandbox_attempts 合并按语义键去重（命令+退出码+证据摘要前缀），
        与 VerificationAgent._merge_attempts_deduped/_attempt_dedupe_key 同键语义。

        断点 A 修复后证据同时落在共享本体（findings_to_verify 元素）与
        verification 返回结果上，merge 段简单拼接会双计同源证据。
        """

        def _key(a: dict) -> tuple:
            return (
                str(a.get("command") or "")[:200],
                a.get("exit_code"),
                str(a.get("evidence_summary") or "")[:200],
            )

        by_key: dict[tuple, dict] = {}
        for a in list(existing) + list(incoming):
            if isinstance(a, dict) and _key(a) not in by_key:
                by_key[_key(a)] = a
        return list(by_key.values())

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

        # 🔥 fix-audit-time-budget-2026-08: 任务时间预算（与外部 wait_for 时钟同源）
        self._deadline_hit = False
        self._dispatch_failures = 0
        self._init_task_deadline(input_data)

        project_info = input_data.get("project_info", {})
        config = input_data.get("config", {})

        # 🔥 保存运行时上下文，用于传递给子 Agent
        self._runtime_context = {
            "project_info": project_info,
            "config": config,
            "project_root": input_data.get("project_root", project_info.get("root", ".")),
            "task_id": input_data.get("task_id"),
        }

        # 🔥 v3.0: 更新追踪管理器的项目名称
        if self.trace_manager:
            self.trace_manager.project_name = project_info.get("name", "unknown")

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
            self._output_floor_violated = False
            self._semgrep_fallback_applied = False
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

                # 🔥 fix-audit-time-budget-2026-08: 主循环时间预算硬阈值——到点优雅收口
                from app.core.config import settings

                hard_floor = int(getattr(settings, "TIME_BUDGET_HARD_FLOOR_SECONDS", 60))
                if self._remaining_seconds() <= hard_floor:
                    self._apply_deadline_bypass("task_deadline_exhausted")
                    await self.emit_event(
                        "warning", "⏰ 任务时间预算耗尽，停止发起新的编排轮次并收口"
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

                # 🔥 v3.0: 智能上下文压缩（在 LLM 调用前）
                if self.context_manager:
                    try:
                        self._conversation_history = await self.context_manager.compress_if_needed(
                            self._conversation_history
                        )
                    except Exception as e:
                        logger.error(f"[{self.name}] 上下文压缩失败: {e}")

                # sandbox-verification-hard-gate Task 15：每轮 LLM 决策前注入 trace
                # 摘要（此前调度/发现/工具轨迹），避免重复调度与重复分析；非致命。
                await self._inject_trace_summary()

                # structured-output-protocol Task 7：后端能力探测支持 tools 时
                # 注入调度轮三函数定义；探测不可用/未探测（属性为 None）时不传，
                # 保持 ReAct 文本协议现状（降级共存）。tool_choice 不传。
                orchestrator_tools = None
                backend_caps = getattr(self.llm_service, "backend_capabilities", None)
                if backend_caps is not None and getattr(backend_caps, "tools", False):
                    orchestrator_tools = self._build_orchestrator_tool_defs()

                # 调用 LLM 进行思考和决策（流式输出）
                try:
                    llm_output, tokens_this_round = await self.stream_llm_call(
                        self._conversation_history,
                        # 🔥 v3.0: 使用 Orchestrator 专用 temperature
                        temperature=get_agent_config().llm_temperature_orchestrator,
                        # 🔥 frequency_penalty/presence_penalty 通过 LLMConfig -> litellm_adapter.stream_complete 生效
                        tools=orchestrator_tools,
                    )
                except asyncio.CancelledError:
                    logger.info(f"[{self.name}] LLM call cancelled")
                    break

                self._total_tokens += tokens_this_round

                # Task 7：本轮是否为原生 tool_calls 响应（done chunk 聚合结果）
                tool_calls_this_round = getattr(self, "_last_tool_calls", None)

                # 🔥 检测空响应（tool_calls 形态正文为空属正常，不判空）
                if (not llm_output or not llm_output.strip()) and not tool_calls_this_round:
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
                    # Task 20：按空响应形态注入 nudge 前缀（形态 B=只思考无正文 /
                    # 形态 A=思考耗尽预算）；tools 协议提示可直接调用调度工具；
                    # other 形态 nudge 为空，维持下方泛化提示。计数/上限/sleep 不变
                    nudge = self._empty_response_nudge(
                        tool_hint=(
                            "或直接调用工具 dispatch_agent / summarize / finish"
                            if orchestrator_tools is not None else ""
                        )
                    )
                    retry_prompt = f"""收到空响应（第 {empty_retry_count} 次）。请严格按照以下格式输出你的决策：

Thought: [你对当前审计状态的思考]
Action: [dispatch_agent|summarize|finish]
Action Input: {{"参数": "值"}}

当前可调度的子 Agent: {list(self.sub_agents.keys())}
当前已收集发现: {len(self._all_findings)} 个

请立即输出你的下一步决策。"""

                    if nudge:
                        retry_prompt = f"{nudge}\n\n{retry_prompt}"

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

                # 解析 LLM 的决策（Task 7 双形态）
                if tool_calls_this_round:
                    # 原生 tool_calls 形态：直接映射为 AgentStep。服务端
                    # tool-call-parser 保证结构合法，不参与文本格式错误重试；
                    # 未知函数名/坏参数由现有"未知操作"/参数缺失观察分支自愈。
                    step = self._step_from_tool_calls(tool_calls_this_round)
                else:
                    # 文本协议（Thought:/Action:/Action Input:）：现状路径不变
                    step = self._parse_llm_response(llm_output)

                if not step:
                    # 🔥 v3.0: LLM 输出格式不正确，智能重试策略
                    format_retry_count = getattr(self, '_format_retry_count', 0) + 1
                    self._format_retry_count = format_retry_count

                    # 🔥 方案3：阈值从 5 提高到 10
                    if format_retry_count >= 10:
                        logger.error(f"[{self.name}] Too many format errors ({format_retry_count}), pausing")
                        await self._pause_for_recoverable_error(
                            reason="format_error",
                            error_code="format_error",
                            user_message=f"连续 {format_retry_count} 次格式错误，任务已暂停。建议调整模型配置（Temperature 建议 0.3-0.5）或切换更强大的模型后点击继续。",
                        )

                    # 🔥 方案4：优化错误提示词策略
                    if format_retry_count <= 2:
                        # 第 1-2 次：静默重试，不添加提示（避免污染上下文）
                        logger.info(f"[{self.name}] Format error #{format_retry_count}, silent retry")
                        await self.emit_event("info", f"格式解析失败（第{format_retry_count}次），静默重试...")
                        continue
                    elif format_retry_count <= 5:
                        # 第 3-5 次：添加简短友好的提示 + 示例
                        logger.warning(f"[{self.name}] Format error #{format_retry_count}, adding helpful hint")
                        await self.emit_event("warning", f"格式错误（第{format_retry_count}次），添加示例提示...")
                        self._conversation_history.append({
                            "role": "user",
                            "content": """请按照以下格式输出你的决策：

Thought: [你的思考过程]
Action: [动作名称，如 dispatch_agent、finish、summarize]
Action Input: [有效的JSON对象]

示例：
Thought: 我需要调度 verification Agent 来验证发现的漏洞
Action: dispatch_agent
Action Input: {"agent": "verification", "task": "验证 SSRF 漏洞", "context": "共1个漏洞需要验证"}"""
                        })
                        continue
                    else:
                        # 第 6+ 次：详细说明 + 当前状态提示
                        logger.warning(f"[{self.name}] Format error #{format_retry_count}, detailed guidance")
                        await self.emit_event("warning", f"格式错误（第{format_retry_count}次），提供详细指导...")
                        self._conversation_history.append({
                            "role": "user",
                            "content": f"""格式解析失败了 {format_retry_count} 次。请严格按照以下要求输出：

1. **必须包含三个字段**：Thought、Action、Action Input
2. **Action 只能是单个单词**：dispatch_agent、finish、summarize（不要使用空格或特殊符号）
3. **Action Input 必须是有效的 JSON 对象**：使用双引号，正确闭合大括号

当前任务状态：
- 已调度的 Agent: {list(self._dispatched_tasks.keys())}
- 发现的漏洞数: {len(self._all_findings)}
- 当前迭代: {iteration}

请重新输出你的决策。"""
                        })
                        continue

                # 重置格式重试计数器
                self._format_retry_count = 0

                self._steps.append(step)

                # 🔥 发射 LLM 思考内容事件 - 展示编排决策的思考过程
                if step.thought:
                    await self.emit_llm_thought(step.thought, iteration + 1)

                # 添加 LLM 响应到历史
                if tool_calls_this_round and step:
                    # Task 7：tool_calls 形态合成 ReAct 文本入历史——多轮历史与
                    # 文本协议自洽（后续 Observation 仍为 user 消息），避免 assistant
                    # 空 content 在 tools 模式下造成后端对话状态混乱
                    history_content = (
                        f"Action: {step.action}\n"
                        f"Action Input: {json.dumps(step.action_input, ensure_ascii=False)}"
                    )
                else:
                    history_content = llm_output
                self._conversation_history.append({
                    "role": "assistant",
                    "content": history_content,
                })

                # sandbox-verification-hard-gate Task 21：无效 tool_calls 强 nudge 自愈。
                # R2 实测模型退化三形态（空 name/dispatch_agent 空参/坏 JSON）下，泛化
                # "未知操作"提示强度不足、喂回后模型继续退化；分发前分类拦截，喂
                # schema 重喂 observation（照常走 llm_observation 事件，前端可见），
                # 连续 ≥2 次追加协议降级引导（文本解析路径保留可承接）。
                # _invalid_tool_calls_count 为连续计数，与 _empty_retry_count（空响应）
                # 相互独立：空响应轮在此之前 continue，互不触碰。
                if tool_calls_this_round:
                    invalid_kind = self._classify_invalid_tool_call(tool_calls_this_round, step)
                    if invalid_kind is not None:
                        invalid_count = getattr(self, "_invalid_tool_calls_count", 0) + 1
                        self._invalid_tool_calls_count = invalid_count
                        observation = self._invalid_tool_call_nudge(invalid_kind, invalid_count)
                        step.observation = observation
                        logger.warning(
                            f"[{self.name}] 无效 tool_calls（连续第 {invalid_count} 次）: {invalid_kind}"
                        )
                        await self.emit_llm_decision(
                            "工具调用无效",
                            {
                                "missing_name": "函数名缺失",
                                "unknown_name": "函数名未知",
                                "empty_dispatch_args": "dispatch_agent 参数缺失",
                                "bad_json": "参数 JSON 非法",
                            }[invalid_kind],
                        )
                        await self.emit_llm_observation(observation)
                        self._conversation_history.append({
                            "role": "user",
                            "content": f"Observation:\n{observation}",
                        })
                        continue
                # 有效决策轮（tool_calls 合法或文本协议解析成功）复位连续无效计数
                self._invalid_tool_calls_count = 0

                # 执行 LLM 决定的操作
                if step.action == "finish":
                    # Task 11 (finding-output-floor): finish 前兜底——Analysis 达调度
                    # 上限或产出下限违规时，Semgrep 预扫发现兜底落库（幂等）；仍无
                    # 可验证产出则按覆盖不足语义收口（completed_with_gaps）。
                    if self._dispatched_tasks.get("analysis", 0) >= 3 or self._output_floor_violated:
                        try:
                            await self._apply_semgrep_fallback()
                        except Exception as e:
                            logger.warning(f"[Orchestrator] Semgrep fallback on finish failed (non-fatal): {e}")
                        self._apply_output_floor_closeout()
                    # 🔥 弹性终止门禁：三层门禁保障审计质量
                    # Task 11: has_findings 按可验证产出口径（recon 上下文线索不算）；
                    # Task 12: 门禁拦截消息/observation 的计数同口径（候选计入、
                    # recon 不计），避免向 LLM 与 observations 报含 recon 的虚高数量。
                    actionable_findings = self._actionable_findings()
                    actionable_count = len(actionable_findings)
                    has_findings = actionable_count > 0
                    verification_dispatched = "verification" in self._dispatched_tasks
                    verification_count = self._dispatched_tasks.get("verification", 0)
                    has_sandbox_evidence = self._has_valid_sandbox_evidence()
                    # R4: 连续被门禁拒绝达上限后，停止强制重派 verification，直接放行按覆盖率收尾。
                    # 根治历史"拒绝→重派→再拒绝"的 token 黑洞循环（生产任务 8 轮失控）。
                    try:
                        max_redispatch = getattr(
                            get_agent_config(), "verification_max_force_redispatch", 3
                        )
                    except Exception:
                        max_redispatch = 3
                    if has_findings and (not verification_dispatched or (verification_count > 0 and not has_sandbox_evidence)):
                        self._finish_gate_rejections += 1
                        self._record_gate_observation(
                            "verification_evidence_gate",
                            f"发现 {actionable_count} 个漏洞但无有效沙箱证据（第 {self._finish_gate_rejections} 次拒绝）",
                        )
                    if has_findings and (not verification_dispatched or (verification_count > 0 and not has_sandbox_evidence)) and self._finish_gate_rejections < max_redispatch:
                        if not verification_dispatched:
                            await self.emit_event(
                                "warning",
                                f"⚠️ 系统强制干预：发现 {actionable_count} 个漏洞但未调度沙箱验证，拒绝完成审计"
                            )
                            await self.emit_llm_decision("拒绝完成", "系统强制要求先调度 verification Agent 进行沙箱验证")
                            prompt_suffix = (
                                "请立即调度 verification Agent:\n"
                                "Thought: [我需要调度 verification Agent 进行沙箱验证]\n"
                                "Action: dispatch_agent\n"
                                f"Action Input: {{\"agent\": \"verification\", \"task\": \"验证所有发现的漏洞，使用 sandbox_exec 在沙箱中执行 PoC\", \"context\": \"共有 {actionable_count} 个漏洞需要验证\"}}"
                            )
                        else:
                            await self.emit_event(
                                "warning",
                                f"⚠️ 系统强制干预：发现 {actionable_count} 个漏洞，已调度 {verification_count} 次验证但无有效沙箱证据（0/{actionable_count} 通过验证），拒绝完成审计"
                            )
                            await self.emit_llm_decision("拒绝完成", f"已调度 {verification_count} 次验证但无有效沙箱证据，必须再次调度并确保 sandbox_exec 执行")
                            prompt_suffix = (
                                f"你已调度 {verification_count} 次验证但 0 条发现通过沙箱确认。\n"
                                "请再次调度 verification Agent，并确保使用 sandbox_exec 工具执行 PoC。\n"
                                "仅凭代码分析判断漏洞是不够的，必须在沙箱中实际验证。\n\n"
                                "Action: dispatch_agent\n"
                                f"Action Input: {{\"agent\": \"verification\", \"task\": \"再次验证所有发现的漏洞，必须使用 sandbox_exec 在沙箱中执行 PoC\", \"context\": \"已调度 {verification_count} 次但无沙箱证据，共 {actionable_count} 个漏洞\"}}"
                            )
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **系统强制干预**: 你发现了 {actionable_count} 个漏洞但还没有有效的沙箱验证证据！\n\n"
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
                        # Task 8: 记录放行原因，收尾标记在最终 return 前（补验结果合入后）执行。
                        self._gate_release_reason = "gate_release_after_max_redispatch"
                        await self.emit_event(
                            "warning",
                            f"⚠️ 验证门禁已达最大重试次数（{max_redispatch} 次），不再强制重派 verification，按覆盖率收尾"
                        )
                        await self.emit_llm_decision(
                            "放行完成",
                            f"已连续拒绝 {max_redispatch} 次仍无沙箱证据，停止强制重派，按当前结果收尾",
                        )
                        # T6 (REQ-VC-2): 放行前程序化收口——剩余未验证漏洞补发一次 verification 调度
                        await self._maybe_dispatch_force_verification()
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
                    # 用 _all_findings 现取值（不得用本轮初的 actionable_findings
                    # 快照）：R4 放行分支的 _maybe_dispatch_force_verification 在
                    # 此之前 await 完成并把验证结果 merge 回 _all_findings
                    # （_merge_or_append_finding 以新 dict 替换索引位置），快照会
                    # 滞留合并前的旧 verification_status 造成误判未验证。
                    actionable_now = [
                        f for f in self._all_findings if is_verification_work_item(f)
                    ]
                    unverified_findings = [
                        f for f in actionable_now
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
                                f"⚠️ **全量验证门禁**: 你已发现 {len(actionable_now)} 个漏洞，"
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
                    for finding in self._actionable_findings():
                        dim = CoverageMatrix.map_finding_to_dimension(finding.get("vulnerability_type", ""))
                        if dim:
                            coverage_matrix.mark_covered(dim, evidence=finding.get("title", ""))
                    for pattern in self._search_registry.get("grep_patterns", set()):
                        dim = CoverageMatrix.map_pattern_to_dimension(pattern)
                        if dim:
                            coverage_matrix.mark_shallow(dim, evidence=f"grep: {pattern}")
                    hard_coverage = coverage_matrix.to_report()

                    if len(self._actionable_findings()) > 0 and not hard_coverage.is_sufficient and self._hard_coverage_block_count < 3:
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
                    elif len(self._actionable_findings()) > 0 and not hard_coverage.is_sufficient and self._hard_coverage_block_count >= 3:
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
                    self._finish_accepted = True  # Task 8: 区分正常 finish 与轮次耗尽退出
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
                    # Task 21：observation 必须回写 step——否则循环末尾入历史的是
                    # "Observation:\nNone"，自愈提示实际未喂回模型（tool_calls 形态的
                    # 同类问题已由分发前拦截块承接，此分支服务文本协议未知 Action）
                    step.observation = observation
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
                # 🔥 fix-audit-time-budget-2026-08: deadline 命中（watchdog）→ 预算耗尽优雅
                # 收口：返回 success 结果 + task_timeout bypass，保全已发现与已声明产出；
                # 真正的用户取消仍由 agent_tasks.is_task_cancelled 终态否决权兜底
                self._finalize_budget_metadata()
                if self._deadline_hit:
                    logger.warning(
                        f"[{self.name}] Task budget exhausted, graceful wrap-up: "
                        f"{len(self._all_findings)} findings, {self._dispatch_failures} dispatch failures"
                    )
                    return AgentResult(
                        success=True,
                        data={
                            "findings": self._all_findings,
                            "summary": final_result or self._generate_default_summary(),
                            "observations": list(self._gate_observations),
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
                        metadata={
                            "coverage_bypassed": self._coverage_bypassed,
                            "coverage_info": self._coverage_bypass_info,
                        },
                    )
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

            # Semgrep 预扫发现默认仅作线索注入子 Agent 上下文，不直接合并到最终
            # 结果（避免规则 ID 直接灌水，问题三修复）；Task 11 兜底例外：
            # Analysis 全部派发后 0 可验证产出时，去重后的预扫发现以候选
            # （source=semgrep_fallback）落库交沙箱验证，已合并数见 fallback_n。
            if self._semgrep_findings:
                fallback_n = sum(
                    1 for f in self._all_findings
                    if isinstance(f, dict) and f.get("source") == "semgrep_fallback"
                )
                logger.info(
                    f"[Orchestrator] {len(self._semgrep_findings)} Semgrep prescan findings "
                    f"used as context leads ({fallback_n} merged as verification candidates)"
                )
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

            # 🔥 fix-audit-time-budget-2026-08: 收口统一预算 metadata（reason 优先级链）
            self._finalize_budget_metadata()

            # Task 11（修复轮 1 I2）：轮次耗尽路径不经过 finish 门禁与 max_dispatch
            # 分支，收尾段幂等补跑 Semgrep 兜底落库与产出下限收口——否则 0 findings
            # 耗尽退出时兜底候选不落库、output_floor 违规不置 coverage_bypassed。
            await self._finalize_output_floor_gate()

            # 🔥 覆盖率兜底检查：20轮耗尽时安全阀可能未触发，需在此兜底
            # （条件保持 _all_findings 真值：recon 线索在场时仍进入评估，
            # 评估口径由 _evaluate_current_coverage 内部排除 recon）
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

            # T6 扩展（REQ-VC-2）：主循环退出（轮次/覆盖率耗尽）路径不经过任何验证门禁——
            # 直接在此收尾前程序化补验一次未验证 finding，杜绝"全量零证据收尾"。
            # 生产回归 ec0985ad 证实该路径现实存在（analysis 3 轮后轮次耗尽 → 5 finding 全零证据）。
            try:
                await self._maybe_dispatch_force_verification()
            except Exception as e:
                logger.warning(f"[Orchestrator] Force verification on finalize failed (non-fatal): {e}")

            # sandbox-verification-hard-gate Task 8: 放行收口——R4 达限放行/轮次耗尽
            # 退出两条路径上，补验后仍零沙箱尝试的未验证 finding 强制写
            # sandbox_skip_reason（不升级验证状态），报告据此呈现"未沙箱验证清单"。
            self._apply_gate_release_marking()

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
            # 🔥 v3.0: 最终化追踪文件
            if self.trace_manager:
                try:
                    self.trace_manager.finalize()
                    logger.info(f"[{self.name}] 审计追踪文件已保存: {self.trace_manager.trace_md}")
                except Exception as e:
                    logger.error(f"[{self.name}] 追踪文件最终化失败: {e}")

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
        """
        解析 LLM 响应（增强容错性）

        v3.0 改进：
        - 支持 Action 中的连字符和下划线
        - 支持中英文冒号混用
        - 更宽松的 Action Input 提取
        - 记录解析失败的原因用于调试
        """
        # 🔥 v2.1: 预处理 - 移除 Markdown 格式标记（LLM 有时会输出 **Action:** 而非 Action:）
        cleaned_response = response
        cleaned_response = re.sub(r'\*\*Action:\*\*', 'Action:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Action Input:\*\*', 'Action Input:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Thought:\*\*', 'Thought:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Observation:\*\*', 'Observation:', cleaned_response)

        # 🔥 v3.0: 兼容中文冒号
        cleaned_response = re.sub(r'Action：', 'Action:', cleaned_response)
        cleaned_response = re.sub(r'Action Input：', 'Action Input:', cleaned_response)
        cleaned_response = re.sub(r'Thought：', 'Thought:', cleaned_response)

        # 提取 Thought
        thought_match = re.search(r'Thought:\s*(.*?)(?=Action:|$)', cleaned_response, re.DOTALL)
        thought = thought_match.group(1).strip() if thought_match else ""

        # 🔥 v3.0: 提取 Action（增强容错性）
        # 优先匹配标准格式：单词 + 下划线 + 连字符
        action_match = re.search(r'Action:\s*([\w\-]+)', cleaned_response)
        if not action_match:
            # 降级：匹配任何非换行字符（去除首尾空格）
            action_match = re.search(r'Action:\s*([^\n]+?)(?:\s*\n|$)', cleaned_response)

        if not action_match:
            logger.warning(f"[{self.name}] 解析失败：未找到 Action 字段")
            logger.debug(f"[{self.name}] 响应内容（前500字符）: {cleaned_response[:500]}")
            return None

        action = action_match.group(1).strip()

        # 🔥 v3.0: 提取 Action Input（更宽松的匹配）
        input_match = re.search(r'Action Input:\s*(.*?)(?=\n(?:Thought:|Action:|Observation:)|$)', cleaned_response, re.DOTALL)
        if not input_match:
            logger.warning(f"[{self.name}] 解析失败：未找到 Action Input 字段")
            logger.debug(f"[{self.name}] Action: {action}, 响应内容（前500字符）: {cleaned_response[:500]}")
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

        logger.debug(f"[{self.name}] 解析成功: action={action}, input_keys={list(action_input.keys()) if isinstance(action_input, dict) else 'not_dict'}")

        return AgentStep(
            thought=thought,
            action=action,
            action_input=action_input,
        )

    # ============ 原生 tools 协议（structured-output-protocol Task 7）============
    # 能力探测 tools=True 时，调度轮携带 OpenAI tools 定义，模型 tool_calls 响应
    # 直接映射到现有 action 分发；文本协议（Thought:/Action:/Action Input:）路径
    # 原样保留（降级共存，spec llm-structured-output 第三 Requirement）。

    # Task 21：tools 协议合法动作与 dispatch_agent 合法 agent 枚举
    # （与 _build_orchestrator_tool_defs 的三函数 schema 保持一致）
    _ORCH_TOOL_ACTIONS = ("dispatch_agent", "summarize", "finish")
    _DISPATCH_AGENT_NAMES = ("recon", "analysis", "verification")

    def _build_orchestrator_tool_defs(self) -> list[dict[str, Any]]:
        """调度轮 OpenAI tools 定义：三函数与文本协议 Action 一一对应。

        不传 tool_choice：模型可自由选择工具或文本（温和约束，避免强推导致
        模型混乱）；finish 无参数，现有 finish 门禁链（沙箱证据/覆盖率等）不变。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "dispatch_agent",
                    "description": (
                        "调度一个子 Agent 执行审计任务。"
                        "recon=信息收集（项目结构/技术栈/入口点），"
                        "analysis=深度代码审计与漏洞检测，"
                        "verification=在沙箱中验证发现的漏洞并生成 PoC。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "enum": ["recon", "analysis", "verification"],
                                "description": "要调度的子 Agent 名称",
                            },
                            "task": {
                                "type": "string",
                                "description": "具体任务描述（目标与范围）",
                            },
                            "context": {
                                "type": "string",
                                "description": "任务上下文（已知信息、前置发现）",
                            },
                        },
                        "required": ["agent", "task"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": (
                        "完成审计并收尾。仅在审计覆盖充分、发现均已处理后调用；"
                        "系统仍会执行验证证据/覆盖率门禁检查。"
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "summarize",
                    "description": "查看当前已收集发现的汇总（不结束审计）",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def _step_from_tool_calls(
        self, tool_calls: list[dict[str, Any]] | None
    ) -> AgentStep | None:
        """tool_calls 响应映射为 AgentStep（与文本协议 _parse_llm_response 对等）。

        取第一个工具调用：function.name → action，function.arguments（JSON 字符串）
        → action_input。tool_calls 形态由服务端 tool-call-parser 保证结构合法，因此
        不走文本格式错误重试计数；映射本身不抛错（非法 JSON/非对象参数降级为空
        dict、空/未知函数名原样保留），无效形态的分类与强 nudge 自愈由主循环
        Task 21 拦截块（_classify_invalid_tool_call）在分发前统一处理。
        """
        if not tool_calls:
            return None
        call = tool_calls[0] or {}
        action = str(call.get("name") or "").strip()
        arguments = call.get("arguments")
        if isinstance(arguments, dict):
            parsed: dict[str, Any] = arguments
        elif isinstance(arguments, str) and arguments.strip():
            try:
                parsed = json.loads(arguments)
            except (json.JSONDecodeError, ValueError):
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
        else:
            parsed = {}
        return AgentStep(thought="", action=action, action_input=parsed)

    # ============ 无效 tool_calls 强 nudge 自愈（sandbox-verification-hard-gate Task 21）============

    def _classify_invalid_tool_call(
        self, tool_calls: list[dict[str, Any]] | None, step: AgentStep
    ) -> str | None:
        """判定 tool_calls 响应是否为模型退化无效形态（Task 21）。

        返回 None（合法，进入正常分发）或分类码：
        - "bad_json"：arguments 为非空字符串但不是合法 JSON 对象——输出损坏，
          坏 JSON 优先判定（参数已不可读，下游参数校验无意义）；
        - "missing_name"：函数名为空/缺失（R2 实测形态①）；
        - "unknown_name"：函数名不在 dispatch_agent/summarize/finish 集合；
        - "empty_dispatch_args"：dispatch_agent 的 agent 缺失/空/不在枚举，
          或 task 缺失/空白（R2 实测形态②，现状会真实调度一次空任务）。
        agent 枚举比对大小写不敏感，与 _dispatch_agent 的 lower 匹配一致。
        """
        call = (tool_calls or [{}])[0] or {}
        arguments = call.get("arguments")
        if isinstance(arguments, str) and arguments.strip():
            try:
                reparsed = json.loads(arguments)
            except (json.JSONDecodeError, ValueError):
                return "bad_json"
            if not isinstance(reparsed, dict):
                return "bad_json"

        action = (step.action or "").strip()
        if not action:
            return "missing_name"
        if action not in self._ORCH_TOOL_ACTIONS:
            return "unknown_name"
        if action == "dispatch_agent":
            params = step.action_input if isinstance(step.action_input, dict) else {}
            agent_name = str(params.get("agent") or "").strip().lower()
            task = params.get("task")
            if (
                agent_name not in self._DISPATCH_AGENT_NAMES
                or not isinstance(task, str)
                or not task.strip()
            ):
                return "empty_dispatch_args"
        return None

    def _invalid_tool_call_nudge(self, kind: str, count: int) -> str:
        """按无效分类返回强 nudge observation；连续 ≥2 次追加协议降级引导（Task 21）。

        降级引导模型改用 Thought:/Action:/Action Input: 文本格式——文本解析
        路径（_parse_llm_response）原样保留，降级后的文本响应可直接承接。
        """
        if kind in ("missing_name", "unknown_name"):
            observation = (
                "【系统提示：工具调用缺少有效的函数名。请重新输出，可用操作与参数 schema："
                "dispatch_agent(agent: 'recon'|'analysis'|'verification', task: str, context: str)、"
                "summarize()、finish()。注意 agent 与 task 参数必须非空。】"
            )
        elif kind == "empty_dispatch_args":
            observation = (
                "【系统提示：dispatch_agent 的 agent 参数缺失或无效，"
                "必须为 recon/analysis/verification 之一；task 必须为非空的具体任务描述。"
                "请重新调用。】"
            )
        else:  # bad_json
            observation = "【系统提示：工具调用参数不是合法 JSON，请重新输出完整参数。】"

        if count >= 2:
            observation += (
                f"\n【系统提示：已连续 {count} 次工具调用无效。"
                "请改用文本格式输出：Thought: ... / Action: dispatch_agent 或 finish / "
                "Action Input: {...}】"
            )
        return observation

    # sandbox-verification-hard-gate Task 15：trace 读侧——把执行轨迹摘要注入
    # 主循环对话历史，并经 previous_results["trace_summary"] 传递给子 Agent。
    # 数据源为 trace 内存 entries/stats（Task 14 M3：add_verification_result 只写
    # markdown 不入 entries，故摘要含调度/工具/发现/压缩/LLM 轨迹，不含验证裁决；
    # 验证完整结论见 audit_trace 文件，由 API audit_trace_path 字段提供入口）。
    def _trace_summary_raw(self) -> str | None:
        """读取 trace 生成摘要（截断 2000 字符）。trace 关闭或首轮空壳（0 调度
        0 事件 0 门禁裁决）返回 None；读取/生成异常上抛，由调用方按场景兜底。"""
        tm = getattr(self, "trace_manager", None)
        if tm is None:
            return None
        stats = getattr(tm, "stats", {}) or {}
        entries = getattr(tm, "entries", []) or []
        gates = getattr(self, "_gate_observations", []) or []
        # 首轮（尚无任何调度/事件/门禁裁决）注入空壳摘要只是噪声，跳过。
        if not entries and stats.get("agents_dispatched", 0) == 0 and not gates:
            return None
        summary = tm.get_summary_for_agent()
        if not summary or not summary.strip():
            return None
        # I1（review 第 1 轮）：spec 要求摘要含"关键门禁裁决"——追加最近 5 条
        # 门禁裁决（gate_release/output_floor/dispatch_budget/semgrep_fallback 等），
        # 这是后续轮避免重复调度最该看到的决策信息；空列表省略该段。主循环注入与
        # 子 Agent 传递同走本方法，一处修复两处生效。
        if gates:
            summary += "\n## 关键门禁裁决\n"
            for obs in gates[-5:]:
                gate = str(obs.get("gate", "?"))
                reason = str(obs.get("reason", "")).replace("\n", " ")[:120]
                summary += f"- [{gate}] {reason}\n"
        return summary[:2000]

    def _build_trace_summary(self) -> str | None:
        """同步非致命版（子 Agent 派发用）：异常 logger.warning 后返回 None。"""
        try:
            return self._trace_summary_raw()
        except Exception as e:
            logger.warning(f"[{self.name}] trace 摘要生成失败，跳过传递（非致命）: {e}")
            return None

    async def _inject_trace_summary(self) -> None:
        """每轮主循环 LLM 调用前注入最新 trace 摘要（user 消息）。

        历史中至多保留一条摘要段：注入前移除上一轮同名段（摘要随 entries/stats
        每轮增长，旧段过时且浪费 token）。摘要生成失败时发一条 warning 事件并
        跳过，不阻断主循环。
        """
        marker = "（系统提示：此前执行轨迹摘要"
        try:
            summary = self._trace_summary_raw()
        except Exception as e:
            logger.warning(f"[{self.name}] trace 摘要生成失败，跳过本轮注入（非致命）: {e}")
            try:
                await self.emit_event(
                    "warning",
                    f"审计轨迹摘要暂不可用，已跳过本轮注入（任务继续）: {e}",
                )
            except Exception:
                pass
            return
        if not summary:
            return
        # 移除上一轮注入的同名摘要段，保持对话历史中至多一条最新摘要
        self._conversation_history = [
            m for m in self._conversation_history
            if not (m.get("role") == "user" and marker in m.get("content", ""))
        ]
        self._conversation_history.append({
            "role": "user",
            "content": (
                f"{marker}——以下为此前各 Agent 的调度/发现/工具轨迹，"
                "请据此避免重复调度与重复分析；完整追踪见审计文件）：\n\n"
                f"{summary}"
            ),
        })

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
        # 调度上限固定 3 次（达限不增加调度次数；analysis 达限走下方 Semgrep
        # 兜底/产出下限收口分支）。覆盖率门禁的"3 次拦截后自动放行"由
        # _hard_coverage_block_count 独立控制，不提升调度上限——旧注释
        # "提升上限到 4" 为陈旧表述，无对应代码。
        max_dispatch = 3
        if dispatch_count >= max_dispatch:
            if agent_name == "analysis":
                # Task 11 (finding-output-floor): Analysis 达调度上限 = "全部派发
                # 完成"——先做 Semgrep 兜底落库（0 产出时静态扫描发现转待验证候选），
                # 再按产出下限信号决定收口方式（历史行为是无条件自动放行 finish）。
                fallback_added = await self._apply_semgrep_fallback()
                if self._output_floor_violated and self._apply_output_floor_closeout():
                    # 违规且兜底后仍 0 可验证产出：覆盖不足收口（completed_with_gaps）
                    return f"""## ⚠️ 重复调度警告

你已经调度 analysis Agent {dispatch_count} 次。Analysis 强制总结未按要求产出任何候选或书面豁免（**产出下限违规**），Semgrep 静态扫描兜底也没有可落库的发现。

请直接使用 finish 操作结束审计。任务将按**覆盖不足（completed_with_gaps）**收口，报告中会呈现"分析未按要求产出候选"。

当前已收集的发现数量: {len(self._all_findings)}"""
                if self._output_floor_violated and fallback_added > 0:
                    # 违规但本轮 Semgrep 兜底候选刚落库：不自动放行，强制先沙箱验证
                    if not any(o.get("gate") == "output_floor" for o in self._gate_observations):
                        self._record_gate_observation(
                            "output_floor",
                            f"Analysis 产出下限违规，{fallback_added} 条 Semgrep 兜底候选已落库，"
                            "须经沙箱验证后方可收口",
                        )
                    return f"""## ⚠️ 重复调度警告

你已经调度 analysis Agent {dispatch_count} 次。Analysis 强制总结未按要求产出候选（**产出下限违规**），系统已将 **{fallback_added} 条 Semgrep 静态扫描发现作为兜底候选落库**（静态扫描兜底候选，confidence=0.5，必须沙箱验证）。

请立即调度 verification Agent 对这些兜底候选执行沙箱验证，验证完成前不得收口：
Action: dispatch_agent
Action Input: {{"agent": "verification", "task": "验证 {fallback_added} 个 Semgrep 兜底候选，使用 sandbox_exec 在沙箱中执行 PoC", "context": "兜底候选 source=semgrep_fallback，共 {fallback_added} 个"}}

当前已收集的发现数量: {len(self._all_findings)}"""
                # 未违规，或违规信号粘滞但后续轮次已产出可验证发现（closeout
                # 返回 False 且本轮无新增兜底）：维持历史自动放行语义
                if self._hard_coverage_block_count < 3:
                    self._hard_coverage_block_count = 3
                    logger.info(f"[Orchestrator] Analysis dispatched {dispatch_count} times, auto-bypassing coverage gate")
                fallback_note = ""
                if fallback_added:
                    fallback_note = (
                        f"\n\n📋 系统已将 {fallback_added} 条 Semgrep 静态扫描发现作为兜底候选落库"
                        "（source=semgrep_fallback），请调度 verification Agent 沙箱验证后再 finish。"
                    )
                return f"""## ⚠️ 重复调度警告

你已经调度 {agent_name} Agent {dispatch_count} 次了。

如果之前的调度没有返回有用的结果，请考虑：
1. 直接使用 finish 操作结束审计并汇总已有发现（覆盖率门禁已自动放行）
2. 提供更具体的任务描述

当前已收集的发现数量: {len(self._all_findings)}
注意：覆盖率门禁已自动放行，你可以直接 finish。
{"（提示：低置信可疑点也应作为候选（needs_verification=true）计入产出并交沙箱验证，不要因为 Analysis 没有给出'确认漏洞'就反复重派——候选本身就是有效产出。）" if agent_name == "analysis" else ""}{fallback_note}"""
            return f"""## ⚠️ 重复调度警告

你已经调度 {agent_name} Agent {dispatch_count} 次了。

如果之前的调度没有返回有用的结果，请考虑：
1. 直接使用 finish 操作结束审计并汇总已有发现（覆盖率门禁已自动放行）
2. 提供更具体的任务描述

当前已收集的发现数量: {len(self._all_findings)}
注意：覆盖率门禁已自动放行，你可以直接 finish。"""

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

            # sandbox-verification-hard-gate Task 15：trace 摘要经 previous_results
            # 传递给子 Agent（读侧接线），子 Agent 在首轮消息注入以避免重复劳动。
            trace_summary = self._build_trace_summary()
            if trace_summary:
                previous_results["trace_summary"] = trace_summary

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

            # 🔥 v3.0: 记录 Agent 调度到追踪文件
            if self.trace_manager:
                self.trace_manager.add_agent_dispatch(
                    agent_name=agent_name,
                    task=task,
                    context=context,
                    parent_agent=self.name
                )

            # 🔥 fix-audit-time-budget-2026-08: 预算将尽拒发新调度；复位调度超时锁存
            # （reset 内部复判外部取消回调，用户取消锁存不会被洗掉）
            budget_refusal = self._budget_refusal(agent_name)
            if budget_refusal:
                await self.emit_event("info", budget_refusal)
                return f"## {agent_name} Agent 未调度\n\n{budget_refusal}"
            agent.reset_dispatch_cancel()

            # 🔥 执行子 Agent - 支持取消和超时
            # 调度超时 = min(类型上限, 剩余任务预算)，见 _resolve_dispatch_timeout
            timeout = self._resolve_dispatch_timeout(agent_name)

            # run_task 提到外层：超时/取消早退时抢救子 Agent 已产出的 findings
            # （见 _salvage_dispatched_findings；生产 9344d5dd 断点 B）
            run_task: asyncio.Task | None = None

            async def run_with_cancel_check() -> AgentResult:
                """包装子 Agent 执行，定期检查取消状态"""
                nonlocal run_task
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

                        # 🔥 fix-audit-time-budget-2026-08: 剩余预算不足时请求 analysis 软停止交卷
                        if self._maybe_request_soft_stop(agent, agent_name):
                            await self.emit_event(
                                "info",
                                "⏳ 任务时间预算将尽，请求 Analysis Agent 立即总结交卷",
                            )

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
                self._dispatch_failures += 1
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
                # 断点 B 修复：早退前抢救子 Agent 取消收口已声明的 findings
                # （verification 的沙箱证据不得随超时整体丢弃，生产 9344d5dd）
                self._salvage_dispatched_findings(agent_name, run_task)
                return f"## {agent_name} Agent 执行超时\n\n子 Agent 执行超过 {timeout} 秒，已强制终止。请尝试更具体的任务或使用其他 Agent。"
            except asyncio.CancelledError:
                self._dispatch_failures += 1
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
                # 断点 B 修复：取消早退同样抢救已声明 findings
                self._salvage_dispatched_findings(agent_name, run_task)
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
                # 断点 B 修复：子 Agent 已正常返回但编排被取消，结果同样保全
                self._salvage_dispatched_findings(agent_name, run_task, result=result)
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

                # 🔥 v3.0: 压缩子 Agent 输出（如果过长）
                original_output = str(data)
                if self.context_manager and len(original_output) > get_agent_config().agent_output_compression_threshold:
                    logger.info(f"[Orchestrator] {agent_name} 输出过长 ({len(original_output)} 字符)，开始压缩...")
                    try:
                        compressed_summary = await self.context_manager.compress_agent_output(
                            agent_name=agent_name,
                            output=original_output,
                            max_length=10_000
                        )
                        # 注意：这里我们记录了压缩，但仍然使用完整 data 进行后续处理
                        # 压缩主要用于传递给 LLM 的上下文
                        logger.info(f"[Orchestrator] {agent_name} 输出已压缩并归档")
                    except Exception as e:
                        logger.error(f"[Orchestrator] {agent_name} 输出压缩失败: {e}")

                # 🔥 FIX: 保存 Agent 的完整结果，供后续 Agent 使用
                self._agent_results[agent_name] = data
                logger.info(f"[Orchestrator] Saved {agent_name} result with keys: {list(data.keys())}")

                # Task 11 (finding-output-floor): Analysis 强制总结产出下限信号
                # （Task 10 data.output_floor_violated）粘滞摄入，max_dispatch/
                # finish 门禁据此决定兜底落库与覆盖不足收口。
                if agent_name == "analysis":
                    self._ingest_analysis_floor_signal(data)

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

                        # T7 (REQ-VC-3): merge-back 统一入口（_sandbox_finding_id 精确匹配优先）
                        self._merge_or_append_finding(normalized_new)

                        # 🔥 v3.0: 记录漏洞发现到追踪文件
                        if self.trace_manager:
                            self.trace_manager.add_finding(
                                finding_type=normalized_new.get("type", "unknown"),
                                severity=normalized_new.get("severity", "medium"),
                                title=normalized_new.get("title", "未知漏洞"),
                                description=normalized_new.get("description", "")[:500],
                                file_path=normalized_new.get("file_path", ""),
                                line_number=normalized_new.get("line_number"),
                                code_snippet=normalized_new.get("code_snippet", "")[:300] if normalized_new.get("code_snippet") else None,
                                poc=normalized_new.get("poc", "")[:300] if normalized_new.get("poc") else None,
                                agent_source=agent_name
                            )

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
                # 🔥 fix-audit-time-budget-2026-08: 失败子 Agent 已声明的发现保全（spec R6）
                # 失败结果同样计入 _dispatch_failures（R7 诚实终态的计数口径）
                self._dispatch_failures += 1
                merged_count = self._merge_failed_result_findings(agent_name, result)
                note = f"\n\n（该 Agent 已声明的 {merged_count} 个发现已保留）" if merged_count else ""
                return f"## {agent_name} Agent 执行失败\n\n错误: {result.error}{note}"

        except Exception as e:
            self._dispatch_failures += 1
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

        # REQ-TH-6: LLM 输出防御——agents 数组元素偶发为字符串（非对象），跳过非法项。
        # 生产 27493b18：LLM 输出 malformed agents 数组致 `s.get` 崩溃（'str' object has no attribute 'get'）。
        agent_specs = [s for s in agent_specs if isinstance(s, dict)]

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
        验证文件路径是否真实存在。

        #4-5 修复（E2E 实证）：改用统一解析器 resolve_project_file——旧实现仅
        ``os.path.join(project_root, clean_path)`` 单点拼接，ZIP 样本文件位于
        src/ 子目录时把裸文件名/沙箱前缀路径误判为幻觉（15 声明仅 5 落库）。
        新解析器剥离 /workspace 前缀、追加 src/ 层级、basename 限深度兜底，
        **只有实际在项目树中找到文件才通过**——幻觉过滤能力不回退。

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

        from app.services.agent.utils.finding_path import resolve_project_file

        return resolve_project_file(project_root, file_path) is not None

    def _merge_or_append_finding(self, normalized_new: dict[str, Any]) -> None:
        """T7 (REQ-VC-3): 将标准化后的 finding merge 回 _all_findings。

        优先按 _sandbox_finding_id（verification 侧分配，验证输出 finding 携带）精确匹配
        原对象，命中即更新原对象（保留 id/finding_id），不追加副本——避免验证输出
        file_path 漂移导致同一漏洞出现两份；未命中回退到原有 file/line/type 模糊去重，
        仍不匹配则追加为新 finding。
        """
        # Create fingerprint for deduplication (file + description similarity)
        new_file = normalized_new.get("file_path", "").lower().strip()
        new_desc = (normalized_new.get("description", "") or "").lower()[:100]
        new_type = (normalized_new.get("vulnerability_type", "") or "").lower()
        new_line = normalized_new.get("line_start") or normalized_new.get("line", 0)
        new_fid = normalized_new.get("_sandbox_finding_id")

        # Check if exists (more flexible matching)
        found = False
        for i, existing_f in enumerate(self._all_findings):
            existing_file = (existing_f.get("file_path", "") or existing_f.get("file", "")).lower().strip()
            existing_desc = (existing_f.get("description", "") or "").lower()[:100]
            existing_type = (existing_f.get("vulnerability_type", "") or existing_f.get("type", "")).lower()
            existing_line = existing_f.get("line_start") or existing_f.get("line", 0)

            # T7 (REQ-VC-3): _sandbox_finding_id 精确匹配优先于模糊判定
            fid_match = bool(new_fid and existing_f.get("_sandbox_finding_id") == new_fid)

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

            if fid_match or ((same_file and (same_line or similar_desc or same_type)) or (no_file_path and title_match and same_type)):
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
                    # 语义键去重：断点 A 修复后证据同时在共享本体与验证返回结果上，
                    # 简单拼接会双计同源证据（见 _merge_attempts_lists_deduped）
                    if key == "sandbox_attempts" and isinstance(value, list) and len(value) > 0:
                        merged[key] = self._merge_attempts_lists_deduped(
                            merged.get(key) or [], value
                        )
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
                # T7 (REQ-VC-3): 保留原对象 id/finding_id 与 _sandbox_finding_id（不随验证输出漂移）
                if existing_f.get("id"):
                    merged["id"] = existing_f["id"]
                if existing_f.get("finding_id"):
                    merged["finding_id"] = existing_f["finding_id"]
                if existing_f.get("_sandbox_finding_id"):
                    merged["_sandbox_finding_id"] = existing_f["_sandbox_finding_id"]

                self._all_findings[i] = merged
                found = True
                logger.info(f"[Orchestrator] Merged finding: {new_file}:{merged.get('line_start', 0)} ({merged.get('vulnerability_type', '')})")
                break

        if not found:
            self._all_findings.append(normalized_new)
            logger.info(f"[Orchestrator] Added new finding: {new_file}:{new_line} ({new_type})")

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
        # #4-5 修复：解析成功后把归一化路径写回，后续验证绑定/落库/展示统一口径
        file_path = normalized.get("file_path", "")
        if file_path:
            project_root = self._runtime_context.get("project_root", "")
            if project_root:
                from app.services.agent.utils.finding_path import resolve_project_file

                resolved = resolve_project_file(project_root, file_path)
                if resolved is None:
                    logger.warning(
                        f"[Orchestrator] 🚫 过滤幻觉发现: 文件不存在 '{file_path}' "
                        f"(title: {normalized.get('title', 'N/A')[:50]})"
                    )
                    return None  # 返回 None 表示发现无效
                if resolved != file_path:
                    normalized["file_path"] = resolved

        # Confidence 阈值过滤：高置信阈值 0.7；分层候选（needs_verification=true
        # 且 0.1 ≤ confidence < 0.7）豁免放行，交 Verification Agent 沙箱证实/证伪
        # （spec finding-output-floor——低置信候选是沙箱验证的工作清单，不是误报）；
        # confidence < 0.1 的无依据猜测仍丢弃。
        confidence = normalized.get("confidence", 0)
        if isinstance(confidence, (int, float)) and confidence < MIN_CONFIDENCE_THRESHOLD:
            is_candidate = (
                bool(normalized.get("needs_verification"))
                and confidence >= MIN_CANDIDATE_CONFIDENCE
            )
            if not is_candidate:
                logger.info(
                    f"[Orchestrator] 🚫 低置信度过滤: confidence={confidence} < 0.7 "
                    f"(title: {normalized.get('title', 'N/A')[:50]})"
                )
                return None
            logger.info(
                f"[Orchestrator] 📋 低置信候选放行: confidence={confidence} "
                f"needs_verification=true，交沙箱验证 "
                f"(title: {normalized.get('title', 'N/A')[:50]})"
            )

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

        # T5 (REQ-VC-1): 严重程度排序映射，verification 交接 key_findings 按此对全量排序
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

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
                # T5 (REQ-VC-1): key_findings 改用 _all_findings 全量（含早期轮 finding），
                # 按严重程度排序；analysis_handoff 仍提供 summary/insights 等其余信息
                # Task 11: recon 上下文线索不进 Verification 交接（semgrep_fallback 候选保留）
                key_findings=sorted(
                    [f for f in self._all_findings
                     if is_verification_work_item(f)],
                    key=lambda f: severity_order.get(f.get("severity", "low"), 3),
                ),
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

                    # T5 (REQ-VC-1): key_findings 改用 _all_findings 全量（含早期轮 finding），
                    # 按严重程度排序，去掉 [:15] 截断
                    # Task 11: recon 上下文线索不进 Verification 交接
                    sorted_findings = sorted(
                        [f for f in self._all_findings
                         if is_verification_work_item(f)],
                        key=lambda x: severity_order.get(x.get("severity", "low"), 3)
                    )

                    for f in sorted_findings:
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
