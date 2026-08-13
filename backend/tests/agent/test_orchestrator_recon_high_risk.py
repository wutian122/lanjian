from app.services.agent.agents.orchestrator import OrchestratorAgent


def test_recon_high_risk_areas_are_context_not_findings():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    assert agent._convert_recon_high_risk_area_to_finding(
        "openhands/server/app.py - FastAPI 应用定义，包含所有路由和中间件"
    ) is None
    assert agent._convert_recon_high_risk_area_to_finding(
        "openhands/app_server/secrets/ - 密钥管理相关代码"
    ) is None
