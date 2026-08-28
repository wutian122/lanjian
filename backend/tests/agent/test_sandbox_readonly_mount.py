"""#3 修复回归测试：项目源码挂载一律只读。

E2E 实证（docker inspect）：两种挂载形态并存——模板 PoC 路径 src ro（execute_with_files），
外部工具/LLM sandbox_exec 路径 src rw（execute_tool_command）。源码只读加固未覆盖全部
路径：Semgrep 等 8 处外部工具调用与 LLM 主动验证都走 rw 挂载，违反最小权限。

修复：execute_tool_command 的项目源码挂载改 ro；只读约束写入 audit-engine 规格。
"""
import inspect

from app.services.agent.tools.sandbox_tool import SandboxManager


class TestSourceMountReadOnly:
    def test_execute_tool_command_src_mount_read_only(self):
        """外部工具/LLM sandbox_exec 路径：项目源码挂载必须只读。"""
        src = inspect.getsource(SandboxManager.execute_tool_command)
        assert 'host_workdir: {"bind": "/workspace/src", "mode": "ro"}' in src, (
            "execute_tool_command 的项目源码挂载必须为 ro（#3 修复）"
        )
        assert 'host_workdir: {"bind": "/workspace/src", "mode": "rw"}' not in src

    def test_execute_with_files_src_mount_still_read_only(self):
        """确定性 PoC/模板 PoC 路径：只读语义不回退。"""
        src = inspect.getsource(SandboxManager.execute_with_files)
        assert 'host_project_dir: {"bind": "/workspace/src", "mode": "ro"}' in src
