"""P9 稳定性簇 T5 测试（2026-10-08）——端点软熔断 + degenerate 可见化 + 观测性。

覆盖：
1. EndpointHealth 端点软熔断（app/core/llm_endpoint_health.py）：
   滑窗（20/min_samples 10/空率 0.5）、backoff_factor 三档手推值、
   degraded 持续 30s 有序收口、冷却 120s 不重复、throttle 实际 sleep、
   救援帧 contextvar 免收、reset 清状态。
2. base.py 接入：
   maybe_throttle 在限流前、_record_endpoint_outcome 统一记账
   （no_physical_call 帧不入窗）、救援退避乘 backoff_factor、
   degenerate 掐断 warning（含「崩坏」）、有序收口标志。
3. D6 degenerate 可见化：
   warning 文案含「崩坏」不含「空响应」不含「静默」；
   _compute_llm_health 按「崩坏」归账 garbled_drops（empty_responses 不串）。
4. 观测性：content_end/llm_complete 的 tokens_used 写当轮 usage
   （degenerate 轮 0）；verification N 进 N 出缺条标注 missing_conclusions。
5. D5：LLM_GLOBAL_CONCURRENCY 默认 4。

生产根因（方案簇 C）：四任务并发共用单卡 SGLang，急性过载窗口空响应率
100%，既有熔断器对空响应零感知（记为成功）→ 各任务独立烧 5 轮退避，
百秒止损、覆盖不足；degenerate 丢弃在健康剖面不可见。
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

EP = "http://endpoint.example/v1"


# ============ 公共小工具 ============

class _Clock:
    """可控单调钟（注入 health._time）。"""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _SpyEmitter:
    """记录 emit 的 AgentEventData，供字段级断言。"""

    def __init__(self):
        self.items = []

    async def emit(self, data):
        self.items.append(data)


class _Captured:
    def __init__(self):
        self.events: list[tuple[str, str]] = []


class _FakeEmitter:
    def __init__(self, captured: _Captured):
        self.captured = captured

    async def emit(self, event) -> None:
        self.captured.events.append((event.event_type, event.message))


def _fresh_health():
    """构造带假钟/假 sleep 的 EndpointHealth。

    Returns (health, clock, sleeps)。
    """
    from app.core.llm_endpoint_health import EndpointHealth

    health = EndpointHealth()
    clock = _Clock()
    health._time = clock
    sleeps: list[float] = []

    async def _fake_sleep(sec):
        sleeps.append(sec)

    health._sleep = _fake_sleep
    return health, clock, sleeps


def _record_mix(health, endpoint, n_empty, n_good):
    for _ in range(n_empty):
        health.record(endpoint, True)
    for _ in range(n_good):
        health.record(endpoint, False)


# ============ A. EndpointHealth 滑窗与 degraded ============

class TestEndpointWindow:
    def test_six_empty_of_ten_degraded(self):
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 6, 4)
        assert health.is_degraded(EP) is True

    def test_four_empty_of_ten_not_degraded(self):
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 4, 6)
        assert health.is_degraded(EP) is False

    def test_min_samples_before_noop(self):
        # 6 连空但样本不足 10 → 不判 degraded
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 6, 0)
        assert health.is_degraded(EP) is False

    def test_window_eviction_recovers(self):
        # 20 帧 12 空 → degraded；再记 20 帧好样本，旧帧全驱逐 → 恢复
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 12, 8)
        assert health.is_degraded(EP) is True
        _record_mix(health, EP, 0, 20)
        assert health.is_degraded(EP) is False

    def test_unknown_endpoint_not_degraded(self):
        health, _, _ = _fresh_health()
        assert health.is_degraded("http://never-called/v1") is False

    def test_reset_clears_state(self):
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 10, 0)
        assert health.is_degraded(EP) is True
        health.reset()
        assert health.is_degraded(EP) is False


# ============ B. backoff_factor 三档手推值 ============

class TestBackoffFactor:
    def test_zero_rate_factor_one(self):
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 0, 10)
        assert health.backoff_factor(EP) == 1.0

    def test_half_rate_factor_three(self):
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 5, 5)
        assert health.backoff_factor(EP) == 3.0

    def test_full_rate_factor_four(self):
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 10, 0)
        assert health.backoff_factor(EP) == 4.0

    def test_below_min_samples_factor_one(self):
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 6, 0)
        assert health.backoff_factor(EP) == 1.0

    def test_between_half_and_full_is_three(self):
        # 9 空 1 好（空率 0.9）→ 档 3.0（非 4.0，仅全空取 4.0）
        health, _, _ = _fresh_health()
        _record_mix(health, EP, 9, 1)
        assert health.backoff_factor(EP) == 3.0


# ============ C. should_orderly_stop 生命周期 ============

class TestOrderlyStop:
    def _degraded_health(self):
        health, clock, _ = _fresh_health()
        _record_mix(health, EP, 6, 4)
        assert health.is_degraded(EP)
        return health, clock

    def test_first_call_false(self):
        health, _ = self._degraded_health()
        assert health.should_orderly_stop(EP) is False

    def test_before_duration_false(self):
        health, clock = self._degraded_health()
        clock.advance(29.9)
        assert health.should_orderly_stop(EP) is False

    def test_after_duration_true(self):
        health, clock = self._degraded_health()
        clock.advance(30.1)
        assert health.should_orderly_stop(EP) is True

    def test_within_cooldown_false(self):
        health, clock = self._degraded_health()
        clock.advance(30.1)
        assert health.should_orderly_stop(EP) is True
        # 冷却窗内立即再来 → False（不重复收口）
        assert health.should_orderly_stop(EP) is False
        clock.advance(119.9)
        assert health.should_orderly_stop(EP) is False

    def test_after_cooldown_true_again(self):
        health, clock = self._degraded_health()
        clock.advance(30.1)
        assert health.should_orderly_stop(EP) is True
        clock.advance(120.1)
        assert health.should_orderly_stop(EP) is True

    def test_recovery_resets_duration(self):
        # degraded 恢复后 degraded_since 清空：再次 degraded 重新计时
        health, clock = self._degraded_health()
        clock.advance(20.0)
        _record_mix(health, EP, 0, 20)  # 恢复
        assert health.is_degraded(EP) is False
        _record_mix(health, EP, 6, 4)  # 再次 degraded
        assert health.should_orderly_stop(EP) is False


# ============ D. maybe_throttle ============

class TestMaybeThrottle:
    def test_degraded_sleeps_configured_seconds(self):
        health, _, sleeps = _fresh_health()
        _record_mix(health, EP, 6, 4)
        asyncio.run(health.maybe_throttle(EP))
        assert sleeps == [5.0]

    def test_healthy_no_sleep(self):
        health, _, sleeps = _fresh_health()
        _record_mix(health, EP, 4, 6)
        asyncio.run(health.maybe_throttle(EP))
        assert sleeps == []

    def test_rescue_frame_exempt(self):
        from app.core.llm_endpoint_health import rescue_frame

        health, _, sleeps = _fresh_health()
        _record_mix(health, EP, 6, 4)
        with rescue_frame():
            asyncio.run(health.maybe_throttle(EP))
        assert sleeps == []
        # 退出救援帧后恢复收费
        asyncio.run(health.maybe_throttle(EP))
        assert sleeps == [5.0]


# ============ E. base.py 接入：端点键 + 统一记账 ============

from app.services.agent.agents.base import BaseAgent as _BaseAgentCls


class _ConcreteBaseAgent(_BaseAgentCls):
    """BaseAgent 的最小具体子类（run 抽象，BaseAgent 无法直接 __new__）。"""

    async def run(self, input_data):
        return None


def _bare_base_agent():
    return _ConcreteBaseAgent.__new__(_ConcreteBaseAgent)


class TestEndpointKey:
    def test_key_from_llm_service_config(self):
        agent = _bare_base_agent()
        agent.llm_service = SimpleNamespace(
            config=SimpleNamespace(base_url="http://x.example/v1")
        )
        assert agent._llm_endpoint_key() == "http://x.example/v1"

    def test_missing_base_url_uses_default_key(self):
        agent = _bare_base_agent()
        agent.llm_service = SimpleNamespace(config=SimpleNamespace(base_url=None))
        assert agent._llm_endpoint_key()  # 兜底键真值且稳定
        assert agent._llm_endpoint_key() == agent._llm_endpoint_key()


class TestRecordEndpointOutcome:
    def test_success_frame_recorded_not_empty(self):
        agent = _bare_base_agent()
        agent._last_empty_kind = None
        with patch(
            "app.services.agent.agents.base.get_endpoint_health"
        ) as get_health:
            agent._record_endpoint_outcome(EP)
        get_health.return_value.record.assert_called_once_with(EP, empty=False)

    def test_empty_frame_recorded_empty(self):
        agent = _bare_base_agent()
        agent._last_empty_kind = "other"
        with patch(
            "app.services.agent.agents.base.get_endpoint_health"
        ) as get_health:
            agent._record_endpoint_outcome(EP)
        get_health.return_value.record.assert_called_once_with(EP, empty=True)

    def test_degenerate_frame_recorded_empty(self):
        agent = _bare_base_agent()
        agent._last_empty_kind = "degenerate"
        with patch(
            "app.services.agent.agents.base.get_endpoint_health"
        ) as get_health:
            agent._record_endpoint_outcome(EP)
        get_health.return_value.record.assert_called_once_with(EP, empty=True)

    def test_no_physical_call_frame_not_recorded(self):
        # 记账修正：熔断 OPEN / critical stream error 帧无物理调用，不入窗
        agent = _bare_base_agent()
        agent._last_empty_kind = "no_physical_call"
        with patch(
            "app.services.agent.agents.base.get_endpoint_health"
        ) as get_health:
            agent._record_endpoint_outcome(EP)
        get_health.return_value.record.assert_not_called()


# ============ F. base.py stream_llm_call 端到端：throttle/退避/degenerate ============

def _agent_for_stream_call(monkeypatch, *, stream_impl=None):
    """搭一个可跑 stream_llm_call 的裸 BaseAgent（限流/熔断/重方法全桩）。"""
    import app.services.agent.agents.base as basemod
    from app.services.agent.agents.base import AgentType

    agent = _bare_base_agent()
    config = MagicMock()
    config.name = "TestAgent"
    config.system_prompt = "system"
    config.agent_type = AgentType.ANALYSIS
    agent.config = config
    captured = _Captured()
    agent.event_emitter = _FakeEmitter(captured)
    agent.trace_manager = None
    agent._agent_id = "agent_t5test"
    agent._cancelled = False
    agent._user_cancelled = False
    agent._soft_stop = False
    agent._soft_stop_consumed = False
    agent._timeout_config = {"llm_first_token_timeout": 30, "llm_stream_timeout": 60}
    agent._conversation_history = []
    agent._insights = []
    agent._work_completed = []

    service = SimpleNamespace(config=SimpleNamespace(base_url=EP))
    service.chat_completion_stream = stream_impl
    agent.llm_service = service

    # 任务限流器桩
    agent._get_llm_rate_limiter = lambda: MagicMock(acquire=AsyncMock())

    # 熔断器直通
    class _FakeCircuit:
        async def call(self, fn):
            return await fn()

    monkeypatch.setattr(basemod, "get_llm_circuit", lambda: _FakeCircuit())

    # trace 写点静默
    agent._trace_llm_call = MagicMock()
    return agent, captured


def _done_stream(content="Final Answer: 审计完成", usage=None):
    usage = usage or {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }

    async def _stream(**kwargs):
        yield {"type": "done", "content": content, "usage": usage}

    return _stream


class TestThrottleWiredInStreamCall:
    def test_throttle_charged_before_llm_call(self, monkeypatch):
        from app.core.llm_endpoint_health import (
            get_endpoint_health,
            reset_endpoint_health,
        )

        agent, _ = _agent_for_stream_call(monkeypatch, stream_impl=_done_stream())
        reset_endpoint_health()
        health = get_endpoint_health()
        sleeps: list[float] = []

        async def _fake_sleep(sec):
            sleeps.append(sec)

        health._sleep = _fake_sleep
        _record_mix(health, EP, 6, 4)  # degraded

        out, _tokens = asyncio.run(
            agent.stream_llm_call(
                [{"role": "user", "content": "hi"}],
                auto_compress=False,
                max_tokens=100,
            )
        )
        # degraded → 限流前实际收费 5s
        assert sleeps == [5.0]
        assert "审计完成" in out

    def test_rescue_call_not_throttled(self, monkeypatch):
        from app.core.llm_endpoint_health import (
            get_endpoint_health,
            reset_endpoint_health,
            rescue_frame,
        )

        agent, _ = _agent_for_stream_call(monkeypatch, stream_impl=_done_stream())
        reset_endpoint_health()
        health = get_endpoint_health()
        sleeps: list[float] = []

        async def _fake_sleep(sec):
            sleeps.append(sec)

        health._sleep = _fake_sleep
        _record_mix(health, EP, 6, 4)

        # 生产救援路径：rescue_frame 包住 _last_ditch=True 的嵌套调用
        async def _run_rescue():
            with rescue_frame():
                return await agent.stream_llm_call(
                    [{"role": "user", "content": "hi"}],
                    auto_compress=False,
                    max_tokens=100,
                    _last_ditch=True,
                )

        out, _ = asyncio.run(_run_rescue())
        assert sleeps == []
        assert "审计完成" in out


class TestDegenerateBreakVisibility:
    def test_warning_emitted_with_bengkwai_keyword(self, monkeypatch):
        async def _one_token_stream(**kwargs):
            yield {"type": "token", "content": "AAAA", "accumulated": "AAAA"}

        agent, captured = _agent_for_stream_call(
            monkeypatch, stream_impl=_one_token_stream
        )
        # 让崩坏检测命中（真实字符墙构造较长，桩精确控制）
        agent._detect_output_degeneracy = MagicMock(return_value=True)

        asyncio.run(
            agent.stream_llm_call(
                [{"role": "user", "content": "hi"}],
                auto_compress=False,
                max_tokens=100,
            )
        )
        warnings = [msg for typ, msg in captured.events if typ == "warning"]
        hit = [m for m in warnings if "崩坏" in m]
        assert len(hit) == 1
        assert "空响应" not in hit[0]
        assert "静默" not in hit[0]

    def test_degenerate_break_sets_orderly_flag_for_orchestrator(self, monkeypatch):
        from app.services.agent.agents.base import AgentType

        async def _one_token_stream(**kwargs):
            yield {"type": "token", "content": "AAAA", "accumulated": "AAAA"}

        agent, _ = _agent_for_stream_call(
            monkeypatch, stream_impl=_one_token_stream
        )
        agent._detect_output_degeneracy = MagicMock(return_value=True)

        # analysis 轮：不置收口标志
        asyncio.run(
            agent.stream_llm_call(
                [{"role": "user", "content": "hi"}],
                auto_compress=False,
                max_tokens=100,
            )
        )
        assert getattr(agent, "_orderly_stopped", False) is False

        # orchestrator 轮：同一掐断点置收口标志
        agent.config.agent_type = AgentType.ORCHESTRATOR
        agent._detect_output_degeneracy = MagicMock(return_value=True)
        asyncio.run(
            agent.stream_llm_call(
                [{"role": "user", "content": "hi"}],
                auto_compress=False,
                max_tokens=100,
            )
        )
        assert agent._orderly_stopped is True


class TestRescueBackoffMultiplied:
    def test_backoff_multiplied_by_factor(self, monkeypatch):
        """degraded 空率 0.5 时救援退避 15s × 3.0 = 45s、30s × 3.0 = 90s。

        外层走真实 stream_llm_call（空帧），救援嵌套调用桩为空成功，
        捕获 asyncio.sleep 验证放大后的退避时长。
        """
        sleep_calls: list[float] = []

        async def _capturing_sleep(sec):
            sleep_calls.append(sec)

        monkeypatch.setattr(asyncio, "sleep", _capturing_sleep)

        from app.core.llm_endpoint_health import (
            get_endpoint_health,
            reset_endpoint_health,
        )
        from app.services.agent.agents.base import BaseAgent

        # 外层空响应：done chunk 给空正文
        async def _outer_stream(**kwargs):
            yield {
                "type": "done",
                "content": "",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }

        agent, _ = _agent_for_stream_call(monkeypatch, stream_impl=_outer_stream)

        reset_endpoint_health()
        health = get_endpoint_health()

        async def _no_throttle_sleep(sec):
            return None

        health._sleep = _no_throttle_sleep
        _record_mix(health, EP, 5, 5)  # 空率 0.5 → factor 3.0

        real_stream = BaseAgent.stream_llm_call

        async def _smart_dispatch(messages, **kw):
            # 救援嵌套调用：一律空帧（rescue frame 包裹与生产一致）
            if kw.get("_last_ditch"):
                return "", 0
            return await real_stream(agent, messages, **kw)

        agent.stream_llm_call = _smart_dispatch

        asyncio.run(real_stream(
            agent,
            [{"role": "user", "content": "hi"}],
            auto_compress=False,
            max_tokens=100,
        ))
        # 15×3=45 与 30×3=90 两档退避实际 sleep（0 档无 sleep）
        assert sleep_calls.count(45) == 1
        assert sleep_calls.count(90) == 1

    def test_no_factor_when_healthy(self, monkeypatch):
        """健康端点 factor 1.0：退避仍为 15s/30s。"""
        sleep_calls: list[float] = []

        async def _capturing_sleep(sec):
            sleep_calls.append(sec)

        monkeypatch.setattr(asyncio, "sleep", _capturing_sleep)

        from app.core.llm_endpoint_health import (
            get_endpoint_health,
            reset_endpoint_health,
        )
        from app.services.agent.agents.base import BaseAgent

        async def _outer_stream(**kwargs):
            yield {
                "type": "done",
                "content": "",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }

        agent, _ = _agent_for_stream_call(monkeypatch, stream_impl=_outer_stream)
        reset_endpoint_health()
        health = get_endpoint_health()

        async def _no_throttle_sleep(sec):
            return None

        health._sleep = _no_throttle_sleep

        real_stream = BaseAgent.stream_llm_call

        async def _smart_dispatch(messages, **kw):
            if kw.get("_last_ditch"):
                return "", 0
            return await real_stream(agent, messages, **kw)

        agent.stream_llm_call = _smart_dispatch

        asyncio.run(real_stream(
            agent,
            [{"role": "user", "content": "hi"}],
            auto_compress=False,
            max_tokens=100,
        ))
        assert sleep_calls.count(15) == 1
        assert sleep_calls.count(30) == 1


# ============ G. _compute_llm_health：garbled_drops 归账 ============

class TestComputeHealthGarbled:
    def test_bengkwai_warning_goes_garbled_only(self):
        from app.api.v1.endpoints.agent_tasks import _compute_llm_health

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [
            ("thinking_start", "开始思考"),
            (
                "warning",
                "LLM 输出崩坏（charwall/loop），本轮内容丢弃并重试",
            ),
        ]
        db.execute.return_value = result_mock
        health = asyncio.run(_compute_llm_health(db, "t1"))
        assert health["garbled_drops"] == 1
        assert health["empty_responses"] == 0

    def test_empty_response_still_counted_separately(self):
        from app.api.v1.endpoints.agent_tasks import _compute_llm_health

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [
            ("thinking_start", "开始思考"),
            (
                "warning",
                "检测到空响应，自动以关思考+预算减半重试（救援 1/3）",
            ),
            (
                "warning",
                "LLM 输出崩坏（charwall/loop），本轮内容丢弃并重试",
            ),
        ]
        db.execute.return_value = result_mock
        health = asyncio.run(_compute_llm_health(db, "t1"))
        assert health["garbled_drops"] == 1
        assert health["empty_responses"] == 1


# ============ H. tokens_used：content_end / llm_complete ============

class TestEventTokensUsed:
    def _agent(self):
        agent = _bare_base_agent()
        config = MagicMock()
        config.name = "X"
        agent.config = config
        agent.event_emitter = _SpyEmitter()
        return agent

    def test_content_end_writes_round_usage(self):
        agent = self._agent()
        agent._last_empty_kind = None
        agent._last_round_usage = {"prompt_tokens": 100, "completion_tokens": 50}
        asyncio.run(agent.emit_content_end("正文全文"))
        data = agent.event_emitter.items[-1]
        assert data.event_type == "content_end"
        assert data.tokens_used == 150

    def test_content_end_degenerate_round_zero(self):
        agent = self._agent()
        agent._last_empty_kind = "degenerate"
        agent._last_round_usage = {"prompt_tokens": 999, "completion_tokens": 999}
        asyncio.run(agent.emit_content_end("部分正文"))
        assert agent.event_emitter.items[-1].tokens_used == 0

    def test_content_end_no_usage_zero(self):
        agent = self._agent()
        agent._last_empty_kind = None
        agent._last_round_usage = None
        asyncio.run(agent.emit_content_end("正文"))
        assert agent.event_emitter.items[-1].tokens_used == 0

    def test_llm_complete_field_round_usage_message_keeps_total(self):
        agent = self._agent()
        agent._last_empty_kind = None
        agent._last_round_usage = {"prompt_tokens": 80, "completion_tokens": 40}
        asyncio.run(agent.emit_llm_complete("分析完成", tokens_used=9999))
        data = agent.event_emitter.items[-1]
        # 事件列 = 当轮 usage；消息/metadata 保留累计口径
        assert data.tokens_used == 120
        assert "9999" in data.message
        assert data.metadata["tokens_used"] == 9999


# ============ I. verification N 进 N 出 ============

def _bare_verification_agent(max_iterations, captured=None):
    """构造不走 __init__ 的 VerificationAgent（同 test_p9 风格）。"""
    from app.services.agent.agents.verification import VerificationAgent

    agent = VerificationAgent.__new__(VerificationAgent)
    config = MagicMock()
    config.name = "Verification"
    config.max_iterations = max_iterations
    config.system_prompt = "system prompt"
    agent.config = config
    agent.tools = {}
    agent.event_emitter = _FakeEmitter(captured) if captured is not None else None
    agent.parent_id = None
    agent.task_id = "t-p9t5"
    agent.trace_manager = None
    agent._agent_id = "agent_p9t5"
    agent._state = MagicMock()
    agent._iteration = 0
    agent._total_tokens = 0
    agent._tool_calls = 0
    agent._cancelled = False
    agent._user_cancelled = False
    agent._cancel_callback = None
    agent._soft_stop = False
    agent._soft_stop_consumed = False
    agent._incoming_handoff = None
    agent._insights = []
    agent._work_completed = []
    agent._sub_format_retry = 0
    agent._gate_observations = []
    agent._timeout_config = {"tool_timeout": 60}
    agent._conversation_history = []
    agent.llm_service = MagicMock()
    agent.llm_service.backend_capabilities = None
    return agent


def _patch_verification_heavy(agent):
    agent._build_sandbox_commands = MagicMock(return_value=[])
    agent._prepare_sandbox_files = MagicMock(return_value="")
    agent._run_deterministic_sandbox_commands = AsyncMock()
    agent._finalize_findings_without_final_answer = MagicMock(
        side_effect=lambda findings: list(findings)
    )
    agent._backfill_original_metadata = MagicMock()
    agent._attach_runtime_sandbox_attempts = MagicMock()
    agent._bind_runtime_evidence_to_all = MagicMock()
    agent._bind_unbound_runtime_evidence = MagicMock()
    agent._trace_verification_results = MagicMock()
    agent._create_verification_handoff = MagicMock(return_value=None)


class TestNInNOut:
    def _run_with(self, n_input, n_report):
        captured = _Captured()
        agent = _bare_verification_agent(20, captured)
        _patch_verification_heavy(agent)

        inputs = [
            {
                "file_path": f"src/f{i}.py",
                "vulnerability_type": "sql_injection",
                "title": f"finding-{i}",
                "severity": "high",
            }
            for i in range(n_input)
        ]
        report = [
            {
                "file_path": f"src/f{i}.py",
                "vulnerability_type": "sql_injection",
                "title": f"finding-{i}",
                "verdict": "confirmed",
            }
            for i in range(n_report)
        ]
        text = (
            "Thought: 验证完成\nFinal Answer: "
            + json.dumps({"findings": report}, ensure_ascii=False)
        )
        agent.stream_llm_call = AsyncMock(return_value=(text, 10))
        result = asyncio.run(
            agent.run({"previous_results": {"findings": inputs}})
        )
        return agent, result

    def test_missing_conclusions_marked(self):
        _agent, result = self._run_with(7, 6)
        assert result.success is True
        assert result.metadata.get("missing_conclusions") == 1
        assert result.metadata.get("input_findings") == 7
        assert result.metadata.get("output_findings") == 6

    def test_equal_in_out_not_marked(self):
        _agent, result = self._run_with(7, 7)
        assert result.success is True
        assert result.metadata.get("missing_conclusions") is None


# ============ J. Orchestrator：breaker 收口后无新 dispatch ============

class TestOrchestratorBreakerStopsDispatch:
    def _make_orch(self, monkeypatch):
        from app.services.agent.agents.orchestrator import OrchestratorAgent

        service = SimpleNamespace(
            backend_capabilities=None,
            config=SimpleNamespace(base_url=EP),
        )
        emitter = _SpyEmitter()
        agent = OrchestratorAgent(
            llm_service=service, tools={}, event_emitter=emitter
        )

        monkeypatch.setattr(agent, "_register_to_registry", lambda task=None: None)
        monkeypatch.setattr(
            agent,
            "_run_semgrep_prescan",
            AsyncMock(
                return_value={
                    "findings": [],
                    "hot_files": [],
                    "scan_success": False,
                }
            ),
        )
        monkeypatch.setattr(agent, "_maybe_pause", AsyncMock())
        monkeypatch.setattr(agent, "emit_thinking", AsyncMock())
        monkeypatch.setattr(agent, "check_messages", lambda: [])
        monkeypatch.setattr(
            agent, "_check_token_budget_exceeded", lambda: False
        )

        dispatch_mock = AsyncMock(return_value="dispatch-observation")
        monkeypatch.setattr(agent, "_dispatch_agent", dispatch_mock)
        summarize_mock = MagicMock(return_value="summary-observation")
        monkeypatch.setattr(agent, "_summarize_findings", summarize_mock)
        return agent, dispatch_mock

    def test_breaker_trip_breaks_without_dispatch(self, monkeypatch):
        from app.core.llm_endpoint_health import get_endpoint_health

        agent, dispatch_mock = self._make_orch(monkeypatch)
        health = get_endpoint_health()
        monkeypatch.setattr(
            health, "should_orderly_stop", lambda endpoint: True
        )

        result = asyncio.run(agent.run({"project_info": {}, "config": {}}))

        # 复用收口流：success + 证据保留；breaker 收口后零新 dispatch
        assert result.success is True
        assert dispatch_mock.call_count == 0
        gates = [
            o
            for o in agent._gate_observations
            if o.get("gate") == "llm_endpoint_circuit"
        ]
        assert len(gates) == 1
        assert agent._orderly_stopped is True

    def test_breaker_not_trip_loop_continues(self, monkeypatch):
        from app.core.llm_endpoint_health import get_endpoint_health

        agent, dispatch_mock = self._make_orch(monkeypatch)
        health = get_endpoint_health()
        monkeypatch.setattr(
            health, "should_orderly_stop", lambda endpoint: False
        )
        # 第一轮即 finish，避免循环跑满
        finish_text = "Thought: 完成\nAction: finish\n"
        agent.stream_llm_call = AsyncMock(return_value=(finish_text, 7))

        asyncio.run(agent.run({"project_info": {}, "config": {}}))
        # 未收口：标志不置位
        assert agent._orderly_stopped is False


# ============ K. D5：LLM_GLOBAL_CONCURRENCY 默认 4 ============

def test_llm_global_concurrency_default_is_4():
    from app.core.config import Settings

    default = Settings.model_fields["LLM_GLOBAL_CONCURRENCY"].default
    assert default == 4
