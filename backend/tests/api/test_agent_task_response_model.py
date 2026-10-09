"""AgentTaskResponse 契约测试（2026-09-29 审查 C1/C2 修复）。

C1：层 5a 让 0 finding + completed_with_gaps 的 quality_score 返回 None，
响应模型若仍声明不可空 float，FastAPI response_model 校验会对详情/列表接口
直接抛 ResponseValidationError（HTTP 500）——恰命中修复目标场景。
C2：detail 下发的 observations 若未在响应模型声明，会被 FastAPI 静默过滤，
前端健康度横幅整条链路失效（审查实测 "has observations key: False"）。

契约：
- quality_score=None 可通过 AgentTaskResponse 校验（序列化保留 null）；
- observations 字段存在且可序列化（llm_health / candidates 明细可下发）；
- security_score=None 同样合法（既有行为，一并锁死）。
"""
import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.agent_tasks import AgentTaskResponse


def _base_payload(**overrides):
    payload = {
        "id": "t-1",
        "project_id": "p-1",
        "name": "任务",
        "description": None,
        "status": "completed_with_gaps",
        "current_phase": "reporting",
        "created_at": "2026-09-29T00:00:00Z",
    }
    payload.update(overrides)
    return payload


class TestAgentTaskResponseContract:
    def test_quality_score_none_accepted(self):
        model = AgentTaskResponse(**_base_payload(quality_score=None))
        assert model.quality_score is None

    def test_security_score_none_accepted(self):
        model = AgentTaskResponse(**_base_payload(security_score=None))
        assert model.security_score is None

    def test_quality_score_none_serializes_as_null(self):
        model = AgentTaskResponse(**_base_payload(quality_score=None))
        dumped = model.model_dump(mode="json")
        assert dumped["quality_score"] is None

    def test_observations_field_declared_and_serialized(self):
        obs = [{"llm_health": {"llm_calls": 10, "truncations": 6,
                               "empty_responses": 0, "format_retries": 2,
                               "garbled_drops": 1, "degraded": True},
                "time": "2026-09-29T00:00:00Z"}]
        model = AgentTaskResponse(**_base_payload(observations=obs))
        dumped = model.model_dump(mode="json")
        assert dumped["observations"] == obs

    def test_observations_defaults_to_none(self):
        model = AgentTaskResponse(**_base_payload())
        assert model.observations is None

    def test_existing_valid_payload_still_accepted(self):
        model = AgentTaskResponse(**_base_payload(quality_score=87.5))
        assert model.quality_score == 87.5
