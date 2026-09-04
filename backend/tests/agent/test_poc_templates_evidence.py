"""Task 4（sandbox-verification-hard-gate）：三个无确认输出模板补全证据分支。

path_traversal / hardcoded_secret / deserialization 三个确定性 PoC 模板此前
没有确认输出分支——跑了也白跑（永远无证据 → not_reproducible）。本测试锁定：

1. 有 sink 场景：提取模板 heredoc 的 PoC 源码真实执行（/workspace/src 替换为
   tmp 目录），输出含对应确认标记（path_traversal 演示成功 →
   VULNERABILITY_CONFIRMED(STATIC)；secret/deserialization → VULNERABILITY_STATIC_ONLY）；
2. 无 sink 场景：输出不含任何 VULNERABILITY_ 确认标记（not_reproducible 路径保持）；
3. 标记被既有识别逻辑消费：static_evidence=True、非动态铁证、非 infra 错误，
   compute 链路终态 static_confirmed。
"""
import os
import subprocess
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.agent.agents.verification import VerificationAgent


def _agent() -> VerificationAgent:
    return VerificationAgent(llm_service=MagicMock(), tools={})


def _record_agent() -> VerificationAgent:
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}

    class _Cfg:
        name = "Verification"

    agent.config = _Cfg()
    return agent


def _extract_poc_source(command: str) -> str:
    """从 'cat > /tmp/poc_N.py << 'POC_EOF' ... POC_EOF' 提取 Python 源码。"""
    start = command.find("POC_EOF'")
    end = command.rfind("POC_EOF")
    assert start != -1 and end != -1 and end > start
    return command[start + len("POC_EOF'"):end]


def _run_poc(tmp_path, vuln_type: str, file_name: str, source_text: str):
    """生成模板 → 提取 PoC 源码 → 以 tmp 目录替换 /workspace/src 后真实执行。"""
    agent = _agent()
    cmd = agent._gen_sandbox_command(vuln_type, file_name, 10, f"{vuln_type} demo", 0)
    assert cmd is not None
    script = _extract_poc_source(cmd["input"]["command"])
    compile(script, f"<poc-{vuln_type}>", "exec")  # 模板语法必须有效

    src_dir = tmp_path / "src"
    target = src_dir / file_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source_text)

    script = script.replace("/workspace/src/", str(src_dir) + "/")
    poc_path = tmp_path / f"poc_{vuln_type}.py"
    poc_path.write_text(script)
    return subprocess.run(
        [sys.executable, str(poc_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ============ ① 有 sink → 输出含对应确认标记 ============

def test_path_traversal_sink_with_demo_emits_confirmed_static(tmp_path):
    """path_traversal：源码含 open(+os.path.join 路径拼接 sink，os.path 逃逸演示成功
    → VULNERABILITY_CONFIRMED(STATIC)。"""
    source = (
        "import os\n"
        "\n"
        "def read_user_file(base_dir, user_filename):\n"
        "    path = os.path.join(base_dir, user_filename)\n"
        "    with open(path) as f:\n"
        "        return f.read()\n"
    )
    proc = _run_poc(tmp_path, "path_traversal", "app/vuln_pt.py", source)
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_CONFIRMED(STATIC)" in proc.stdout, (
        f"路径拼接 sink + 逃逸演示应输出 CONFIRMED(STATIC):\n{proc.stdout}"
    )
    # 禁止裸确认标记（反伪造规则：模板演示性确认一律 (STATIC) 变体）
    assert "VULNERABILITY_CONFIRMED:" not in proc.stdout


def test_hardcoded_secret_match_emits_static_only(tmp_path):
    """hardcoded_secret：源码含硬编码密钥（Secret pattern found: N, N>0）
    → VULNERABILITY_STATIC_ONLY（密钥类动态确认无意义，static 档）。"""
    source = 'API_KEY = "sk-abcd1234efgh5678ijklmnop"\n'
    proc = _run_poc(tmp_path, "hardcoded_secret", "app/vuln_hs.py", source)
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_STATIC_ONLY" in proc.stdout, (
        f"密钥 pattern 命中应输出 STATIC_ONLY:\n{proc.stdout}"
    )
    assert "VULNERABILITY_CONFIRMED" not in proc.stdout


def test_deserialization_sink_with_demo_emits_static_only(tmp_path):
    """deserialization：源码含 pickle.load 危险 sink 且可达输入构造演示成功
    → VULNERABILITY_STATIC_ONLY。"""
    source = (
        "import pickle\n"
        "\n"
        "def load_user_data(blob):\n"
        "    return pickle.loads(blob)\n"
    )
    proc = _run_poc(tmp_path, "deserialization", "app/vuln_ds.py", source)
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_STATIC_ONLY" in proc.stdout, (
        f"危险反序列化 sink + 演示应输出 STATIC_ONLY:\n{proc.stdout}"
    )
    assert "VULNERABILITY_CONFIRMED" not in proc.stdout


# ============ ② 无 sink → 无确认标记（not_reproducible 路径不回归） ============

def test_path_traversal_no_sink_emits_no_marker(tmp_path):
    source = "def add(a, b):\n    return a + b\n"
    proc = _run_poc(tmp_path, "path_traversal", "app/clean_pt.py", source)
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_CONFIRMED" not in proc.stdout
    assert "VULNERABILITY_STATIC_ONLY" not in proc.stdout


def test_hardcoded_secret_no_match_emits_no_marker(tmp_path):
    source = "DEBUG = True\nPORT = 8000\n"
    proc = _run_poc(tmp_path, "hardcoded_secret", "app/clean_hs.py", source)
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_CONFIRMED" not in proc.stdout
    assert "VULNERABILITY_STATIC_ONLY" not in proc.stdout


def test_deserialization_no_sink_emits_no_marker(tmp_path):
    source = "def add(a, b):\n    return a + b\n"
    proc = _run_poc(tmp_path, "deserialization", "app/clean_ds.py", source)
    assert proc.returncode == 0, f"PoC 应正常退出: {proc.stderr}"
    assert "VULNERABILITY_CONFIRMED" not in proc.stdout
    assert "VULNERABILITY_STATIC_ONLY" not in proc.stdout


# ============ ③ 标记被既有识别逻辑消费（compute 链路 → static_confirmed） ============

def test_static_markers_recognized_and_derive_static_confirmed():
    """两类静态确认标记都须被 _record_sandbox_attempt 识别为 static_evidence，
    不被当作动态铁证/infra 错误，compute 终态 static_confirmed。"""
    markers = (
        "VULNERABILITY_CONFIRMED(STATIC): path traversal demo - os.path.join "
        "with user input escapes base dir (source-asserted sink)",
        "VULNERABILITY_STATIC_ONLY: hardcoded secret pattern present in source "
        "(1 matches); rotate and move to env/secret manager",
        "VULNERABILITY_STATIC_ONLY: unsafe deserialization sink present and "
        "reachable-input payload demo constructed (no data-flow to target source)",
    )
    for marker in markers:
        agent = _record_agent()
        obs = (
            "沙箱执行结果\n退出码: 0\n标准输出:\n```\n"
            "Source: 200 chars loaded\n"
            f"{marker}\n"
            "=== Verification Complete ===\n```"
        )
        agent._record_sandbox_attempt(
            {"command": "# FINDING_ID:f-t4-1\npython3 /tmp/poc_0.py"},
            obs,
            finding_id="f-t4-1",
        )
        attempt = agent._sandbox_attempts[-1]
        assert attempt["static_evidence"] is True, f"static_evidence 未识别: {marker}"
        assert attempt["success"] is True
        assert attempt["infra_error"] is False, f"静态确认不是 infra 错误: {marker}"
        # 静态证据不得被判为动态铁证（终态上限 static_confirmed，不得 confirmed）
        assert agent._attempt_has_vuln_evidence(attempt) is False, marker

        finding = {
            "title": "demo finding",
            "vulnerability_type": "path_traversal",
            "file_path": "app/vuln.py",
            "line_start": 10,
            "_sandbox_finding_id": "f-t4-1",
        }
        agent._attach_runtime_sandbox_attempts(finding)
        normalized = agent._normalize_verification_outcome(finding)
        assert normalized["verification_status"] == "static_confirmed", (
            f"静态确认标记应推导 static_confirmed: {marker} -> {normalized.get('verification_status')}"
        )
        assert normalized["is_verified"] is True


def test_no_sink_observation_keeps_not_reproducible():
    """无 sink（NO_SINK 文本、无确认标记）→ 不打 static_evidence，终态 not_reproducible。"""
    agent = _record_agent()
    obs = (
        "沙箱执行结果\n退出码: 0\n标准输出:\n```\n"
        "Source: 100 chars loaded\n"
        "NO_SINK: 目标源码未发现路径拼接 sink 关键词，演示性确认不成立\n"
        "=== Verification Complete ===\n```"
    )
    agent._record_sandbox_attempt(
        {"command": "# FINDING_ID:f-t4-ns\npython3 /tmp/poc_0.py"},
        obs,
        finding_id="f-t4-ns",
    )
    attempt = agent._sandbox_attempts[-1]
    assert attempt["static_evidence"] is False
    assert agent._attempt_has_vuln_evidence(attempt) is False
    finding = {
        "title": "demo finding",
        "vulnerability_type": "path_traversal",
        "file_path": "app/clean.py",
        "line_start": 10,
        "_sandbox_finding_id": "f-t4-ns",
    }
    agent._attach_runtime_sandbox_attempts(finding)
    normalized = agent._normalize_verification_outcome(finding)
    assert normalized["verification_status"] == "not_reproducible"
    assert normalized["is_verified"] is False


def test_new_templates_never_emit_bare_confirmation_marker():
    """三模板的确认标记只能是 (STATIC)/STATIC_ONLY 变体，禁止裸 VULNERABILITY_CONFIRMED:。"""
    agent = _agent()
    for vuln_type in ("path_traversal", "hardcoded_secret", "deserialization"):
        cmd = agent._gen_sandbox_command(vuln_type, "app/vuln.py", 10, "t", 0)
        assert cmd is not None
        script = cmd["input"]["command"]
        assert "VULNERABILITY_CONFIRMED:" not in script, (
            f"{vuln_type} 模板不得输出裸确认标记（反伪造规则）"
        )
