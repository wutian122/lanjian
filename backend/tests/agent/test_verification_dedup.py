"""
C 修复测试：Verification Agent 工具调用去重 key 归一化

根因：verification.py:970 用 json.dumps(sort_keys=True) 精确匹配做重复
      调用检测，LLM 微调输入（空格/换行/变量名）即产生不同 key 绕过去重，
      导致同一 PoC 被执行十几次。修复后 sandbox_exec 的去重 key 对空白
      归一化，非 sandbox_exec 保持原逻辑。
"""
import pytest

from app.services.agent.agents.verification import _normalize_tool_key


class TestNormalizeToolKey:
    """验证去重 key 归一化逻辑"""

    def test_dedup_key_normalizes_whitespace(self):
        """同一 PoC 命令的空白变体，归一化后 key 必须相同"""
        cmd1 = "python3 -c 'print(\"VULNERABILITY_CONFIRMED\")'"
        cmd2 = "python3  -c  'print(\"VULNERABILITY_CONFIRMED\")'"  # 多空格
        cmd3 = "python3\t-c\t'print(\"VULNERABILITY_CONFIRMED\")'"  # tab
        cmd4 = "python3 -c\n  'print(\"VULNERABILITY_CONFIRMED\")'"  # 换行+缩进
        k1 = _normalize_tool_key("sandbox_exec", {"command": cmd1})
        k2 = _normalize_tool_key("sandbox_exec", {"command": cmd2})
        k3 = _normalize_tool_key("sandbox_exec", {"command": cmd3})
        k4 = _normalize_tool_key("sandbox_exec", {"command": cmd4})
        assert k1 == k2 == k3 == k4, (
            f"空白变体应归一化为同一 key，实际 {k1!r} != {k2!r} != {k3!r} != {k4!r}"
        )

    def test_dedup_key_different_commands_differ(self):
        """不同漏洞的 PoC 命令，key 必须不同"""
        k1 = _normalize_tool_key("sandbox_exec", {"command": "print('vuln1')"})
        k2 = _normalize_tool_key("sandbox_exec", {"command": "print('vuln2')"})
        assert k1 != k2

    def test_dedup_key_truncates_long_command(self):
        """超长命令截断到 500 字符，避免 key 过长且前后差异段被截断后归一"""
        long_cmd = "python3 -c " + "a" * 800
        k = _normalize_tool_key("sandbox_exec", {"command": long_cmd})
        # key 形如 sandbox_exec:<normalized>，截断后长度可控
        assert k.startswith("sandbox_exec:")
        # 前缀 14 + 500 = 514，留余量
        assert len(k) < 520

    def test_dedup_key_truncation_500_boundary(self):
        """共享前 500 字符、尾部不同的两条长命令会被截断为同一 key（已知权衡）"""
        prefix = "python3 -c " + "a" * 490
        cmd_a = prefix + " payload_a_tail"
        cmd_b = prefix + " payload_b_tail"
        k1 = _normalize_tool_key("sandbox_exec", {"command": cmd_a})
        k2 = _normalize_tool_key("sandbox_exec", {"command": cmd_b})
        # 两条尾部不同但前 500 字符相同 -> 截断后归一为同一 key（这是防 key 膨胀的已知权衡）
        assert k1 == k2
        assert len(k1) < 520

    def test_dedup_key_non_sandbox_unchanged(self):
        """非 sandbox_exec 工具保持原 JSON key 逻辑"""
        k = _normalize_tool_key("read_file", {"path": "/a/b.py"})
        assert k.startswith("read_file:")
        assert "/a/b.py" in k

    def test_dedup_key_non_sandbox_order_independent(self):
        """非 sandbox 工具的 dict key 顺序不影响去重"""
        k1 = _normalize_tool_key("read_file", {"path": "/a.py", "offset": 0})
        k2 = _normalize_tool_key("read_file", {"offset": 0, "path": "/a.py"})
        assert k1 == k2

    def test_dedup_key_sandbox_missing_command_field(self):
        """sandbox_exec 但无 command 字段时不报错"""
        k = _normalize_tool_key("sandbox_exec", {})
        assert k.startswith("sandbox_exec:")
