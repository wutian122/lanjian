"""Task 2（sandbox-verification-hard-gate）：沙箱执行结果形状对齐与退出码语义。

两个锁定目标：
  ① execute_tool_command 最外层 except 返回的 dict 曾缺 "stdout" 键，
     SandboxTool._execute 访问 result["stdout"] 时 KeyError，被 base.py
     最外层 except 吞成"工具执行异常" observation——LLM 路径沙箱失败
     观测性断裂。失败 dict 必须与正常返回同构（success/error/stdout/
     stderr/exit_code 五键齐全）。
  ② exit_code 语义：None = 命令未进容器（Docker 不可用/容器创建失败/
     daemon 中断），-1 = 进过容器但超时被 kill。确定性路径曾对 infra
     失败合成 -1，_format_sandbox_result 渲染"退出码: -1"，
     _record_sandbox_attempt 据此判 ran_in_container=True，抑制
     connection 类 infra 签名（daemon 中断窄时序漏判）。None 时渲染层
     不输出"退出码"行，正则解析不到 → ran_in_container=False。
"""
import asyncio
import os

import pytest

from app.services.agent.tools.sandbox_tool import SandboxManager, SandboxTool
from app.services.agent.tools.sandbox_language import ShellTestTool, PythonTestTool
from app.services.agent.agents.verification import (
    VerificationAgent,
    compute_verification_status,
)


# ---------- 测试夹具 ----------

class _FakeContainer:
    """模拟 Docker 容器：wait/logs/kill/remove 均为同步方法（生产经 to_thread 调用）。"""

    def __init__(self, status_code: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self._status_code = status_code
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False
        self.removed = False

    def wait(self):
        return {"StatusCode": self._status_code}

    def logs(self, stdout=True, stderr=False):
        return self._stdout if stdout else self._stderr

    def kill(self):
        self.killed = True

    def remove(self, force=False):
        self.removed = True


class _FakeContainers:
    def __init__(self, *, run_raises: Exception = None, container: _FakeContainer = None):
        self._run_raises = run_raises
        self._container = container

    def run(self, **kwargs):
        if self._run_raises is not None:
            raise self._run_raises
        return self._container


class _FakeDockerClient:
    def __init__(self, containers: _FakeContainers):
        self.containers = containers


def _manager_with_client(fake_client) -> SandboxManager:
    """构造 is_available=True 的 SandboxManager（跳过真实 initialize）。"""
    mgr = SandboxManager()
    mgr._docker_client = fake_client
    mgr._initialized = True
    return mgr


def _unavailable_manager() -> SandboxManager:
    """构造 is_available=False 的 SandboxManager（Docker 不可用）。"""
    mgr = SandboxManager()
    mgr._docker_client = None
    mgr._initialized = True
    return mgr


def _make_verification_agent() -> VerificationAgent:
    """与 test_infra_error_separation 同构的最小 VerificationAgent。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._sandbox_attempts = []
    agent._runtime_attempts_by_finding_id = {}

    class _Cfg:
        name = "Verification"

    agent.config = _Cfg()
    return agent


_FAILURE_KEYS = {"success", "error", "stdout", "stderr", "exit_code"}


# ---------- ① KeyError 回归锁定：异常返回必须含 stdout/stderr ----------

@pytest.mark.asyncio
async def test_execute_tool_command_exception_return_contains_stdout_stderr(tmp_path):
    """containers.run 抛异常（daemon 中断）→ execute_tool_command 返回 dict
    必须含 stdout/stderr 键（修复前缺 stdout → SandboxTool._execute KeyError）。"""
    fake_client = _FakeDockerClient(
        _FakeContainers(run_raises=Exception("Connection aborted. FileNotFoundError(2)"))
    )
    mgr = _manager_with_client(fake_client)

    result = await mgr.execute_tool_command(
        command="python3 app.py",
        host_workdir=str(tmp_path),
        timeout=5,
    )

    assert result["success"] is False
    assert "stdout" in result, "异常返回缺 stdout 键——SandboxTool._execute 会 KeyError"
    assert "stderr" in result, "异常返回缺 stderr 键"
    assert result["stdout"] == ""
    assert result["stderr"] == ""
    assert "Connection aborted" in result["error"]


@pytest.mark.asyncio
async def test_sandbox_tool_execute_no_keyerror_on_infra_failure(tmp_path):
    """端到端：sandbox_exec 遇容器创建失败不得 KeyError，observation 必须携带
    错误文本（而非 base.py 兜底的"工具执行异常"），且不渲染退出码行。"""
    fake_client = _FakeDockerClient(
        _FakeContainers(run_raises=Exception("Connection aborted. Docker daemon down"))
    )
    mgr = _manager_with_client(fake_client)
    tool = SandboxTool(mgr, str(tmp_path))

    result = await tool._execute(command="python3 app.py", timeout=5)

    assert result.success is False
    assert result.data is not None
    assert "Connection aborted" in result.data
    assert "错误" in result.data
    assert "工具执行异常" not in result.data, "KeyError 被 base.py 吞成工具异常即回归"
    assert "退出码" not in result.data, "未进容器不得渲染退出码行"
    assert result.metadata["exit_code"] is None


# ---------- 返回结构逐键对齐（三函数全部失败/成功路径） ----------

@pytest.mark.asyncio
async def test_all_failure_dicts_have_uniform_key_set(tmp_path):
    """三个执行函数的所有失败返回 dict 键集必须一致（防手写 dict 再漂移）。"""
    raising_client = _FakeDockerClient(
        _FakeContainers(run_raises=Exception("boom"))
    )

    # 顺序 await 收集（不提前创建协程，避免断言失败时 never-awaited 警告）
    cases = []

    # is_available=False 短路
    unavail = _unavailable_manager()
    cases.append(("command/unavailable",
                  await unavail.execute_command(command="true", timeout=5)))
    cases.append(("tool_command/unavailable",
                  await unavail.execute_tool_command(command="true", host_workdir=str(tmp_path), timeout=5)))
    cases.append(("with_files/unavailable",
                  await unavail.execute_with_files(command="true", host_project_dir=str(tmp_path), timeout=5)))

    # 容器创建/daemon 异常（最外层 except）
    mgr = _manager_with_client(raising_client)
    cases.append(("command/run_raises", await mgr.execute_command(command="true", timeout=5)))
    cases.append(("tool_command/run_raises",
                  await mgr.execute_tool_command(command="true", host_workdir=str(tmp_path), timeout=5)))
    cases.append(("with_files/run_raises",
                  await mgr.execute_with_files(command="true", host_project_dir=str(tmp_path), timeout=5)))

    # 工作目录校验失败（容器未创建）
    cases.append(("tool_command/dir_missing",
                  await mgr.execute_tool_command(command="true", host_workdir=str(tmp_path / "nope"), timeout=5)))
    cases.append(("with_files/dir_missing",
                  await mgr.execute_with_files(command="true", host_project_dir=str(tmp_path / "nope"), timeout=5)))
    cases.append(("tool_command/invalid_workdir",
                  await mgr.execute_tool_command(command="true", host_workdir=os.path.abspath(os.sep), timeout=5)))

    for label, result in cases:
        assert set(result.keys()) == _FAILURE_KEYS, (
            f"{label} 返回键集漂移: {set(result.keys()) ^ _FAILURE_KEYS}"
        )
        assert result["success"] is False
        assert result["stdout"] == "" and result["stderr"] == ""


@pytest.mark.asyncio
async def test_success_dicts_have_uniform_key_set(tmp_path):
    """正常执行路径返回 dict 同样五键齐全（error=None），exit_code 为真实容器退出码。"""
    container = _FakeContainer(status_code=0, stdout=b"ok\n", stderr=b"")
    mgr = _manager_with_client(_FakeDockerClient(_FakeContainers(container=container)))

    r1 = await mgr.execute_command(command="echo ok", timeout=5)
    assert set(r1.keys()) == _FAILURE_KEYS
    assert r1["exit_code"] == 0 and r1["success"] is True and r1["error"] is None

    r2 = await mgr.execute_with_files(command="echo ok", host_project_dir=str(tmp_path), timeout=5)
    assert set(r2.keys()) == _FAILURE_KEYS
    assert r2["exit_code"] == 0 and r2["success"] is True and r2["error"] is None

    r3 = await mgr.execute_tool_command(command="echo ok", host_workdir=str(tmp_path), timeout=5)
    assert set(r3.keys()) == _FAILURE_KEYS
    assert r3["exit_code"] == 0 and r3["success"] is True and r3["error"] is None

    # 非零退出码语义不变
    container1 = _FakeContainer(status_code=1, stderr=b"failed\n")
    mgr1 = _manager_with_client(_FakeDockerClient(_FakeContainers(container=container1)))
    r4 = await mgr1.execute_with_files(command="false", host_project_dir=str(tmp_path), timeout=5)
    assert r4["exit_code"] == 1 and r4["success"] is False


# ---------- ② infra 失败 exit_code=None 语义与 connection 签名链路 ----------

@pytest.mark.asyncio
async def test_unavailable_short_circuit_returns_exit_code_none(tmp_path):
    """is_available=False 短路：三函数 exit_code 均为 None（未进容器）。"""
    mgr = _unavailable_manager()

    for result in (
        await mgr.execute_command(command="true", timeout=5),
        await mgr.execute_tool_command(command="true", host_workdir=str(tmp_path), timeout=5),
        await mgr.execute_with_files(command="true", host_project_dir=str(tmp_path), timeout=5),
    ):
        assert result["exit_code"] is None
        assert result["success"] is False


@pytest.mark.asyncio
async def test_format_sandbox_result_skips_exit_line_for_none():
    """_format_sandbox_result：exit_code=None 时不渲染"退出码"行（下游正则
    据此判 ran_in_container=False）；错误行正常渲染。"""
    agent = _make_verification_agent()

    text = agent._format_sandbox_result(
        {"success": False, "error": "Docker not available",
         "stdout": "", "stderr": "", "exit_code": None}
    )
    assert "退出码" not in text
    assert "错误: Docker not available" in text

    # 真实退出码仍正常渲染（正常路径语义不变）
    text_ok = agent._format_sandbox_result(
        {"success": True, "error": None,
         "stdout": "done", "stderr": "", "exit_code": 0}
    )
    assert "退出码: 0" in text_ok


@pytest.mark.asyncio
async def test_daemon_connection_aborted_chain_marks_infra_error(tmp_path):
    """Task 1 review Important ① 回归锁：is_available=True 后 daemon 中断，
    containers.run 抛 "Connection aborted" → 外层 except exit_code=None →
    格式化无"退出码"行 → _record_sandbox_attempt 判 ran_in_container=False →
    connection 签名生效 → infra_error=True。
    修复前：exit_code=-1 渲染"退出码: -1" → ran_in_container=True → 签名抑制 → 漏判。"""
    fake_client = _FakeDockerClient(
        _FakeContainers(run_raises=Exception(
            "Error while fetching server API version: ('Connection aborted.', "
            "FileNotFoundError(2, 'No such file or directory'))"
        ))
    )
    mgr = _manager_with_client(fake_client)
    result = await mgr.execute_with_files(
        command="python3 /tmp/poc_ssrf.py",
        host_project_dir=str(tmp_path),
        timeout=5,
    )
    assert result["exit_code"] is None

    agent = _make_verification_agent()
    observation = agent._format_sandbox_result(result)
    assert "退出码" not in observation

    agent._record_sandbox_attempt({"command": "python3 /tmp/poc_ssrf.py"}, observation)
    attempt = agent._sandbox_attempts[-1]
    assert attempt["exit_code"] is None, "无退出码行时解析应为 None（ran_in_container=False）"
    assert attempt["infra_error"] is True, "daemon 中断必须判 infra_error，不得伪装未复现"


@pytest.mark.asyncio
async def test_unavailable_docker_layer_signature_chain_marks_infra_error(tmp_path):
    """is_available=False 确定性路径：exit_code=None + "Docker not available"
    docker 层签名 → infra_error=True（与 Task 1 签名链一致）。"""
    mgr = _unavailable_manager()
    result = await mgr.execute_with_files(
        command="python3 /tmp/poc.py", host_project_dir=str(tmp_path), timeout=5
    )
    assert result["exit_code"] is None

    agent = _make_verification_agent()
    observation = agent._format_sandbox_result(result)
    agent._record_sandbox_attempt({"command": "python3 /tmp/poc.py"}, observation)
    attempt = agent._sandbox_attempts[-1]
    assert attempt["exit_code"] is None
    assert attempt["infra_error"] is True


# ---------- ③ 超时路径保留 -1（进过容器，ran_in_container=True） ----------

@pytest.mark.asyncio
async def test_timeout_keeps_exit_code_minus_one(tmp_path, monkeypatch):
    """容器已创建、命令执行后超时被 kill：exit_code 保持 -1（语义=进过容器但
    无退出码），渲染"退出码: -1"，下游判 ran_in_container=True，不误判 infra。"""

    async def _fake_wait_for(coro, timeout):
        # 不 await to_thread 协程（fake 直接抛超时），关闭避免 never-awaited 警告
        close = getattr(coro, "close", None)
        if close is not None:
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

    container = _FakeContainer()
    mgr = _manager_with_client(_FakeDockerClient(_FakeContainers(container=container)))

    result = await mgr.execute_with_files(
        command="sleep 100", host_project_dir=str(tmp_path), timeout=1
    )
    assert result["exit_code"] == -1
    assert result["success"] is False
    assert container.killed is True

    agent = _make_verification_agent()
    observation = agent._format_sandbox_result(result)
    assert "退出码: -1" in observation

    agent._record_sandbox_attempt({"command": "sleep 100"}, observation)
    attempt = agent._sandbox_attempts[-1]
    assert attempt["exit_code"] == -1, "超时路径进过容器，ran_in_container 应为 True"
    assert attempt["infra_error"] in (None, False), "超时不是基础设施故障"

# ---------- 语言测试工具（sandbox_language.py）渲染对齐 ----------
# review 第 1 轮 Important：六个语言工具渲染块（基类 Shell/PHP 继承 + Python/
# JavaScript/Java/Go/Ruby 重写）在 exit_code=None 时曾渲染 "退出码: None" 泄漏，
# 且 error 键（daemon 报错）从不渲染——stderr 为空时 observation 既无 "\n错误:"
# 行（_has_sandbox_failure_marker 不命中 → success 误翻 True）也无 connection
# 签名文本（_is_infra_error 漏判）。修复：None 不渲染退出码行 + 补 "错误:" 行。

_DAEMON_DOWN_ERROR = (
    "Error while fetching server API version: ('Connection aborted.', "
    "FileNotFoundError(2, 'No such file or directory'))"
)


def _lang_finding(**overrides):
    f = {
        "title": "RCE in eval",
        "vulnerability_type": "rce",
        "file_path": "app/main.py",
        "line_start": 10,
        "verification_method": "sandbox_exec",
    }
    f.update(overrides)
    return f


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_cls,tool_name,code",
    [
        (ShellTestTool, "shell_test", "id"),       # 基类 _execute（Shell/PHP 继承）
        (PythonTestTool, "python_test", "print('x')"),  # 重写 _execute
    ],
)
async def test_language_tools_infra_failure_renders_error_without_exit_code(
    tool_cls, tool_name, code, tmp_path
):
    """daemon 中断（is_available=True 后 containers.run 抛 Connection aborted）：
    observation 不得含退出码行（None 不渲染），必须含"错误:"行 + connection 签名；
    经 _record_language_test_attempt 录得 exit_code=None/success=False/infra_error=True，
    状态机判 needs_context。"""
    fake_client = _FakeDockerClient(_FakeContainers(run_raises=Exception(_DAEMON_DOWN_ERROR)))
    mgr = _manager_with_client(fake_client)
    tool = tool_cls(mgr, str(tmp_path))

    result = await tool._execute(code=code, timeout=5)

    # 语言工具硬编码 ToolResult(success=True)，base.py 以 data 原文为 observation
    observation = str(result.data)
    assert "退出码" not in observation, "exit_code=None 不得渲染退出码行（防'退出码: None'泄漏）"
    assert "错误:" in observation, "error 键必须渲染为'错误:'行，failure marker 才能命中"
    assert "Connection aborted" in observation, (
        "daemon 报错文本必须进 observation，connection 类 infra 签名才能命中"
    )
    assert result.metadata["exit_code"] is None

    agent = _make_verification_agent()
    agent._record_language_test_attempt(tool_name, {"code": code}, observation)
    attempt = agent._sandbox_attempts[-1]
    assert attempt["exit_code"] is None
    assert attempt["success"] is False, "无'错误:'行时 success 误翻 True——错误行渲染是闭环关键"
    assert attempt["infra_error"] is True, "daemon 中断必须判 infra_error"

    finding = _lang_finding(sandbox_attempts=[attempt])
    status, is_verified, notes = compute_verification_status(
        finding,
        [attempt],
        attempt_has_vuln_evidence_fn=lambda a: False,
        attempt_matches_finding_fn=lambda a, f: False,
    )
    assert status == "needs_context", f"全 infra 应判 needs_context，got {status}"
    assert is_verified is False
    assert notes.get("infra_error") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_cls", [ShellTestTool, PythonTestTool])
async def test_language_tools_unavailable_early_return_chain(tool_cls, tmp_path):
    """is_available=False 早退回归：ToolResult(success=False, error 含'沙箱环境不可用')，
    经 base.py observation 形态（工具执行失败 + 错误文本）录得 infra_error=True。"""
    mgr = _unavailable_manager()
    tool = tool_cls(mgr, str(tmp_path))

    result = await tool._execute(code="id", timeout=5)

    assert result.success is False
    assert "沙箱环境不可用" in (result.error or "")

    # 复现 base.py:1576 失败 observation 形态
    observation = f"⚠️ 工具执行失败\n\n**工具**: {tool.name}\n**错误**: {result.error}"
    agent = _make_verification_agent()
    agent._record_language_test_attempt(tool.name, {"code": "id"}, observation)
    attempt = agent._sandbox_attempts[-1]
    assert attempt["infra_error"] is True
    assert attempt["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_cls,tool_name,code",
    [
        (ShellTestTool, "shell_test", "id"),
        (PythonTestTool, "python_test", "print('x')"),
    ],
)
async def test_language_tools_normal_path_renders_exit_code_zero(
    tool_cls, tool_name, code, tmp_path
):
    """正常执行（StatusCode=0 + stdout）：退出码行正常渲染"退出码: 0"，语义不变。"""
    container = _FakeContainer(status_code=0, stdout=b"uid=0(root) gid=0(root)\n", stderr=b"")
    mgr = _manager_with_client(_FakeDockerClient(_FakeContainers(container=container)))
    tool = tool_cls(mgr, str(tmp_path))

    result = await tool._execute(code=code, timeout=5)

    observation = str(result.data)
    assert "退出码: 0" in observation
    assert result.metadata["exit_code"] == 0

    agent = _make_verification_agent()
    agent._record_language_test_attempt(tool_name, {"code": code}, observation)
    attempt = agent._sandbox_attempts[-1]
    assert attempt["exit_code"] == 0
