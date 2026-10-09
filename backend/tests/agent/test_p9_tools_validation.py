"""P9 T2 工具组修复测试（2026-10-08）：

P9-2 R1 必填校验重写（tools/base.py execute()）：
  is_required() 优先（pydantic v2）、getattr(finfo,"required",False) v1 回退；
  缺字段时 _execute 不被调用、stats 计数递增；统一文案含「参数示例」；
  key 存在值为 None/空串不拦（走业务校验）；自省异常放行。

P9-3 映射参数形状防御（coerce_mapping_arg）：
  None→None；dict 原样；JSON 字符串还原 dict；list/坏文本/数字→None（调用方引导）；
  接入 sandbox_language 基类 + 五叶子 + Universal + sandbox_tool PhpTestTool + HTTP headers；
  非 str 值 str 化，杜绝下游 .lower()/.replace() AttributeError。

wrapper 注入安全（第七章防护）：
  key 含引号/分号、value 尾反斜杠不再逃逸出语言字面量、无法伪造 stdout 的 uid= 标记。

files_read 饿死防护（第七章）：
  执行失败的 read_file/search_code 不计入跨轮上报，成功才计入。
"""
import asyncio
import contextlib
import io
import json
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

from app.services.agent.tools.base import AgentTool, ToolResult

# ============ 公共 FakeMgr ============

class FakeMgr:
    """记录 initialize 调用与下发命令的假沙箱管理器。"""

    def __init__(self, stdout: str = "", exit_code: int = 0):
        self.init_called = 0
        self.commands = []
        self.is_available = True
        self._stdout = stdout
        self._exit_code = exit_code

    async def initialize(self):
        self.init_called += 1

    async def execute_command(self, command, timeout=30, env=None, **kwargs):
        self.commands.append({"command": command, "env": env})
        return {
            "success": self._exit_code == 0,
            "stdout": self._stdout,
            "stderr": "",
            "exit_code": self._exit_code,
            "error": None,
        }


# ============ P9-2: R1 必填校验 ============

class _SpyInput(BaseModel):
    file_path: str = Field(description="必填路径")


class _SpyTool(AgentTool):
    def __init__(self):
        super().__init__()
        self.executed = False
        self.received: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "spy_tool"

    @property
    def description(self) -> str:
        return "spy"

    @property
    def args_schema(self):
        return _SpyInput

    async def _execute(self, **kwargs):
        self.executed = True
        self.received = kwargs
        if not kwargs.get("file_path"):
            return ToolResult(success=False, error="业务校验: file_path 不能为空")
        return ToolResult(success=True, data="ok")


class TestR1Spy:
    def test_missing_field_blocks_execute_and_counts(self):
        """缺必填字段：_execute 未被调用、call_count 递增、文案含「参数示例」。"""
        tool = _SpyTool()
        result = asyncio.run(tool.execute())

        assert result.success is False
        assert tool.executed is False
        assert result.error.startswith("必填参数缺失: file_path")
        assert "参数示例" in result.error
        assert tool.stats["call_count"] == 1
        assert result.duration_ms >= 0

    def test_missing_field_error_text_full_guidance(self):
        """文案须与 P9-1c 短路特征一致（含 JSON 对象要求与 list_files 指引）。"""
        tool = _SpyTool()
        result = asyncio.run(tool.execute())
        text = result.error or ""
        assert "工具调用参数必须是完整 JSON 对象" in text
        assert "禁止空参数或省略字段" in text
        assert '{"file_path": "<实际值>", ...}' in text
        assert "list_files/search_code" in text


class _TwoFieldInput(BaseModel):
    a: str = Field(description="必填 a")
    b: int = Field(default=3, description="可选 b")


class _FullTool(AgentTool):
    def __init__(self):
        super().__init__()
        self.executed = False
        self.received: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "full_tool"

    @property
    def description(self) -> str:
        return "full"

    @property
    def args_schema(self):
        return _TwoFieldInput

    async def _execute(self, **kwargs):
        self.executed = True
        self.received = kwargs
        if not kwargs.get("a"):
            return ToolResult(success=False, error="业务校验: a 不能为空")
        return ToolResult(success=True, data="ok")


class TestR1FullPicture:
    def test_all_missing(self):
        tool = _FullTool()
        result = asyncio.run(tool.execute())
        assert result.success is False
        assert tool.executed is False
        assert result.error.startswith("必填参数缺失: a")

    def test_single_required_missing_optional_present(self):
        """只缺必填 a、可选 b 已给 → 仍拦截点名 a。"""
        tool = _FullTool()
        result = asyncio.run(tool.execute(b=5))
        assert result.success is False
        assert tool.executed is False
        assert "必填参数缺失: a" in result.error

    def test_required_present_optional_defaulted(self):
        tool = _FullTool()
        result = asyncio.run(tool.execute(a="x"))
        assert result.success is True
        assert tool.executed is True
        assert tool.received == {"a": "x"}

    def test_all_fields_present(self):
        tool = _FullTool()
        result = asyncio.run(tool.execute(a="x", b=9))
        assert result.success is True
        assert tool.received == {"a": "x", "b": 9}

    def test_extra_fields_pass_through(self):
        """多余字段不拦（透传 _execute）。"""
        tool = _FullTool()
        result = asyncio.run(tool.execute(a="x", c="z"))
        assert result.success is True
        assert tool.received.get("c") == "z"

    def test_none_value_not_blocked(self):
        """key 存在值为 None 不拦（None 与缺失语义不同，业务校验负责）。"""
        tool = _FullTool()
        result = asyncio.run(tool.execute(a=None))
        assert tool.executed is True
        assert result.success is False
        assert result.error == "业务校验: a 不能为空"

    def test_empty_string_value_goes_business_validation(self):
        """file_path/a 空串不被 R1 拦，走业务校验。"""
        tool = _FullTool()
        result = asyncio.run(tool.execute(a=""))
        assert tool.executed is True
        assert result.success is False
        assert result.error.startswith("业务校验")

    def test_every_call_counted(self):
        """成功与拦截的调用均计入 call_count。"""
        tool = _FullTool()
        asyncio.run(tool.execute())
        asyncio.run(tool.execute(a="x"))
        assert tool.stats["call_count"] == 2


class _V1Finfo:
    """模拟 pydantic v1 ModelField：无 is_required，有 required 布尔属性。"""
    required = True


class _V1Schema:
    model_fields = {"x": _V1Finfo()}


class _V1Tool(AgentTool):
    def __init__(self):
        super().__init__()
        self.executed = False

    @property
    def name(self) -> str:
        return "v1_tool"

    @property
    def description(self) -> str:
        return "v1"

    @property
    def args_schema(self):
        return _V1Schema

    async def _execute(self, **kwargs):
        self.executed = True
        return ToolResult(success=True, data="ok")


class TestR1V1Fallback:
    def test_v1_required_attribute_blocks(self):
        tool = _V1Tool()
        result = asyncio.run(tool.execute())
        assert result.success is False
        assert tool.executed is False
        assert "必填参数缺失: x" in result.error

    def test_v1_present_passes(self):
        tool = _V1Tool()
        result = asyncio.run(tool.execute(x="v"))
        assert result.success is True
        assert tool.executed is True


class TestR1IntrospectionFailurePassThrough:
    def test_schema_introspection_exception_passes_to_execute(self):
        """自省过程抛异常时保留原行为（放行到 _execute），不能因校验异常阻断业务。"""
        tool = _FullTool()

        class _BoomSchema:
            @property
            def model_fields(self):
                raise RuntimeError("boom")

        # args_schema 是 property；直接替换 property 底层
        type(tool).args_schema = property(lambda self: _BoomSchema())
        try:
            result = asyncio.run(tool.execute(a="x"))
            assert result.success is True
            assert tool.executed is True
        finally:
            # 恢复，避免污染同文件后续用例
            type(tool).args_schema = property(lambda self: _TwoFieldInput)


# ============ P9-3: coerce_mapping_arg ============

class TestCoerceMappingArg:
    def test_none_returns_none(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg(None, "params") is None

    def test_dict_passthrough_identity(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        d = {"a": "b"}
        assert coerce_mapping_arg(d, "params") is d

    def test_json_string_restored(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg('  {"a": "b"}  ', "params") == {"a": "b"}

    def test_empty_object_string_restored(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg("{}", "params") == {}

    def test_json_array_returns_none(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg('["a", "b"]', "params") is None

    def test_bad_plain_text_returns_none(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg("id", "params") is None

    def test_list_value_returns_none(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg(["a"], "params") is None

    def test_int_value_returns_none(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg(5, "params") is None

    def test_non_object_json_scalar_returns_none(self):
        from app.services.agent.tools.base import coerce_mapping_arg
        assert coerce_mapping_arg("5", "params") is None
        assert coerce_mapping_arg("\"x\"", "params") is None


# ============ P9-3: 六语言叶子坏参数（initialize 未被调） ============

CODE_SNIPPET = "print('hi')"


class TestLeafBadParams:
    def _bad_case(self, tool_cls, bad_value, field="params"):
        mgr = FakeMgr()
        tool = tool_cls(mgr, ".")
        kwargs = {"code": CODE_SNIPPET, field: bad_value}
        result = asyncio.run(tool.execute(**kwargs))
        assert result.success is False
        assert mgr.init_called == 0, "坏参数必须在 initialize 之前返回引导"
        assert f"{field} 必须是 JSON 对象" in result.error
        assert "参数示例" in result.error
        return result

    def test_python_bad_params_string(self):
        from app.services.agent.tools.sandbox_language import PythonTestTool
        self._bad_case(PythonTestTool, "id")

    def test_python_bad_env_vars(self):
        from app.services.agent.tools.sandbox_language import PythonTestTool
        self._bad_case(PythonTestTool, "bad", field="env_vars")

    def test_js_bad_params_string(self):
        from app.services.agent.tools.sandbox_language import JavaScriptTestTool
        self._bad_case(JavaScriptTestTool, "id")

    def test_java_bad_params_list(self):
        from app.services.agent.tools.sandbox_language import JavaTestTool
        self._bad_case(JavaTestTool, ["a"])

    def test_go_bad_params_string(self):
        from app.services.agent.tools.sandbox_language import GoTestTool
        self._bad_case(GoTestTool, "x")

    def test_ruby_bad_params_string(self):
        from app.services.agent.tools.sandbox_language import RubyTestTool
        self._bad_case(RubyTestTool, "x")

    def test_php_base_bad_params(self):
        """sandbox_language 的 PhpTestTool 走基类 _execute。"""
        from app.services.agent.tools.sandbox_language import PhpTestTool
        self._bad_case(PhpTestTool, "id")


# ============ P9-3: 各语言还原成功（无 AttributeError、命令真实下发） ============

class TestLeafRestoreSuccess:
    def _run(self, tool_cls, params, **extra):
        mgr = FakeMgr()
        tool = tool_cls(mgr, ".")
        result = asyncio.run(tool.execute(
            code=CODE_SNIPPET, params=params, **extra
        ))
        assert result.success is True
        assert mgr.init_called == 1
        assert len(mgr.commands) == 1
        return mgr.commands[0]["command"]

    def test_python_restore_and_numeric_stringify(self):
        from app.services.agent.tools.sandbox_language import PythonTestTool
        # JSON 字符串还原
        cmd = self._run(PythonTestTool, '{"a": "1"}')
        assert "os.environ[\"A\"] = \"1\"" in cmd
        # 数字值 str 化（dict 直传，防 value.lower 型 AttributeError）
        cmd2 = self._run(PythonTestTool, {"a": 1})
        assert "os.environ[\"A\"] = \"1\"" in cmd2

    def test_js_restore(self):
        from app.services.agent.tools.sandbox_language import JavaScriptTestTool
        cmd = self._run(JavaScriptTestTool, '{"id": "1"}')
        assert '"id": "1"' in cmd

    def test_java_restore(self):
        from app.services.agent.tools.sandbox_language import JavaTestTool
        cmd = self._run(JavaTestTool, '{"a": "1"}')
        # JSON 转义片段嵌入 Java 字面量
        assert '"a", "1"' in cmd
        assert 'new String[]{"1"}' in cmd

    def test_go_restore(self):
        from app.services.agent.tools.sandbox_language import GoTestTool
        cmd = self._run(GoTestTool, '{"a": "1"}')
        assert 'os.Setenv("A", "1")' in cmd
        assert '"program", "1"' in cmd

    def test_ruby_restore(self):
        from app.services.agent.tools.sandbox_language import RubyTestTool
        cmd = self._run(RubyTestTool, '{"a": "1"}')
        assert 'ENV["A"] = "1"' in cmd

    def test_php_restore(self):
        from app.services.agent.tools.sandbox_language import PhpTestTool
        cmd = self._run(PhpTestTool, '{"cmd": "id"}')
        # _build_command 对整段代码做 shell 单引号转义（' → '"'"'）
        expected = "$_GET['cmd'] = 'id';".replace("'", "'\"'\"'")
        assert expected in cmd


# ============ P9-3: Universal 转发层 ============

class TestUniversalForwarding:
    def _universal(self, params, language="javascript"):
        from app.services.agent.tools.sandbox_language import UniversalCodeTestTool
        mgr = FakeMgr()
        tool = UniversalCodeTestTool(mgr, ".")
        result = asyncio.run(tool.execute(
            language=language, code=CODE_SNIPPET, params=params
        ))
        return result, mgr

    def test_bad_params_guidance_before_forward(self):
        result, mgr = self._universal("id")
        assert result.success is False
        assert "params 必须是 JSON 对象" in result.error
        assert mgr.init_called == 0

    def test_restore_forwarded(self):
        result, mgr = self._universal('{"id": "1"}')
        assert result.success is True
        assert mgr.init_called == 1
        assert '"id": "1"' in mgr.commands[0]["command"]


# ============ P9-3: sandbox_tool PhpTestTool + HTTP headers ============

class TestSandboxToolPhp:
    def _run(self, get_params=None, post_params=None):
        from app.services.agent.tools.sandbox_tool import PhpTestTool
        mgr = FakeMgr()
        tool = PhpTestTool(mgr, ".")
        result = asyncio.run(tool.execute(
            php_code="echo 'hi';", get_params=get_params, post_params=post_params
        ))
        return result, mgr

    def test_bad_get_params_guidance(self):
        result, mgr = self._run(get_params="id")
        assert result.success is False
        assert "get_params 必须是 JSON 对象" in result.error
        assert mgr.init_called == 0

    def test_bad_post_params_guidance(self):
        result, mgr = self._run(post_params=["a"])
        assert result.success is False
        assert "post_params 必须是 JSON 对象" in result.error
        assert mgr.init_called == 0

    def test_restore_success(self):
        import base64
        result, mgr = self._run(
            get_params='{"cmd": "id"}', post_params='{"x": "1"}'
        )
        assert result.success is True
        assert mgr.init_called == 1
        cmd = mgr.commands[0]["command"]
        # wrapper 经 base64 编码进命令，解码后核对 PHP 源
        encoded = cmd.split("echo '", 1)[1].split("'", 1)[0]
        php_source = base64.b64decode(encoded).decode("utf-8")
        assert "$_GET['cmd'] = 'id';" in php_source
        assert "$_POST['x'] = '1';" in php_source


class TestHttpHeadersCoerce:
    def _call_http(self, headers):
        from app.services.agent.tools.sandbox_tool import SandboxManager
        mgr = SandboxManager.__new__(SandboxManager)
        mgr.config = MagicMock()
        mgr.config.network_mode = "none"
        captured = {}

        async def _fake_execute_command(command, timeout=None, **kwargs):
            captured["command"] = command
            return {
                "success": True, "stdout": "body\n200", "stderr": "",
                "exit_code": 0, "error": None,
            }

        mgr.execute_command = _fake_execute_command
        result = asyncio.run(mgr.execute_http_request(
            method="GET", url="http://example.com/", headers=headers
        ))
        return result, captured

    def test_headers_json_restored_and_carried(self):
        """headers JSON 字符串还原后，curl 命令实际携带该头。"""
        result, captured = self._call_http('{"X-Test": "abc"}')
        assert result["success"] is True
        assert "-H 'X-Test: abc'" in captured["command"]

    def test_bad_headers_guidance_no_command(self):
        result, captured = self._call_http(["a"])
        assert result["success"] is False
        assert "headers 必须是 JSON 对象" in result["error"]
        assert captured == {}


# ============ wrapper 注入安全 ============

class TestWrapperInjection:
    # ---- PHP: var_export 语义（key 引号/分号 + value 尾反斜杠） ----
    def test_php_key_quote_semicolon_contained(self):
        from app.services.agent.tools.sandbox_language import PhpTestTool, _php_literal
        tool = PhpTestTool(FakeMgr(), ".")
        evil_key = "a']; echo 'PWNED'; //"
        wrapper = tool._build_wrapper_code("echo 1;", {evil_key: "v"})
        # 逃逸片段不得以未转义形式出现
        assert "echo 'PWNED'" not in wrapper
        # key 必须整体作为 var_export 语义字面量嵌入（精确片段断言）
        expected = f"$_GET[{_php_literal(evil_key)}] = 'v';"
        assert expected in wrapper

    def test_php_value_trailing_backslash_contained(self):
        from app.services.agent.tools.sandbox_language import PhpTestTool
        tool = PhpTestTool(FakeMgr(), ".")
        wrapper = tool._build_wrapper_code("echo 1;", {"k": "x\\"})
        # var_export: 反斜杠被翻倍，语句 = 'x\\'; 正常闭合（旧实现为 'x\'; 吞掉闭引号）
        assert "= 'x\\\\';" in wrapper

    # ---- Python: 真实执行 wrapper，确认无注入输出 ----
    def test_python_value_injection_does_not_execute(self):
        """旧实现 value 原样插入单引号：x'; print("uid=0(root)"); y=' 会逃逸执行。"""
        from app.services.agent.tools.sandbox_language import PythonTestTool
        tool = PythonTestTool(FakeMgr(), ".")
        evil_value = "x'; print(\"uid=0(root)\"); y='"
        wrapper = tool._build_wrapper_code("pass", {"k": evil_value})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(wrapper, "<wrapper>", "exec"), {})
        assert "uid=0(root)" not in buf.getvalue(), "注入值被当代码执行，伪造了 uid= 回显"

    def test_python_key_injection_does_not_execute(self):
        from app.services.agent.tools.sandbox_language import PythonTestTool
        tool = PythonTestTool(FakeMgr(), ".")
        evil_key = "K']='1'; print(\"uid=0(root)\"); z=['"
        wrapper = tool._build_wrapper_code("pass", {evil_key: "v"})
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                # 真实执行 wrapper（旧实现此行会执行注入的 print）
                exec(compile(wrapper, "<wrapper>", "exec"), {})
        except ValueError:
            # 注入内容成为 os.environ 的键名（含 ]/; 非法字符）→ 赋值即抛，
            # 注入语句未执行，同样证明 containment
            pass
        assert "uid=0(root)" not in buf.getvalue()

    # ---- Java/Go/Ruby: 断言 JSON 安全字面量被精确嵌入 ----
    def test_java_value_json_embedded(self):
        from app.services.agent.tools.sandbox_language import JavaTestTool
        tool = JavaTestTool(FakeMgr(), ".")
        evil_value = 'x"); System.out.println("uid=0"); //'
        wrapper = tool._build_wrapper_code("int x=0;", {"k": evil_value})
        lit = json.dumps(evil_value)
        assert f'"k", {lit}' in wrapper
        assert 'System.out.println("uid=0")' not in wrapper

    def test_go_value_json_embedded(self):
        from app.services.agent.tools.sandbox_language import GoTestTool
        tool = GoTestTool(FakeMgr(), ".")
        evil_value = 'x\'); fmt.Println("uid=0"); //'
        wrapper = tool._build_wrapper_code("var x int", {"k": evil_value})
        assert f'os.Setenv("K", {json.dumps(evil_value)})' in wrapper
        assert 'fmt.Println("uid=0")' not in wrapper

    def test_ruby_value_hash_interpolation_neutralized(self):
        """Ruby 双引号字符串的 #{...} 插值必须被中和（JSON 不转义 #）。"""
        from app.services.agent.tools.sandbox_language import RubyTestTool
        tool = RubyTestTool(FakeMgr(), ".")
        evil_value = '#{system("echo uid=0")}'
        wrapper = tool._build_wrapper_code("nil", {"k": evil_value})
        # 插值触发序列不得出现（注意：payload 文本 uid=0 会作为普通字符
        # 保留在安全字面量内，那不是执行证据）
        assert '#{system' not in wrapper
        # 精确断言：ENV 赋值右侧为转义后的 Ruby 安全字面量
        safe_lit = json.dumps(evil_value).replace("#", "\\u0023")
        assert f'ENV["K"] = {safe_lit}' in wrapper

    def test_ruby_normal_value_uses_json_literal(self):
        from app.services.agent.tools.sandbox_language import RubyTestTool
        tool = RubyTestTool(FakeMgr(), ".")
        wrapper = tool._build_wrapper_code("nil", {"a": "1"})
        assert 'ENV["A"] = "1"' in wrapper

    # ---- Shell: 真实 bash 执行 ----
    def test_shell_value_injection_does_not_execute(self):
        """旧实现 export K="value"：双引号注入 x"; echo uid=0; echo " 会伪造 uid=。"""
        from app.services.agent.tools.sandbox_language import ShellTestTool
        tool = ShellTestTool(FakeMgr(), ".")
        evil_value = 'x"; echo uid=0; echo "'
        wrapper = tool._build_wrapper_code("true", {"k": evil_value})
        import subprocess
        proc = subprocess.run(
            ["bash", "-c", wrapper], capture_output=True, text=True
        )
        assert "uid=0" not in proc.stdout

    # ---- sandbox_tool PhpTestTool 内联 wrapper 同样加固 ----
    def test_sandbox_tool_php_wrapper_injection_contained(self):
        from app.services.agent.tools.sandbox_tool import PhpTestTool
        # 直接走还原 + wrapper 生成路径：构造含引号 key 的 dict
        mgr = FakeMgr()
        tool = PhpTestTool(mgr, ".")
        evil = {"a']; echo 'PWNED'; //": "v"}
        result = asyncio.run(tool.execute(
            php_code="echo 1;", get_params=json.dumps(evil)
        ))
        assert result.success is True
        cmd = mgr.commands[0]["command"]
        assert "echo 'PWNED'" not in cmd

    # ---- sandbox_tool CommandInjectionTest 模板字面量 ----
    def test_command_injection_php_template_escaped(self):
        import base64

        from app.services.agent.tools.sandbox_tool import CommandInjectionTestTool
        mgr = FakeMgr()
        tool = CommandInjectionTestTool(mgr, ".")
        evil_cmd = "id'; system('id'); //"
        # 直接测 PHP 注入模板（命令以 base64 承载 wrapper 源）
        asyncio.run(tool._test_php_injection("echo 1;", "cmd", evil_cmd))
        assert len(mgr.commands) == 1
        encoded = mgr.commands[0]["command"].split("echo '", 1)[1].split("'", 1)[0]
        php_source = base64.b64decode(encoded).decode("utf-8")
        # 注入不得以未转义代码形式出现
        assert "system('id')" not in php_source
        # var_export 语义：引号被转义
        assert "id\\'; system(\\'id\\'); //" in php_source


# ============ _analyze_output 非 str 值不崩 + 保守口径 ============

class TestAnalyzeOutputDefense:
    def test_non_str_value_does_not_crash(self):
        from app.services.agent.tools.sandbox_language import ShellTestTool
        tool = ShellTestTool(FakeMgr(), ".")
        result = {
            "exit_code": 0,
            "stdout": "1",
        }
        # params value 为数字：旧实现 value.lower() 抛 AttributeError
        analysis = tool._analyze_output(result, {"k": 1})
        assert analysis["is_vulnerable"] is True


# ============ files_read 饿死防护 ============

def _agent_with_steps(steps):
    from app.services.agent.agents.analysis import AnalysisAgent
    agent = AnalysisAgent.__new__(AnalysisAgent)
    agent._steps = steps
    return agent


class TestFilesReadStarvation:
    def test_failed_read_not_counted_success_counted(self):
        from app.services.agent.agents.analysis import AnalysisStep
        steps = [
            AnalysisStep(
                thought="", action="read_file",
                action_input={"file_path": "a.py"},
                observation=(
                    "⚠️ 工具执行失败\n\n**工具**: read_file\n"
                    "**错误**: 必填参数缺失: start_line..."
                ),
            ),
            AnalysisStep(
                thought="", action="read_file",
                action_input={"file_path": "b.py"},
                observation="文件内容: print('hi')",
            ),
        ]
        report = _agent_with_steps(steps)._collect_execution_report()
        assert report["files_read"] == ["b.py"]

    def test_failed_search_not_counted(self):
        from app.services.agent.agents.analysis import AnalysisStep
        steps = [
            AnalysisStep(
                thought="", action="search_code",
                action_input={"keyword": "pw"},
                observation="⚠️ 工具执行失败\n\n**错误**: params 必须是 JSON 对象...",
            ),
            AnalysisStep(
                thought="", action="search_code",
                action_input={"keyword": "ok_kw"},
                observation="搜索结果: ...",
            ),
        ]
        report = _agent_with_steps(steps)._collect_execution_report()
        assert report["grep_patterns"] == ["ok_kw"]

    def test_timeout_and_cancelled_not_counted(self):
        from app.services.agent.agents.analysis import AnalysisStep
        steps = [
            AnalysisStep(
                thought="", action="read_file",
                action_input={"file_path": "t.py"},
                observation="⚠️ 工具 'read_file' 执行超时 (60秒)...",
            ),
            AnalysisStep(
                thought="", action="read_file",
                action_input={"file_path": "c.py"},
                observation="⚠️ 任务已取消",
            ),
        ]
        report = _agent_with_steps(steps)._collect_execution_report()
        assert report["files_read"] == []

    def test_missing_observation_still_counted(self):
        """无 observation（合成步骤/历史兼容）不判失败，保持计数——只排除确证失败。"""
        from app.services.agent.agents.analysis import AnalysisStep
        steps = [
            AnalysisStep(
                thought="", action="read_file",
                action_input={"file_path": "good.py"},
                observation=None,
            ),
        ]
        report = _agent_with_steps(steps)._collect_execution_report()
        assert report["files_read"] == ["good.py"]
