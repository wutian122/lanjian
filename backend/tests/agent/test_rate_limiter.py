from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.agent.core import rate_limiter as module


def test_get_llm_rate_limiter_reconfigures_existing_limiter(monkeypatch):
    module._global_registry = None

    configs = iter([
        SimpleNamespace(llm_rate_per_minute=60),
        SimpleNamespace(llm_rate_per_minute=120),
    ])

    monkeypatch.setattr(
        "app.services.agent.config.get_agent_config",
        lambda: next(configs),
    )

    limiter = module.get_llm_rate_limiter()
    assert limiter.rate == 1.0
    assert limiter.burst == 5

    limiter.tokens = 9.0
    same_limiter = module.get_llm_rate_limiter()

    assert same_limiter is limiter
    assert same_limiter.rate == 2.0
    assert same_limiter.burst == 5
    assert same_limiter.tokens == 5.0
