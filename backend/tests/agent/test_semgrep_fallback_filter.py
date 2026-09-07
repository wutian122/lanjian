"""F1（Task 11 follow-up）：Semgrep 兜底落库前的可验证类型过滤。

生产实证（Task 19 端到端）：40 条 semgrep_fallback 候选全是配置类
（.github/actions/Dockerfile，vulnerability_type=other），无代码 sink，
确定性 PoC 只输出 NO_SINK——白占沙箱验证预算、拖垮 Verification LLM 循环。
兜底落库前只保留"有确定性 PoC 模板"的类型（VERIFIABLE_SEMGREP_TYPES，
与 verification._gen_sandbox_command 专用模板集合一致）且 severity ≥ medium；
被滤候选不进 _all_findings/验证队列/报告，仅记 semgrep_fallback_filtered
observation 供可观测。全滤时 _all_findings 为空，维持 Task 11"无兜底产出"
收口语义（completed_with_gaps 照常），不引入新行为分支。
"""
import pytest

from app.services.agent.agents.orchestrator import (
    VERIFIABLE_SEMGREP_TYPES,
    OrchestratorAgent,
)


def _make_orch():
    """绕过 __init__ 构造 agent，手动设置兜底逻辑依赖的实例属性。"""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent._all_findings = []
    agent._semgrep_findings = []
    agent._semgrep_fallback_applied = False
    agent._gate_observations = []
    agent._runtime_context = {}
    return agent


def _semgrep_finding(
    path="app/db.py",
    line=42,
    rule="p.sql-injection",
    message="SQL injection",
    vuln_type="sql_injection",
    severity="high",
):
    """构造一条 _semgrep_findings 格式记录（与 _run_semgrep_prescan 输出一致）。"""
    return {
        "title": rule,  # 预扫映射时 check_id 写入 title
        "file_path": path,
        "line_start": line,
        "line_end": line,
        "severity": severity,
        "description": message,
        "vulnerability_type": vuln_type,
        "code_snippet": "cursor.execute(query)",
        "source": "semgrep",
        "verification_method": "semgrep_static_analysis",
        "is_verified": False,
    }


def _filtered_obs(agent):
    return [o for o in agent._gate_observations if o.get("gate") == "semgrep_fallback_filtered"]


def _fallback_obs(agent):
    return [o for o in agent._gate_observations if o.get("gate") == "semgrep_fallback"]


# ============ ① 可验证类型落库 ============

@pytest.mark.asyncio
async def test_verifiable_type_persists_as_fallback_candidate():
    """sql_injection 候选正常落库：source=semgrep_fallback、进 _all_findings
    与可验证产出口径，且不记过滤 observation。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path="app/db.py", vuln_type="sql_injection", severity="high"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert len(agent._all_findings) == 1
    f = agent._all_findings[0]
    assert f["source"] == "semgrep_fallback"
    assert f["vulnerability_type"] == "sql_injection"
    assert f["needs_verification"] is True
    assert len(agent._actionable_findings()) == 1
    assert len(_fallback_obs(agent)) == 1
    assert _filtered_obs(agent) == []


@pytest.mark.asyncio
async def test_prescan_injection_alias_canonicalizes_to_sql_injection():
    """生产预扫真实形态：_map_semgrep_to_vuln_type 对 SQL 类 check_id 返回
    泛化 "injection"（模板表无此键，会落入 default 空转）——兜底落库前
    规范化为 "sql_injection"，使确定性 SQL PoC 模板能正确分派。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="app/db.py",
            rule="python.django.security.injection.sql.sql-injection",
            vuln_type="injection",  # _run_semgrep_prescan 实际写入值
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert agent._all_findings[0]["vulnerability_type"] == "sql_injection"
    assert _filtered_obs(agent) == []


@pytest.mark.parametrize("vuln_type", sorted(VERIFIABLE_SEMGREP_TYPES))
@pytest.mark.asyncio
async def test_each_verifiable_type_persists(vuln_type):
    """10 类有专用 PoC 模板的类型全部保留落库。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path=f"app/{vuln_type}.py", vuln_type=vuln_type, severity="medium"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert agent._all_findings[0]["vulnerability_type"] == vuln_type


# ============ ② 配置类/非可验证类型过滤 ============

@pytest.mark.asyncio
async def test_config_other_type_filtered_with_observation():
    """配置类候选（.github/workflows，vulnerability_type=other）不进
    _all_findings、不送沙箱；记 semgrep_fallback_filtered observation。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path=".github/workflows/ci.yml",
            rule="yaml.github-actions.security.pull-request-target",
            vuln_type="other",
            severity="medium",
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert agent._all_findings == []
    assert agent._actionable_findings() == []
    assert _fallback_obs(agent) == []
    filtered = _filtered_obs(agent)
    assert len(filtered) == 1
    assert "1 条" in filtered[0]["reason"]
    assert "other" in filtered[0]["reason"]


@pytest.mark.parametrize("vuln_type", ["other", "weak_crypto", "xxe", "auth_bypass", "open_redirect", "csrf", "unknown_type"])
@pytest.mark.asyncio
async def test_non_verifiable_types_filtered(vuln_type):
    """走 default 通用模板（无确定性确认标记）的类型一律过滤。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path=f"app/{vuln_type}.py", vuln_type=vuln_type, severity="high"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert agent._all_findings == []
    assert len(_filtered_obs(agent)) == 1


# ============ ③ 低严重度过滤 ============

@pytest.mark.parametrize("severity", ["low", "info"])
@pytest.mark.asyncio
async def test_low_severity_verifiable_type_filtered(severity):
    """类型可验证但 severity < medium（INFO/LOW）同样不送沙箱。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path=f"app/{severity}.py", vuln_type="sql_injection", severity=severity),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert agent._all_findings == []
    filtered = _filtered_obs(agent)
    assert len(filtered) == 1
    assert severity in filtered[0]["reason"]


# ============ ④ 全滤：维持"无兜底产出"现状语义 ============

@pytest.mark.asyncio
async def test_all_filtered_keeps_no_fallback_semantics():
    """候选全部被滤 → added=0、_all_findings 空、无 semgrep_fallback
    observation——Task 11 completed_with_gaps 收口路径不受影响。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path="Dockerfile", rule="p.dockerfile", vuln_type="other", severity="high"),
        _semgrep_finding(path="app/crypto.py", rule="p.weak-crypto", vuln_type="weak_crypto", severity="medium"),
        _semgrep_finding(path="app/note.py", rule="p.info", vuln_type="xss", severity="low"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert agent._all_findings == []
    assert agent._actionable_findings() == []
    assert _fallback_obs(agent) == []
    filtered = _filtered_obs(agent)
    assert len(filtered) == 1
    assert "3 条" in filtered[0]["reason"]
    # 幂等标志仍置位（兜底执行过，只是 0 条可落库）
    assert agent._semgrep_fallback_applied is True
    # 重复调用不重复记录
    await agent._apply_semgrep_fallback()
    assert len(_filtered_obs(agent)) == 1


# ============ 别名二次分流：injection → sql/command ============

@pytest.mark.asyncio
async def test_command_injection_check_id_routes_to_command_template():
    """命令注入规则 id（含 'injection' 被 _map 截胡为泛化 'injection'）兜底
    落库类型必须是 command_injection（不是 sql_injection）——否则误走 SQL
    模板（sink 硬编码 execute/raw/query，subprocess/os.system 0 命中）
    退化为 NO_SINK 假阴。"""
    from app.services.agent.agents.verification import VerificationAgent

    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="app/run.py",
            rule="python.lang.security.audit.os-command-injection",
            message="OS command injection via subprocess",
            vuln_type=None,  # 走 _map_semgrep_to_vuln_type → "injection"
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    vtype = agent._all_findings[0]["vulnerability_type"]
    assert vtype == "command_injection", f"命令注入误标为 {vtype!r}"

    # 模板分派：command_injection 专用模板（命令 sink），不是 SQL 模板
    verifier = VerificationAgent.__new__(VerificationAgent)
    matched = verifier._gen_sandbox_command(vtype, "app/run.py", 10, "cmd inj", 0)
    cmd = matched["input"]["command"]
    assert "command_injection" in matched["label"]
    assert "subprocess" in cmd or "os.system" in cmd or "os.popen" in cmd
    assert "sqlite3" not in cmd, "命令注入不得走 SQL 模板"


@pytest.mark.asyncio
async def test_sql_injection_check_id_routes_to_sql_template():
    """纯 SQL 规则 id 兜底落库类型 = sql_injection，走 SQL 专用模板。"""
    from app.services.agent.agents.verification import VerificationAgent

    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="app/db.py",
            rule="python.django.security.injection.sql.sql-injection",
            vuln_type=None,
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    vtype = agent._all_findings[0]["vulnerability_type"]
    assert vtype == "sql_injection"

    verifier = VerificationAgent.__new__(VerificationAgent)
    matched = verifier._gen_sandbox_command(vtype, "app/db.py", 10, "sqli", 0)
    assert "sqlite3" in matched["input"]["command"]


@pytest.mark.asyncio
async def test_other_injection_check_id_without_command_hint_routes_to_sql():
    """其他含 'injection' 但无 command/exec/subprocess 线索的 check_id
    （_map 第 1 分支截胡为 'injection'，如 open-redirect-injection 类）：
    兜底按 F1 别名现状落 sql_injection（确定性专用模板优于 default 空转）。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="app/web.py",
            rule="custom.open-redirect-injection",
            vuln_type=None,
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert agent._all_findings[0]["vulnerability_type"] == "sql_injection"


@pytest.mark.parametrize(
    "rule_id,expected",
    [
        ("p.command-injection", "command_injection"),
        ("java.lang.security.audit.os-command-injection", "command_injection"),
        ("python.lang.security.audit.dangerous-asyncio-exec-injection", "command_injection"),
        ("p.sql-injection", "sql_injection"),
        ("p.sql-injection.execute", "sql_injection"),
    ],
)
def test_canonicalize_type_mapping_unit(rule_id, expected):
    """分流函数单元：命令注入关键词 → command_injection；含 sql → sql_injection。"""
    from app.services.agent.agents.orchestrator import (
        _canonicalize_semgrep_fallback_type,
    )

    assert _canonicalize_semgrep_fallback_type("injection", rule_id) == expected
    # 非 injection 类型原样返回（不干预其他映射）
    assert _canonicalize_semgrep_fallback_type("xss", rule_id) == "xss"
    assert _canonicalize_semgrep_fallback_type("other", rule_id) == "other"


# ============ ⑤ 去重与过滤共存 ============

@pytest.mark.asyncio
async def test_dedup_then_filter_counts_deduped_candidates():
    """去重键 file_path+rule_id 先于过滤生效：同文件同规则的 2 条配置类
    命中只计 1 条被滤；可验证候选不受影响。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path=".github/workflows/ci.yml", line=10,
            rule="yaml.github-actions.pull-request-target", vuln_type="other",
        ),
        _semgrep_finding(
            path=".github/workflows/ci.yml", line=88,
            rule="yaml.github-actions.pull-request-target", vuln_type="other",
        ),
        _semgrep_finding(path="app/db.py", rule="p.sql-injection", vuln_type="sql_injection"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert len(agent._all_findings) == 1
    assert agent._all_findings[0]["vulnerability_type"] == "sql_injection"
    filtered = _filtered_obs(agent)
    assert len(filtered) == 1
    assert "1 条" in filtered[0]["reason"], f"去重后应只滤 1 条，got: {filtered[0]['reason']!r}"


@pytest.mark.asyncio
async def test_mixed_verifiable_and_filtered_observability_breakdown():
    """混合场景：可验证候选落库 + 被滤候选记 observation（含类型分布）。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path="app/db.py", rule="p.sql-injection", vuln_type="sql_injection"),
        _semgrep_finding(path="app/tpl.py", rule="p.xss", vuln_type="xss", severity="medium"),
        _semgrep_finding(path="Dockerfile", rule="p.dockerfile", vuln_type="other", severity="high"),
        _semgrep_finding(path="app/xxe.py", rule="p.xxe", vuln_type="xxe", severity="high"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 2
    assert {f["vulnerability_type"] for f in agent._all_findings} == {"sql_injection", "xss"}
    assert len(_fallback_obs(agent)) == 1
    filtered = _filtered_obs(agent)
    assert len(filtered) == 1
    assert "2 条" in filtered[0]["reason"]
    assert "other" in filtered[0]["reason"]
    assert "xxe" in filtered[0]["reason"]
