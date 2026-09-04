"""Task 5（sandbox-verification-hard-gate）：Semgrep 静态短路先执行确定性 PoC。

豁免路径 a 收口：source=semgrep 的 hardcoded_secret/weak_crypto/deserialization/xxe
四类 finding 此前在 _normalize_verification_outcome 中无条件 STATIC_CONFIRMED +
is_verified=True，零沙箱证据。改造后：

- hardcoded_secret/deserialization（有专用确认模板，R3 确定性执行产出
  VULNERABILITY_STATIC_ONLY → attempt.static_evidence）：状态由
  compute_verification_status 从 attempt + 静态证据共同推导——
  PoC 证伪（NO_SINK）→ not_reproducible；基础设施全失败 → needs_context；
- weak_crypto/xxe（无专用确认模板）：标 sandbox_skip_reason="no_poc_template"
  显式豁免（spec Requirement 条件 2），保持 static_confirmed 现行为；
- 非四类 semgrep finding（如 sql_injection）：不走短路，行为不变。

测试驱动真实行为：模板 heredoc PoC 提取后 subprocess 真实执行（非字符串断言），
经 _record_sandbox_attempt 真实记录路径产出 attempt，再绑定 + 归一化。
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import VerificationAgent


def _agent() -> VerificationAgent:
    """轻量实例（同 test_infra_error_separation 模式）：仅初始化 attempt 存储。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}
    agent._all_findings = []

    class _Cfg:
        name = "Verification"

    agent.config = _Cfg()
    return agent


def _semgrep_finding(vuln_type: str, file_path: str = "app/demo.py", **overrides) -> dict:
    f = {
        "title": f"semgrep {vuln_type} finding",
        "vulnerability_type": vuln_type,
        "file_path": file_path,
        "line_start": 10,
        "source": "semgrep",
        "severity": "high",
    }
    f.update(overrides)
    return f


def _extract_poc_source(command: str) -> str:
    """从 'cat > /tmp/poc_N.py << 'POC_EOF' ... POC_EOF' 提取 Python 源码。"""
    start = command.find("POC_EOF'")
    end = command.rfind("POC_EOF")
    assert start != -1 and end != -1 and end > start
    return command[start + len("POC_EOF'") : end]


def _run_deterministic_poc(agent: VerificationAgent, finding: dict, tmp_path, source_text: str):
    """复刻生产 R3 链路（verification.py:1172 → 1284 → 1681）：

    _build_sandbox_commands 生成命令（注入 _sandbox_finding_id）→ 提取 heredoc
    PoC 源码 → /workspace/src 替换为 tmp 目录后 subprocess 真实执行 →
    _record_sandbox_attempt 记录（含 finding_id 索引）→ _attach_runtime_sandbox_attempts
    绑定到 finding。返回 (proc, finding)。
    """
    commands = agent._build_sandbox_commands([finding])
    assert commands, f"类型 {finding.get('vulnerability_type')} 应有确定性模板命令"
    sc = commands[0]
    cmd_input = sc["input"]
    script = _extract_poc_source(cmd_input["command"])
    compile(script, f"<poc-{finding['vulnerability_type']}>", "exec")

    src_dir = tmp_path / "src"
    target = src_dir / finding["file_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source_text)

    script = script.replace("/workspace/src/", str(src_dir) + "/")
    poc_path = tmp_path / f"poc_{finding['vulnerability_type']}.py"
    poc_path.write_text(script)
    proc = subprocess.run(
        [sys.executable, str(poc_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # 与沙箱工具输出格式一致（_record_sandbox_attempt 据此解析退出码）
    obs = f"Sandbox result\n退出码: {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    agent._record_sandbox_attempt(cmd_input, obs, finding_id=sc.get("finding_id"))
    agent._attach_runtime_sandbox_attempts(finding)
    return proc


# ============ ① hardcoded_secret：PoC 执行产生 attempt，状态共同推导 ============


def test_hardcoded_secret_semgrep_runs_poc_and_derives_static_confirmed(tmp_path):
    """源码含硬编码密钥 → PoC 真实执行输出 VULNERABILITY_STATIC_ONLY →
    attempt 绑定到 finding，归一化由 attempt 证据推导 static_confirmed（非无条件短路）。"""
    agent = _agent()
    finding = _semgrep_finding("hardcoded_secret", file_path="app/config.py")
    proc = _run_deterministic_poc(
        agent, finding, tmp_path, 'API_KEY = "sk-abcd1234efgh5678ijklmnop"\n'
    )
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_STATIC_ONLY" in proc.stdout

    # 先执行 PoC：finding 上必须有真实 attempt（豁免路径 a 收口的核心）
    assert finding.get("sandbox_attempts"), "确定性 PoC 执行后应绑定 attempt"
    assert finding["sandbox_attempts"][0].get("static_evidence") is True

    normalized = agent._normalize_verification_outcome(dict(finding))
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


def test_hardcoded_secret_semgrep_poc_no_sink_derives_not_reproducible(tmp_path):
    """源码无密钥 pattern → PoC 输出 NO_SINK（semgrep 误报被证伪）→
    不得再无条件 static_confirmed，状态引擎推导 not_reproducible。"""
    agent = _agent()
    finding = _semgrep_finding("hardcoded_secret", file_path="app/config.py")
    proc = _run_deterministic_poc(agent, finding, tmp_path, "DEBUG = True\n")
    assert proc.returncode == 0
    assert "NO_SINK" in proc.stdout

    normalized = agent._normalize_verification_outcome(dict(finding))
    assert (
        normalized["verification_status"] == "not_reproducible"
    ), f"PoC 证伪（无密钥）应判 not_reproducible，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False


# ============ ② deserialization：同链路 ============


def test_deserialization_semgrep_runs_poc_and_derives_static_confirmed(tmp_path):
    """源码含 pickle.load sink → PoC 输出 VULNERABILITY_STATIC_ONLY → static_confirmed。"""
    agent = _agent()
    finding = _semgrep_finding("deserialization", file_path="app/loader.py")
    source = "import pickle\n" "def load_data(payload):\n" "    return pickle.loads(payload)\n"
    proc = _run_deterministic_poc(agent, finding, tmp_path, source)
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_STATIC_ONLY" in proc.stdout
    assert finding.get("sandbox_attempts")
    assert finding["sandbox_attempts"][0].get("static_evidence") is True

    normalized = agent._normalize_verification_outcome(dict(finding))
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


def test_deserialization_semgrep_poc_no_sink_derives_not_reproducible(tmp_path):
    """源码无危险反序列化 sink → PoC 输出 NO_SINK → not_reproducible。"""
    agent = _agent()
    finding = _semgrep_finding("deserialization", file_path="app/loader.py")
    proc = _run_deterministic_poc(
        agent,
        finding,
        tmp_path,
        "import json\n" "def load_data(payload):\n" "    return json.loads(payload)\n",
    )
    assert proc.returncode == 0
    assert "NO_SINK" in proc.stdout

    normalized = agent._normalize_verification_outcome(dict(finding))
    assert (
        normalized["verification_status"] == "not_reproducible"
    ), f"PoC 证伪（无 sink）应判 not_reproducible，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False


# ============ ③ weak_crypto/xxe：无专用模板 → 显式豁免标记 ============


def test_weak_crypto_semgrep_marked_no_poc_template_and_stays_static_confirmed():
    """weak_crypto 无专用确认模板 → sandbox_skip_reason=no_poc_template，
    保持 static_confirmed + is_verified=True 现行为（spec 条件 2 显式豁免）。"""
    agent = _agent()
    finding = _semgrep_finding("weak_crypto")
    normalized = agent._normalize_verification_outcome(dict(finding))
    assert normalized["sandbox_skip_reason"] == "no_poc_template"
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


def test_xxe_semgrep_marked_no_poc_template_and_stays_static_confirmed():
    """xxe 同 weak_crypto：显式豁免 no_poc_template + static_confirmed。"""
    agent = _agent()
    finding = _semgrep_finding("xxe", file_path="app/XmlReader.java")
    normalized = agent._normalize_verification_outcome(dict(finding))
    assert normalized["sandbox_skip_reason"] == "no_poc_template"
    assert normalized["verification_status"] == "static_confirmed"
    assert normalized["is_verified"] is True


# ============ ④ 非四类 semgrep finding：行为不变（不走短路） ============


def test_non_shortcut_semgrep_type_without_attempts_is_needs_context():
    """sql_injection 不在短路四类 → 无 attempt 时走状态引擎 needs_context（现状行为）。"""
    agent = _agent()
    finding = _semgrep_finding("sql_injection")
    normalized = agent._normalize_verification_outcome(dict(finding))
    assert normalized["verification_status"] == "needs_context"
    assert normalized["is_verified"] is False
    assert "no_poc_template" not in str(normalized.get("sandbox_skip_reason") or "")


def test_non_shortcut_semgrep_type_with_confirmed_evidence_stays_confirmed():
    """sql_injection semgrep finding 带动态铁证 attempt → confirmed（不被短路干扰）。"""
    agent = _agent()
    finding = _semgrep_finding(
        "sql_injection",
        sandbox_attempts=[
            {
                "success": True,
                "exit_code": 0,
                "evidence_summary": "VULNERABILITY_CONFIRMED: SQL injection via id parameter",
                "target_ref": "app/login.py:10",
            }
        ],
    )
    normalized = agent._normalize_verification_outcome(dict(finding))
    assert normalized["verification_status"] == "confirmed"
    assert normalized["is_verified"] is True


# ============ ⑤ infra 失败承接 Task 1：needs_context 而非 static_confirmed ============


def test_hardcoded_secret_semgrep_infra_failure_returns_needs_context():
    """hardcoded_secret 的确定性 PoC 因 Docker 不可用全失败（infra_error）→
    承接 Task 1 状态机：needs_context + is_verified=False，不得短路成 static_confirmed。"""
    agent = _agent()
    finding = _semgrep_finding("hardcoded_secret", file_path="app/config.py")
    # 复刻 R3 链路但沙箱基础设施故障
    commands = agent._build_sandbox_commands([finding])
    sc = commands[0]
    agent._record_sandbox_attempt(
        sc["input"],
        "工具执行失败: Docker not available: Error while creating mount source path",
        finding_id=sc.get("finding_id"),
    )
    agent._attach_runtime_sandbox_attempts(finding)
    assert finding["sandbox_attempts"][0].get("infra_error") is True

    normalized = agent._normalize_verification_outcome(dict(finding))
    assert (
        normalized["verification_status"] == "needs_context"
    ), f"全 infra_error 应判 needs_context，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False


def test_weak_crypto_semgrep_infra_failure_returns_needs_context():
    """weak_crypto 即便走豁免路径，若已有 attempt 且全部 infra_error →
    仍承接 Task 1 报沙箱环境故障（不得用 no_poc_template 掩盖基础设施故障）。"""
    agent = _agent()
    finding = _semgrep_finding(
        "weak_crypto",
        sandbox_attempts=[
            {
                "success": False,
                "exit_code": -1,
                "evidence_summary": "工具执行失败: Docker not available",
                "infra_error": True,
            }
        ],
    )
    normalized = agent._normalize_verification_outcome(dict(finding))
    assert (
        normalized["verification_status"] == "needs_context"
    ), f"全 infra_error 应判 needs_context，got {normalized['verification_status']}"
    assert normalized["is_verified"] is False
