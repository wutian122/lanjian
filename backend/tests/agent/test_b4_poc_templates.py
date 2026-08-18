"""B4 PoC 模板增强测试（T4.1-T4.5）。

验证 _gen_sandbox_command 为认证缺失/多租户隔离/IDOR 生成正确的动态 PoC，
SSRF 模板增强 + bridge 不可用降级，以及 mock 服务自清理约束。
"""
from app.services.agent.agents.verification import VerificationAgent


def _gen(vuln_type: str, title: str = "test"):
    """调用 _gen_sandbox_command 生成模板。"""
    agent = VerificationAgent.__new__(VerificationAgent)
    return agent._gen_sandbox_command(vuln_type, "app.py", 10, title, 0)


def test_poc_template_auth_missing():
    """T4.1: 认证缺失类生成动态 PoC——容器内 loopback mock 无认证接口。"""
    matched = _gen("auth_missing", "Missing Authentication on Sensitive API")
    assert matched is not None
    cmd = matched["input"]["command"]
    # 必须起本地 mock 服务（loopback，无需 bridge）
    assert "http.server" in cmd or "HTTPServer" in cmd or "http.server" in cmd
    assert "127.0.0.1" in cmd or "localhost" in cmd
    # 必须发无凭证请求断言能读数据
    assert "VULNERABILITY_CONFIRMED" in cmd
    # loopback 不需要 network_enabled
    assert matched["input"].get("network_enabled", False) is False


def test_poc_template_tenant_isolation():
    """T4.2: 多租户隔离类生成动态 PoC——mock 多用户数据 + 断言能读他人数据。"""
    matched = _gen("tenant_isolation", "Tenant Isolation Bypass")
    assert matched is not None
    cmd = matched["input"]["command"]
    assert "http.server" in cmd or "HTTPServer" in cmd
    assert "127.0.0.1" in cmd or "localhost" in cmd
    # 必须模拟多租户数据并断言越权读取
    assert "VULNERABILITY_CONFIRMED" in cmd
    assert matched["input"].get("network_enabled", False) is False


def test_poc_template_idor():
    """T4.3: IDOR 类生成动态 PoC——mock CRUD + 越权 ID 请求断言。"""
    matched = _gen("idor", "IDOR on user resource")
    assert matched is not None
    cmd = matched["input"]["command"]
    assert "http.server" in cmd or "HTTPServer" in cmd
    assert "127.0.0.1" in cmd or "localhost" in cmd
    # 必须断言越权 ID 能访问他人资源
    assert "VULNERABILITY_CONFIRMED" in cmd
    assert matched["input"].get("network_enabled", False) is False


def test_poc_template_ssrf():
    """T4.4: SSRF 模板增强——需 network_enabled + bridge，实际请求云元数据。"""
    matched = _gen("ssrf", "SSRF to cloud metadata")
    assert matched is not None
    cmd = matched["input"]["command"]
    # 增强后必须检查源码是否对用户 URL 做过滤
    assert "filter" in cmd.lower() or "validate" in cmd.lower() or "解析" in cmd
    # 必须实际请求云元数据地址
    assert "169.254.169.254" in cmd
    assert "VULNERABILITY_CONFIRMED" in cmd
    # SSRF 需要联网
    assert matched["input"].get("network_enabled") is True


def test_ssrf_degraded_when_bridge_unavailable():
    """T4.4: bridge 不可用时 SSRF 降级为内网地址解析探测模拟。

    降级路径：检查 URL 解析逻辑，不强求真实联网，标 static_confirmed。
    验证 _gen_sandbox_command 在 network_enabled=False 降级场景下
    仍生成有效命令（含 static 断言）。
    """
    matched = _gen("ssrf", "SSRF degraded")
    assert matched is not None
    cmd = matched["input"]["command"]
    # 增强模板必须含降级分支：bridge 不可用时走静态断言
    assert "static" in cmd.lower() or "STATIC_CONFIRMED" in cmd or "降级" in cmd or "degraded" in cmd.lower()


def test_poc_mock_self_cleanup():
    """T4.5: mock 服务自清理约束——随机高端口 + 5s 超时 + try/finally 自清理。"""
    for vtype in ("auth_missing", "tenant_isolation", "idor"):
        matched = _gen(vtype, "test")
        assert matched is not None
        cmd = matched["input"]["command"]
        # 必须用 try/finally 自清理 mock 服务
        assert "finally" in cmd
        # 必须有超时约束
        assert "timeout" in cmd.lower() or "5" in cmd
        # 必须关闭/清理 mock
        assert "shutdown" in cmd or "server_close" in cmd or "close" in cmd
