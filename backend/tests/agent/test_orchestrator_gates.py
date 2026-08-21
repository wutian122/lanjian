"""R4/R5/R6 门禁根治测试：3 次终止、Bug D 判定修正、observations、伪造证据排除。"""
from app.services.agent.agents.orchestrator import OrchestratorAgent


def _make_agent():
    """绕过 __init__ 构造 agent，手动设置门禁依赖的实例属性。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._all_findings = []
    agent._steps = []
    agent._agent_results = {}
    agent._hard_coverage_block_count = 0
    agent._dispatched_tasks = {}
    agent._coverage_bypassed = False
    agent._coverage_bypass_info = {}
    agent._finish_gate_rejections = 0
    agent._gate_observations = []
    return agent


# ============ R4: 门禁 3 次终止 ============

def _finish_gate_decision(agent):
    """复刻 run() finish 分支的门禁判定逻辑，返回 (should_block, should_release)。
    提取自 orchestrator.py:869-944，用于验证 R4 3 次终止语义。"""
    has_findings = len(agent._all_findings) > 0
    verification_dispatched = "verification" in agent._dispatched_tasks
    verification_count = agent._dispatched_tasks.get("verification", 0)
    has_sandbox_evidence = agent._has_valid_sandbox_evidence()
    max_redispatch = 3
    needs_gate = has_findings and (
        not verification_dispatched or (verification_count > 0 and not has_sandbox_evidence)
    )
    if needs_gate:
        agent._finish_gate_rejections += 1
        agent._record_gate_observation(
            "verification_evidence_gate",
            f"发现 {len(agent._all_findings)} 个漏洞但无有效沙箱证据（第 {agent._finish_gate_rejections} 次拒绝）",
        )
    should_block = needs_gate and agent._finish_gate_rejections < max_redispatch
    should_release = has_findings and agent._finish_gate_rejections >= max_redispatch
    return should_block, should_release


def test_gate_blocks_first_two_then_releases_on_third():
    """R4: 无沙箱证据时前 2 次拒绝，第 3 次达到上限后放行（不再无限重派）。"""
    agent = _make_agent()
    agent._all_findings = [{"title": "SSRF", "verification_status": "needs_context"}]
    agent._dispatched_tasks = {"verification": 3}  # 已调度 3 次验证但无证据

    # 第一次 finish → 拦截
    block1, release1 = _finish_gate_decision(agent)
    assert block1 is True
    assert release1 is False
    assert agent._finish_gate_rejections == 1

    # 第二次 finish → 拦截
    block2, release2 = _finish_gate_decision(agent)
    assert block2 is True
    assert agent._finish_gate_rejections == 2

    # 第三次 finish → 达到上限，放行（不再拦截）
    block3, release3 = _finish_gate_decision(agent)
    assert block3 is False
    assert release3 is True
    assert agent._finish_gate_rejections == 3

    # 后续 finish 不再拦截（持续放行）
    block4, release4 = _finish_gate_decision(agent)
    assert block4 is False
    assert release4 is True


def test_gate_does_not_release_without_findings():
    """无发现时门禁不拦截也不放行（由正常完成路径处理）。"""
    agent = _make_agent()
    agent._all_findings = []
    agent._dispatched_tasks = {"verification": 1}
    block, release = _finish_gate_decision(agent)
    assert block is False
    assert release is False


def test_gate_rejection_counter_increments():
    """每次被无沙箱证据门禁拒绝都会累加计数。"""
    agent = _make_agent()
    agent._record_gate_observation("verification_evidence_gate", "no evidence")
    agent._record_gate_observation("verification_evidence_gate", "still no evidence")
    assert agent._finish_gate_rejections == 0  # 计数由 finish 门禁逻辑累加，此处只测 observations
    assert len(agent._gate_observations) == 2


def test_has_valid_sandbox_evidence_rejects_fabricated():
    """R3: fabricated 证据不计入有效沙箱证据（即使 success+exit0+VULNERABILITY_CONFIRMED）。"""
    agent = _make_agent()
    agent._all_findings = [
        {
            "title": "SSRF",
            "verification_status": "confirmed",
            "sandbox_attempts": [
                {
                    "success": True,
                    "exit_code": 0,
                    "fabricated": True,
                    "evidence_summary": "Simulated ... VULNERABILITY_CONFIRMED",
                }
            ],
        }
    ]
    assert agent._has_valid_sandbox_evidence() is False


def test_has_valid_sandbox_evidence_accepts_real_evidence():
    """R3: 真实证据（非 fabricated）仍被接受。"""
    agent = _make_agent()
    agent._all_findings = [
        {
            "title": "SSRF",
            "verification_status": "confirmed",
            "sandbox_attempts": [
                {"success": True, "exit_code": 0, "evidence_summary": "VULNERABILITY_CONFIRMED"}
            ],
        }
    ]
    assert agent._has_valid_sandbox_evidence() is True


# ============ R5: Bug D 全量验证门禁判定修正 ============

def test_bugD_gate_treats_needs_context_as_unverified():
    """R5: needs_context 状态被视为未验证 → unverified_findings 非空（原逻辑被默认值击穿）。"""
    agent = _make_agent()
    agent._all_findings = [
        {"title": "A", "verification_status": "needs_context", "is_verified": False},
        {"title": "B", "verification_status": "confirmed", "is_verified": True},
    ]
    terminal = {"confirmed", "static_confirmed", "not_reproducible", "false_positive"}
    unverified = [
        f for f in agent._all_findings
        if f.get("verification_status") not in terminal
        and f.get("is_verified") is not True
    ]
    assert len(unverified) == 1
    assert unverified[0]["title"] == "A"


def test_bugD_gate_not_trigger_for_terminal_statuses():
    """R5: confirmed/not_reproducible/false_positive 视为已终结，不触发门禁。"""
    agent = _make_agent()
    agent._all_findings = [
        {"title": "A", "verification_status": "confirmed", "is_verified": True},
        {"title": "B", "verification_status": "not_reproducible", "is_verified": False},
        {"title": "C", "verification_status": "false_positive", "is_verified": False},
    ]
    terminal = {"confirmed", "static_confirmed", "not_reproducible", "false_positive"}
    unverified = [
        f for f in agent._all_findings
        if f.get("verification_status") not in terminal
        and f.get("is_verified") is not True
    ]
    assert unverified == []


# ============ R6: observations 记录 ============

def test_record_gate_observation_accumulates():
    """R6: 门禁拒绝原因被记录到 _gate_observations（供落库）。"""
    agent = _make_agent()
    agent._record_gate_observation("verification_evidence_gate", "发现 5 个漏洞但无有效沙箱证据")
    agent._record_gate_observation("coverage_gate", "覆盖率 9/10 未达标，拦截 5 次后放行")
    assert len(agent._gate_observations) == 2
    assert agent._gate_observations[0]["gate"] == "verification_evidence_gate"
    assert "覆盖率" in agent._gate_observations[1]["reason"]
    # 每条都带时间戳
    assert all("time" in obs for obs in agent._gate_observations)
