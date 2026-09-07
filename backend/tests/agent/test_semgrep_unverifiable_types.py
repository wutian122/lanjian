"""A1（Task 19 follow-up）：Semgrep 兜底 EL 表达式/SSTI/模板注入类的
"非标准注入类"识别——归 unverifiable 排除出沙箱，消除确定性 PoC 白跑。

生产实证（tomcat 审计）：ELProcessor.java（EL 表达式注入）的 Semgrep 规则
check_id 含 "injection"，被 _map_semgrep_to_vuln_type 泛化为 "injection"，
再经 _canonicalize 二次分流为 sql_injection（无 command/exec 线索）→
误走 SQL 专用模板（sink 硬编码 execute/raw/query/sql），EL 代码零命中 →
NO_SINK → not_reproducible——沙箱白跑、验证预算空耗。

兜底构建层（_build_semgrep_fallback_candidates）在 canonicalize 之前识别
EL/表达式/SSTI/模板引擎特征（词边界匹配，防 "el" 短词误伤 model/level 等），
命中候选 vulnerability_type 归 "unverifiable"：不进 _all_findings、不送沙箱，
独立记 semgrep_fallback_unverifiable observation（与 F1 的
semgrep_fallback_filtered 口径分开）。全滤时维持 Task 11 "无兜底产出" 收口。
"""
import pytest

from app.services.agent.agents.orchestrator import OrchestratorAgent


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


def _unverifiable_obs(agent):
    return [o for o in agent._gate_observations if o.get("gate") == "semgrep_fallback_unverifiable"]


def _filtered_obs(agent):
    return [o for o in agent._gate_observations if o.get("gate") == "semgrep_fallback_filtered"]


def _fallback_obs(agent):
    return [o for o in agent._gate_observations if o.get("gate") == "semgrep_fallback"]


# ============ ① EL 表达式类：归 unverifiable，不落库、不送沙箱 ============

@pytest.mark.asyncio
async def test_el_processor_check_id_classified_unverifiable():
    """生产实证形态：java.el.ELProcessor 规则（_map 映射为 other）兜底构建时
    归 unverifiable——不进 _all_findings/验证队列，记 semgrep_fallback_unverifiable
    observation，且不计入 F1 的 filtered 口径。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="src/main/java/Bean.java",
            rule="java.el.ELProcessor",
            message="ELProcessor eval expression",
            vuln_type=None,  # _map → "other"
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert agent._all_findings == []
    assert agent._actionable_findings() == []
    assert _fallback_obs(agent) == []
    unverifiable = _unverifiable_obs(agent)
    assert len(unverifiable) == 1
    assert "1 条" in unverifiable[0]["reason"]
    assert _filtered_obs(agent) == [], "EL 类不得计入 F1 配置类 filtered 口径"


@pytest.mark.asyncio
async def test_el_injection_check_id_not_canonicalized_to_sql():
    """白跑根因形态：check_id 同时含 el 与 injection（如 javax.el-expression-injection），
    _map→"injection"→canonicalize 会误分流为 sql_injection 进 SQL 模板白跑。
    A1 在 canonicalize 之前拦截：归 unverifiable，绝不落库为 sql_injection。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="src/main/java/El.java",
            rule="javax.el-expression-injection",
            message="EL expression injection",
            vuln_type="injection",  # 预扫存储的泛化映射值
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert agent._all_findings == []
    unverifiable = _unverifiable_obs(agent)
    assert len(unverifiable) == 1
    # 构建产物自身类型标记为 unverifiable，而非 sql_injection
    candidates = agent._build_semgrep_fallback_candidates()
    assert candidates[0]["vulnerability_type"] == "unverifiable"
    assert candidates[0]["unverifiable_kind"]


# ============ ② SSTI/模板引擎家族同等待遇 ============

@pytest.mark.parametrize(
    "rule",
    [
        "java.spring.security.audit.spel-injection",
        "java.ognl.security.ognl-injection",
        "java.mvel.mvel-expression-injection",
        "freemarker.template.injection.ssti",
        "velocity.template-injection",
        "org.thymeleaf.template-injection",
        "python.flask.security.audit.template-injection",
        "python.jinja2.security.audit.ssti",
    ],
)
@pytest.mark.asyncio
async def test_ssti_template_family_classified_unverifiable(rule):
    """SpEL/OGNL/MVEL/FreeMarker/Velocity/Thymeleaf/Jinja 模板注入类全部归
    unverifiable（这些规则 id 多含 injection，旧路径会误分流 sql_injection
    白跑 SQL 模板）。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path=f"app/tpl_{rule.split('.')[-1]}.java",
            rule=rule,
            vuln_type=None,
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0, f"{rule} 不应落库送沙箱"
    assert agent._all_findings == []
    assert len(_unverifiable_obs(agent)) == 1
    assert _filtered_obs(agent) == []


# ============ ③ 词边界：短词 "el" 不得误伤 model/level 等 ============

@pytest.mark.parametrize(
    "rule",
    [
        "python.django.security.injection.sql.model-based-raw-sql",
        "custom.sql-injection.level2-check",
        "custom.audit.panel-config-review",
        "python.lang.security.audit.cancel-token-sql",
    ],
)
def test_classifier_word_boundary_no_false_positive(rule):
    """含 'el' 子串但非独立 token（model/level/panel/cancel）不得判为 EL 类。"""
    from app.services.agent.agents.orchestrator import (
        _classify_unverifiable_semgrep_fallback,
    )

    assert _classify_unverifiable_semgrep_fallback("injection", rule) is None


@pytest.mark.asyncio
async def test_sql_rule_with_model_token_still_persists():
    """端到端：规则 id 含 'model' 子串的 SQL 注入候选正常走 F1 二次分流落库，
    不被 A1 误杀。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="app/model_db.py",
            rule="python.django.security.injection.sql.model-based-raw-sql",
            vuln_type=None,
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert agent._all_findings[0]["vulnerability_type"] == "sql_injection"
    assert _unverifiable_obs(agent) == []
    assert _filtered_obs(agent) == []


# ============ ④ F1 既有行为不变：SQL/命令注入二次分流正常 ============

@pytest.mark.asyncio
async def test_command_injection_still_routes_to_command_template():
    """命令注入规则（含 injection 被 _map 截胡）仍经 _canonicalize 分流为
    command_injection 落库——A1 识别不得波及真实可验证注入类。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="app/run.py",
            rule="python.lang.security.audit.os-command-injection",
            vuln_type=None,
        ),
        _semgrep_finding(
            path="app/db.py",
            rule="python.django.security.injection.sql.sql-injection",
            vuln_type=None,
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 2
    vtypes = {f["vulnerability_type"] for f in agent._all_findings}
    assert vtypes == {"command_injection", "sql_injection"}
    assert _unverifiable_obs(agent) == []


# ============ ⑤ 与 F1 配置类过滤共存：两类 observation 分开记 ============

@pytest.mark.asyncio
async def test_unverifiable_and_config_filtered_recorded_separately():
    """混合场景：EL 类（unverifiable）+ 配置类（other，F1 filtered）+ 可验证
    SQL 候选——两条 observation 各自独立、口径不串。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(
            path="src/main/java/El.java",
            rule="javax.el-expression-injection",
            vuln_type="injection",
        ),
        _semgrep_finding(
            path=".github/workflows/ci.yml",
            rule="yaml.github-actions.security.pull-request-target",
            vuln_type="other",
            severity="medium",
        ),
        _semgrep_finding(path="app/db.py", rule="p.sql-injection", vuln_type="sql_injection"),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 1
    assert agent._all_findings[0]["vulnerability_type"] == "sql_injection"
    unverifiable = _unverifiable_obs(agent)
    filtered = _filtered_obs(agent)
    assert len(unverifiable) == 1
    assert "1 条" in unverifiable[0]["reason"]
    assert len(filtered) == 1
    assert "1 条" in filtered[0]["reason"]
    assert "other" in filtered[0]["reason"]
    # 两条 observation 互不包含对方口径
    assert "unverifiable" not in filtered[0]["reason"]
    assert "other" not in unverifiable[0]["reason"]


@pytest.mark.asyncio
async def test_unverifiable_observation_includes_kind_breakdown():
    """observation 含命中特征分布（el/ssti/... 计数），供可观测定位。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path="a/El1.java", rule="java.el.ELProcessor", vuln_type=None),
        _semgrep_finding(path="a/El2.java", rule="javax.el-expression", vuln_type=None),
        _semgrep_finding(
            path="a/tpl.py", rule="python.flask.security.audit.template-injection", vuln_type=None
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    obs = _unverifiable_obs(agent)
    assert len(obs) == 1
    reason = obs[0]["reason"]
    assert "3 条" in reason
    # el 命中 2 条、template_injection 命中 1 条（分布字符串含计数）
    assert "el×2" in reason
    assert "template_injection×1" in reason


# ============ ⑥ 全滤降级：_all_findings 空，Task 11 收口不受影响 ============

@pytest.mark.asyncio
async def test_all_unverifiable_keeps_no_fallback_semantics():
    """候选全部为 EL/SSTI 类 → added=0、_all_findings 空、无 semgrep_fallback
    observation——completed_with_gaps 收口路径与幂等行为不变。"""
    agent = _make_orch()
    agent._semgrep_findings = [
        _semgrep_finding(path="a/El.java", rule="java.el.ELProcessor", vuln_type=None),
        _semgrep_finding(
            path="a/spel.java", rule="java.spring.security.audit.spel-injection", vuln_type=None
        ),
    ]

    added = await agent._apply_semgrep_fallback()

    assert added == 0
    assert agent._all_findings == []
    assert agent._actionable_findings() == []
    assert _fallback_obs(agent) == []
    assert len(_unverifiable_obs(agent)) == 1
    assert agent._semgrep_fallback_applied is True
    # 重复调用不重复记录
    await agent._apply_semgrep_fallback()
    assert len(_unverifiable_obs(agent)) == 1


# ============ 分类器单元 ============

@pytest.mark.parametrize(
    "rule,expected_kind",
    [
        ("java.el.ELProcessor", "el"),
        ("javax.el-expression", "el"),
        ("custom.el-injection", "el"),
        ("java.expression-language.injection", "expression"),
        ("python.jinja2.security.audit.ssti", "ssti"),
        ("python.flask.security.audit.template-injection", "template_injection"),
        ("java.spring.security.audit.spel-injection", "spel"),
        ("java.ognl.ognl-injection", "ognl"),
        ("java.mvel-expression-injection", "mvel"),
        ("freemarker.template.injection", "freemarker"),
        ("org.thymeleaf.template-injection", "thymeleaf"),
        ("velocity.template-injection", "velocity"),
    ],
)
def test_classifier_detects_expression_ssti_families(rule, expected_kind):
    from app.services.agent.agents.orchestrator import (
        _classify_unverifiable_semgrep_fallback,
    )

    assert _classify_unverifiable_semgrep_fallback("injection", rule) == expected_kind


@pytest.mark.parametrize(
    "rule",
    [
        "python.django.security.injection.sql.sql-injection",
        "python.lang.security.audit.os-command-injection",
        "java.lang.security.audit.ssrf",
        "python.pickling.deserialization",
        "p.model-binding",
        "p.channel-config",
    ],
)
def test_classifier_ignores_verifiable_and_unrelated_rules(rule):
    from app.services.agent.agents.orchestrator import (
        _classify_unverifiable_semgrep_fallback,
    )

    assert _classify_unverifiable_semgrep_fallback(None, rule) is None
