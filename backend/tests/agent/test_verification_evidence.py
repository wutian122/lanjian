"""R1/R2/R3 验证证据根治测试：确定性状态引擎 + 反伪造 + 全量证据绑定。"""
from app.services.agent.agents.verification import (
    VerificationAgent,
    compute_verification_status,
)


def _make_agent():
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    # ID 匹配分支的日志用到 self.name → self.config.name，mock 一个 config
    class _Cfg:
        name = "Verification"
    agent.config = _Cfg()
    return agent


def _finding(**overrides):
    f = {
        "title": "SSRF in MCP",
        "vulnerability_type": "ssrf",
        "file_path": "console/AppController.java",
        "line_start": 113,
        "verification_method": "sandbox_exec",
    }
    f.update(overrides)
    return f


# ============ R1: 确定性状态引擎 ============

def test_confirmed_from_evidence_even_when_llm_omitted_verdict():
    """有铁证（success+exit0+VULNERABILITY_CONFIRMED+匹配）但 LLM 未写 confirmed → 仍判 confirmed（根治根因1）。"""
    finding = _finding()
    attempts = [
        {
            "success": True,
            "exit_code": 0,
            "evidence_summary": "VULNERABILITY_CONFIRMED: SSRF via URI.create()",
            "target_ref": "console/AppController.java:113",
        }
    ]
    agent = _make_agent()
    agent._sandbox_attempts = attempts
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True


def test_no_evidence_is_needs_context():
    """无任何证据 → needs_context（LLM 自述 confirmed 不再被信任为起点）。"""
    finding = _finding()
    normalized = _make_agent()._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False


def test_attempts_without_confirmation_are_not_reproducible():
    """有尝试但无确认证据 → not_reproducible（跑了但没复现）。"""
    finding = _finding(sandbox_attempts=[{"success": True, "exit_code": 0, "evidence_summary": "no vuln marker"}])
    normalized = _make_agent()._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_false_positive_preserved():
    """LLM 显式 false_positive 且无 confirmed 证据 → false_positive。"""
    finding = _finding(verdict="false_positive", verification_status="false_positive")
    normalized = _make_agent()._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "false_positive"
    assert normalized["is_verified"] is False


# ============ R3: 反伪造 ============

def test_fabricated_attempt_marked_and_excluded():
    """Simulated + VULNERABILITY_CONFIRMED → fabricated=True，不判 confirmed（根治根因4）。"""
    agent = _make_agent()
    # 直接调用确定性引擎：伪造 attempt 不得作为证据
    finding = _finding()
    attempts = [
        {
            "success": True,
            "exit_code": 0,
            "fabricated": True,
            "evidence_summary": "Simulated trust-all context: verify_mode=0 ... VULNERABILITY_CONFIRMED",
            "target_ref": "console/AppController.java:113",
        }
    ]
    status, is_verified, _ = compute_verification_status(
        finding, attempts,
        attempt_has_vuln_evidence_fn=agent._attempt_has_vuln_evidence,
        attempt_matches_finding_fn=agent._sandbox_attempt_matches_finding,
    )
    assert status != "confirmed"
    assert is_verified is False


def test_fabricated_evidence_marked_in_record_sandbox_attempt():
    """_record_sandbox_attempt 对 Simulated + 确认标记输出打 fabricated=True 且 success=False。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 -c 'simulated poc'"},
        "Sandbox result\n退出码: 0\nSimulated trust-all context ... VULNERABILITY_CONFIRMED",
    )
    assert len(agent._sandbox_attempts) == 1
    a = agent._sandbox_attempts[0]
    assert a["fabricated"] is True
    assert a["success"] is False


def test_real_sandbox_evidence_not_fabricated():
    """真实读到源码并输出确认标记 → 不判 fabricated，可判 confirmed。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_0.py"},
        "Sandbox result\n退出码: 0\nSource: 4039 chars loaded\nVULNERABILITY_CONFIRMED: Trust-all X509TrustManager",
    )
    assert len(agent._sandbox_attempts) == 1
    a = agent._sandbox_attempts[0]
    assert a.get("fabricated") in (None, False)
    assert a["success"] is True


# ============ R2: 全量证据强制绑定 ============

def test_evidence_bound_for_all_findings_including_llm_omitted():
    """LLM Final Answer 漏报的 finding 仍获得运行时证据（根治根因2：4/5 丢失）。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "cat > /tmp/poc_0.py << 'POC_EOF' ... Target: console/AppController.java:113",
            "target_ref": "console/AppController.java:113",
            "evidence_summary": "VULNERABILITY_CONFIRMED: SSRF",
            "finding_id": "f1",
        },
        {
            "tool": "sandbox_exec",
            "success": True,
            "exit_code": 0,
            "command": "cat > /tmp/poc_1.py << 'POC_EOF' ... Target: app/Other.java:10",
            "target_ref": "app/Other.java:10",
            "evidence_summary": "VULNERABILITY_CONFIRMED: XSS",
            "finding_id": "f2",
        },
    ]
    # 两个 finding，其中一个带 _sandbox_finding_id（ID 绑定）
    f1 = _finding(file_path="console/AppController.java", _sandbox_finding_id="f1")
    f2 = _finding(file_path="app/Other.java", vulnerability_type="xss", _sandbox_finding_id="f2")
    # 模拟 LLM Final Answer 只报告了 f1
    verified = []
    for f in (f1,):
        agent._attach_runtime_sandbox_attempts(f)
        verified.append(agent._normalize_verification_outcome(f))
    # R2 兜底：对全部 findings_to_verify 强制绑定
    agent._bind_runtime_evidence_to_all(verified, [f1, f2])
    assert len(verified) == 2
    # f2（LLM 漏报）现在也有证据并判 confirmed
    f2_merged = [vf for vf in verified if vf.get("file_path") == "app/Other.java"][0]
    assert f2_merged.get("sandbox_attempts")
    assert f2_merged["verification_status"] == "confirmed"


# ============ V6 B1（REQ-VE-1）: 绑定层取消 success 前置 ============

def test_failed_attempt_still_bound_by_finding_id():
    """REQ-VE-1 Scenario 2：同 finding_id 的全部 attempt 均绑定，不过滤 success。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": False,
            "exit_code": 1,
            "command": "python3 /tmp/poc_0.py",
            "target_ref": "console/AppController.java:113",
            "evidence_summary": "payload executed but assertion failed",
            "finding_id": "f1",
        }
    ]
    finding = _finding(_sandbox_finding_id="f1")
    agent._attach_runtime_sandbox_attempts(finding)
    assert finding.get("sandbox_attempts"), "失败 attempt 必须如实绑定，不得被 success 前置过滤丢弃"


def test_all_failed_attempts_lead_to_not_reproducible():
    """REQ-VE-1 Scenario 1：全部尝试失败 → 绑定后判 not_reproducible 而非 needs_context。"""
    agent = _make_agent()
    agent._sandbox_attempts = [
        {
            "tool": "sandbox_exec",
            "success": False,
            "exit_code": 1,
            "command": "python3 /tmp/poc_0.py",
            "target_ref": "console/AppController.java:113",
            "evidence_summary": "Traceback in stderr, no marker",
            "finding_id": "f1",
        }
    ]
    finding = _finding(_sandbox_finding_id="f1")
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert finding.get("sandbox_attempts"), "证据必须落库（非 NULL）"
    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


# ============ V6 B2（REQ-VE-2）: 失败标记收窄 ============

def test_exit0_with_evidence_and_incidental_error_marker_succeeds():
    """REQ-VE-2 Scenario 1：exit 0 + 铁证标记 + 正文 incidental 'Error:' 子串 → success=True。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_0.py"},
        "Sandbox result\n退出码: 0\nSource: 4039 chars loaded\n"
        "payload=1' OR '1'='1 rows=3 -> INJECTABLE\n"
        "VULNERABILITY_CONFIRMED: SQL injection dynamically verified\n"
        "note: some frameworks print Error: on recoverable path",
    )
    assert len(agent._sandbox_attempts) == 1
    a = agent._sandbox_attempts[0]
    assert a["success"] is True, "exit 0 且含铁证标记，不得因 incidental 'Error:' 子串被误杀"
    assert a.get("fabricated") in (None, False)


def test_exit1_with_traceback_fails():
    """REQ-VE-2 Scenario 2：exit 1 + stderr 段 Traceback → success=False（真失败仍识别）。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_0.py"},
        "Sandbox result\n退出码: 1\n标准输出:\n```\nok\n```\n标准错误:\n```\n"
        "Traceback (most recent call last):\n  File \"/tmp/poc_0.py\", line 3\nAttributeError: 'list' object has no attribute 'add'\n```",
    )
    assert agent._sandbox_attempts[0]["success"] is False


def test_exit0_with_stderr_traceback_still_fails():
    """exit 0 但 stderr 段含 Traceback（沙箱包装异常）→ 仍判失败（stderr 段内标记生效）。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_0.py"},
        "Sandbox result\n退出码: 0\n标准输出:\n```\nVULNERABILITY_CONFIRMED: x\n```\n标准错误:\n```\nTraceback ...\n```",
    )
    assert agent._sandbox_attempts[0]["success"] is False


# ============ V6 B4（REQ-VE-4）: 确定性执行按 finding_id 直写索引 ============

def test_record_attempt_with_explicit_finding_id_registers_index():
    """显式 finding_id 直写索引：命令文本无 # FINDING_ID 注释也能登记（不依赖命令文本解析）。"""
    agent = _make_agent()
    agent._record_sandbox_attempt(
        {"command": "python3 /tmp/poc_0.py"},
        "Sandbox result\n退出码: 0\nVULNERABILITY_CONFIRMED: sql injection verified",
        finding_id="f-det-1",
    )
    idx = agent._runtime_attempts_by_finding_id
    assert "f-det-1" in idx, "显式 finding_id 必须登记到运行时证据索引"
    assert len(idx["f-det-1"]) == 1
    assert len(agent._sandbox_attempts) == 1, "扁平列表同步双写"


def test_deterministic_run_registers_all_finding_ids():
    """确定性执行 N 条命令 → 索引 N 个 finding_id 全部登记且内容不依赖命令注释。"""
    import asyncio

    agent = _make_agent()
    agent._sandbox_exec_calls = 0
    agent._sandbox_exec_attempts = 0
    agent._sandbox_exec_success = 0
    agent._verified_finding_indices = set()
    agent._cancelled = False
    agent._cancel_callback = None
    agent._get_sandbox_manager = lambda: None

    async def _fake_exec(tool, cmd_input):
        return "Sandbox result\n退出码: 0\nVULNERABILITY_CONFIRMED: demo"

    async def _fake_emit(kind, msg):
        pass

    agent.execute_tool = _fake_exec
    agent.emit_event = _fake_emit
    commands = [
        {"finding_id": f"f-{i}", "input": {"command": f"python3 /tmp/poc_{i}.py", "timeout": 1}}
        for i in range(3)
    ]
    asyncio.run(agent._run_deterministic_sandbox_commands(commands, None))
    idx = agent._runtime_attempts_by_finding_id
    assert set(idx.keys()) == {"f-0", "f-1", "f-2"}, "N 条命令 → N 个 finding_id 均有索引记录"


def test_binding_consumes_index_full_set():
    """绑定层 ID 分支优先消费索引：反解失败（扁平列表无 id）的 attempt 也能按索引全量绑定。"""
    agent = _make_agent()
    att_ok = {
        "success": True,
        "exit_code": 0,
        "evidence_summary": "VULNERABILITY_CONFIRMED: sqli",
        "target_ref": "console/AppController.java:113",
        "command": "python3 /tmp/poc_0.py",
        "finding_id": "f-idx-1",
    }
    att_fail = {
        "success": False,
        "exit_code": 1,
        "evidence_summary": "Traceback in stderr",
        "target_ref": "console/AppController.java:113",
        "command": "python3 /tmp/poc_0.py --retry",
        "finding_id": None,  # 命令文本反解失败：扁平列表里无 finding_id
    }
    agent._sandbox_attempts = [att_ok, att_fail]
    # 确定性执行按显式 finding_id 登记（含反解失败的那条）
    agent._runtime_attempts_by_finding_id = {"f-idx-1": [att_ok, att_fail]}
    finding = _finding(_sandbox_finding_id="f-idx-1")
    agent._attach_runtime_sandbox_attempts(finding)
    assert len(finding["sandbox_attempts"]) == 2, (
        "索引消费必须全量：反解失败的 attempt 也要按索引绑定（扁平过滤只得 1 条是缺陷）"
    )


def test_attach_id_branch_no_double_count_on_rebind():
    """同一 finding 重复进入 ID 绑定分支（R2 全量绑定重入等）不得重复附加同一批 attempt。"""
    agent = _make_agent()
    att = {
        "success": True,
        "exit_code": 0,
        "evidence_summary": "ran poc, no vuln marker",
        "target_ref": "console/AppController.java:113",
        "command": "python3 /tmp/poc_0.py",
        "finding_id": "f-dup-1",
    }
    agent._sandbox_attempts = [att]
    agent._runtime_attempts_by_finding_id = {"f-dup-1": [att]}
    finding = _finding(_sandbox_finding_id="f-dup-1")
    agent._attach_runtime_sandbox_attempts(finding)
    agent._attach_runtime_sandbox_attempts(finding)
    assert len(finding["sandbox_attempts"]) == 1, "语义重复的 attempt 必须去重，不得双计"


# ============ V6 B3（REQ-VE-3）: 空 Final Answer 回填证据 ============

def test_empty_final_answer_backfills_attempts():
    """空 Final Answer + 确定性证据存在 → 回填 attempts、状态据实、非全 needs_context。"""
    agent = _make_agent()
    att_confirmed = {
        "success": True,
        "exit_code": 0,
        "evidence_summary": "VULNERABILITY_CONFIRMED: sqli dynamically verified",
        "target_ref": "console/AppController.java:113",
        "command": "python3 /tmp/poc_0.py",
        "finding_id": "f-b3-1",
    }
    att_failed = {
        "success": False,
        "exit_code": 1,
        "evidence_summary": "Traceback in stderr, not reproduced",
        "target_ref": "console/AppController.java:200",
        "command": "python3 /tmp/poc_1.py",
        "finding_id": "f-b3-2",
    }
    agent._sandbox_attempts = [att_confirmed, att_failed]
    agent._runtime_attempts_by_finding_id = {
        "f-b3-1": [att_confirmed],
        "f-b3-2": [att_failed],
    }
    findings = [
        _finding(_sandbox_finding_id="f-b3-1", line_start=113),
        _finding(_sandbox_finding_id="f-b3-2", line_start=200),
    ]
    results = agent._finalize_findings_without_final_answer(findings)
    assert len(results) == 2
    by_line = {r.get("line_start"): r for r in results}
    r1, r2 = by_line[113], by_line[200]
    assert r1.get("sandbox_attempts"), "成功证据必须回填（落库非 NULL）"
    assert r1["verification_status"] == "confirmed"
    assert r1["is_verified"] is True
    assert r2.get("sandbox_attempts"), "失败证据也必须回填（落库非 NULL）"
    assert r2["verification_status"] == "not_reproducible"
    assert r2["is_verified"] is False
