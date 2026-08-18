"""Integration tests: circuit breaker + rate limiter wired into stream_llm_call."""
import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.services.agent.agents.recon import ReconAgent
from app.services.agent.core.circuit_breaker import get_llm_circuit, CircuitState, CircuitStats
from app.services.agent.core.rate_limiter import get_llm_rate_limiter


def _make_emitter():
    e = MagicMock()
    e.emit = AsyncMock()
    return e


def _make_agent(stream_fn):
    service = MagicMock()
    service.get_agent_timeout_config = MagicMock(return_value={
        "llm_first_token_timeout": 30,
        "llm_stream_timeout": 60,
        "agent_timeout": 1800,
        "sub_agent_timeout": 600,
        "tool_timeout": 60,
    })
    service.chat_completion_stream = stream_fn
    return ReconAgent(llm_service=service, tools={}, event_emitter=_make_emitter())


def _reset_resilience():
    c = get_llm_circuit()
    c._state = CircuitState.CLOSED
    c._stats = CircuitStats()
    c._half_open_calls = 0
    c._last_state_change = time.time()
    lim = get_llm_rate_limiter()
    lim.tokens = float(lim.burst)
    lim.last_update = time.monotonic()


@pytest.fixture(autouse=True)
def _reset():
    _reset_resilience()
    yield
    _reset_resilience()


@pytest.mark.asyncio
async def test_rate_limiter_consumes_token_on_call():
    limiter = get_llm_rate_limiter()
    before = limiter.available_tokens

    async def _ok(messages=None, temperature=None, max_tokens=None):
        yield {"type": "done", "content": "ok", "usage": {"total_tokens": 10}}

    agent = _make_agent(_ok)
    text, _ = await agent.stream_llm_call([{"role": "user", "content": "hi"}])

    after = limiter.available_tokens
    assert before - after >= 0.99, f"token not consumed: before={before} after={after}"


@pytest.mark.asyncio
async def test_circuit_records_success_on_normal_call():
    circuit = get_llm_circuit()
    assert circuit.stats.successful_calls == 0

    async def _ok(messages=None, temperature=None, max_tokens=None):
        yield {"type": "done", "content": "ok", "usage": {"total_tokens": 5}}

    agent = _make_agent(_ok)
    await agent.stream_llm_call([{"role": "user", "content": "hi"}])

    assert circuit.stats.successful_calls == 1
    assert circuit.is_closed


@pytest.mark.asyncio
async def test_circuit_records_failure_on_critical_error():
    circuit = get_llm_circuit()

    async def _fail(messages=None, temperature=None, max_tokens=None):
        yield {"type": "error", "error_type": "connection", "error": "refused",
               "user_message": "conn failed", "accumulated": ""}

    agent = _make_agent(_fail)
    text, _ = await agent.stream_llm_call([{"role": "user", "content": "hi"}])

    assert circuit.stats.failed_calls == 1
    assert "[API_ERROR:connection]" in text


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_and_rejects():
    circuit = get_llm_circuit()
    threshold = circuit.config.failure_threshold

    async def _fail(messages=None, temperature=None, max_tokens=None):
        yield {"type": "error", "error_type": "connection", "error": "refused",
               "user_message": "conn failed", "accumulated": ""}

    agent = _make_agent(_fail)
    for _ in range(threshold):
        await agent.stream_llm_call([{"role": "user", "content": "hi"}])

    assert circuit.is_open

    text, _ = await agent.stream_llm_call([{"role": "user", "content": "hi"}])
    assert "circuit_open" in text
    assert circuit.stats.rejected_calls >= 1
