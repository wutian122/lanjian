"""
根因1 修复测试：沙箱项目挂载路径统一 + /workspace/ 误判修复

根因：
- execute_tool_command 挂载到 /workspace，execute_with_files 挂载到 /workspace/src，
  PoC 脚本硬编码 /workspace/src/，导致 execute_tool_command 路径下找不到文件。
- _command_needs_project_mount 中 '/workspace/' in command 误判为不挂载，
  PoC 命令含 /workspace/src/ 时项目根本没挂载。
修复：execute_tool_command 挂载到 /workspace/src；_command_needs_project_mount
      仅 heredoc 不挂载，含 /workspace/ 仍挂载。
"""
import pytest
from app.services.agent.tools.sandbox_tool import SandboxTool


class TestCommandNeedsProjectMount:
    """验证 _command_needs_project_mount 修复"""

    def test_needs_mount_with_workspace_path(self):
        """根因1: 含 /workspace/src/ 的命令应返回 True（需挂载项目读文件）"""
        cmd = "python3 /tmp/poc_0.py"  # 不含 /workspace/
        # PoC 脚本内部引用 /workspace/src/，但命令本身是 python3 跑脚本
        # 这种情况命令含 .py 文件引用，应返回 True
        assert SandboxTool._command_needs_project_mount(cmd) is True

    def test_needs_mount_with_direct_workspace_ref(self):
        """根因1: 命令直接含 /workspace/ 路径引用时应返回 True（修复后）"""
        cmd = "cat /workspace/src/auth.py"
        # 修复前：含 /workspace/ 返回 False（不挂载，导致找不到文件）
        # 修复后：返回 True（需挂载）
        assert SandboxTool._command_needs_project_mount(cmd) is True

    def test_needs_mount_heredoc(self):
        """根因1: heredoc 写入命令返回 False（不挂载）"""
        cmd = "cat > /tmp/poc.py << 'EOF'\nprint('x')\nEOF\npython3 /tmp/poc.py"
        assert SandboxTool._command_needs_project_mount(cmd) is False

    def test_needs_mount_inline_python(self):
        """根因1: python3 -c 内联命令返回 False（不挂载）"""
        cmd = 'python3 -c "print(1+1)"'
        assert SandboxTool._command_needs_project_mount(cmd) is False


class TestExecuteToolCommandMount:
    """验证 execute_tool_command 挂载点（通过容器配置 mock）"""

    def test_execute_tool_command_mounts_workspace_src(self):
        """根因1: execute_tool_command 挂载到 /workspace/src"""
        import inspect
        from app.services.agent.tools.sandbox_tool import SandboxManager
        src = inspect.getsource(SandboxManager.execute_tool_command)
        # 挂载点应为 /workspace/src，不再是 /workspace
        assert "/workspace/src" in src
        # 不应再保留 /workspace 作为挂载点（working_dir 除外）
        assert '"bind": "/workspace"' not in src
