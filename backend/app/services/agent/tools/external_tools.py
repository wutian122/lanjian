"""
外部安全工具集成
集成 Semgrep、Bandit、Gitleaks、TruffleHog、npm audit 等专业安全工具
"""

import asyncio
import json
import logging
import os
import tempfile
import shutil
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from dataclasses import dataclass

from .base import AgentTool, ToolResult
from .sandbox_tool import SandboxManager
from ..utils.path_safety import resolve_safe_path, UnsafePathError

logger = logging.getLogger(__name__)


# ============ 公共辅助函数 ============

def _smart_resolve_target_path(
    target_path: str, 
    project_root: str, 
    tool_name: str = "Tool"
) -> tuple[str, str, Optional[str]]:
    """
    智能解析目标路径
    
    Args:
        target_path: 用户/Agent 传入的目标路径
        project_root: 项目根目录（绝对路径）
        tool_name: 工具名称（用于日志）
    
    Returns:
        (safe_target_path, host_check_path, error_msg)
        - safe_target_path: 容器内使用的安全路径
        - host_check_path: 宿主机上的检查路径
        - error_msg: 如果有错误返回错误信息，否则为 None
    """
    # 获取项目根目录名
    project_dir_name = os.path.basename(project_root.rstrip('/'))
    
    if target_path in (".", "", "./"):
        # 扫描整个项目根目录，在容器内对应 /workspace
        safe_target_path = "."
        host_check_path = project_root
    elif target_path == project_dir_name or target_path == f"./{project_dir_name}":
        # 🔥 智能修复：Agent 可能把项目名当作子目录传入
        logger.info(f"[{tool_name}] 智能路径修复: '{target_path}' -> '.' (项目根目录名: {project_dir_name})")
        safe_target_path = "."
        host_check_path = project_root
    else:
        # 相对路径，需要验证是否存在
        safe_target_path = target_path.lstrip("/") if target_path.startswith("/") else target_path
        host_check_path = os.path.join(project_root, safe_target_path)
        
        # 🔥 智能回退：如果路径不存在，尝试扫描整个项目
        if not os.path.exists(host_check_path):
            logger.warning(
                f"[{tool_name}] 路径 '{target_path}' 不存在于项目中，自动回退到扫描整个项目 "
                f"(project_root={project_root}, project_dir_name={project_dir_name})"
            )
            # 回退到扫描整个项目
            safe_target_path = "."
            host_check_path = project_root
    
    # 最终检查
    if not os.path.exists(host_check_path):
        error_msg = f"目标路径不存在: {target_path} (完整路径: {host_check_path})"
        logger.error(f"[{tool_name}] {error_msg}")
        return safe_target_path, host_check_path, error_msg
    
    return safe_target_path, host_check_path, None


# ============ Semgrep 工具 ============

class SemgrepInput(BaseModel):
    """Semgrep 扫描输入"""
    target_path: str = Field(
        default=".",
        description="要扫描的路径。⚠️ 重要：使用 '.' 扫描整个项目（推荐），或使用 'src/' 等子目录。不要使用项目目录名如 'PHP-Project'！"
    )
    rules: Optional[str] = Field(
        default="p/security-audit",
        description="规则集: p/security-audit, p/owasp-top-ten, p/r2c-security-audit"
    )
    severity: Optional[str] = Field(
        default=None,
        description="过滤严重程度: ERROR, WARNING, INFO"
    )
    max_results: int = Field(default=50, description="最大返回结果数")


class SemgrepTool(AgentTool):
    """
    Semgrep 静态分析工具
    
    Semgrep 是一款快速、轻量级的静态分析工具，支持多种编程语言。
    提供丰富的安全规则库，可以检测各种安全漏洞。
    
    官方规则集:
    - p/security-audit: 综合安全审计
    - p/owasp-top-ten: OWASP Top 10 漏洞
    - p/r2c-security-audit: R2C 安全审计规则
    - p/python: Python 特定规则
    - p/javascript: JavaScript 特定规则
    """
    
    AVAILABLE_RULESETS = [
        "p/security-audit",
        "p/owasp-top-ten",
        "p/r2c-security-audit",
        "p/python",
        "p/javascript",
        "p/typescript",
        "p/java",
        "p/go",
        "p/php",
        "p/ruby",
        "p/secrets",
        "p/sql-injection",
        "p/xss",
        "p/command-injection",
    ]
    
    def __init__(self, project_root: str, sandbox_manager: Optional["SandboxManager"] = None):
        super().__init__()
        # 🔥 将相对路径转换为绝对路径，Docker 需要绝对路径
        self.project_root = os.path.abspath(project_root)
        # 🔥 使用共享的 SandboxManager 实例，避免重复初始化
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "semgrep_scan"
    
    @property
    def description(self) -> str:
        return """使用 Semgrep 进行静态安全分析。
Semgrep 是业界领先的静态分析工具，支持 30+ 种编程语言。

⚠️ 重要提示:
- target_path 使用 '.' 扫描整个项目（推荐）
- 或使用子目录如 'src/'、'app/' 等
- 不要使用项目目录名（如 'PHP-Project'、'MyApp'）！

可用规则集:
- p/security-audit: 综合安全审计（推荐）
- p/owasp-top-ten: OWASP Top 10 漏洞检测
- p/secrets: 密钥泄露检测
- p/sql-injection: SQL 注入检测

使用场景:
- 快速全面的代码安全扫描
- 检测常见安全漏洞模式"""
    
    @property
    def args_schema(self):
        return SemgrepInput
    
    async def _execute(
        self,
        target_path: str = ".",
        rules: str = "p/security-audit",
        severity: Optional[str] = None,
        max_results: int = 50,
        **kwargs
    ) -> ToolResult:
        """执行 Semgrep 扫描"""
        # 确保 Docker 可用
        await self.sandbox_manager.initialize()
        if not self.sandbox_manager.is_available:
            error_msg = f"Semgrep unavailable: {self.sandbox_manager.get_diagnosis()}"
            return ToolResult(
                success=False,
                data=error_msg,  # 🔥 修复：设置 data 字段避免 None
                error=error_msg
            )

        # 🔥 使用公共函数进行智能路径解析
        safe_target_path, host_check_path, error_msg = _smart_resolve_target_path(
            target_path, self.project_root, "Semgrep"
        )
        if error_msg:
            return ToolResult(success=False, data=error_msg, error=error_msg)
        
        cmd = ["semgrep", "--json", "--quiet"]
        
        if rules == "auto":
            # 🔥 Fallback if user explicitly requests 'auto', but prefer security-audit
            cmd.extend(["--config", "p/security-audit"])
        elif rules.startswith("p/"):
            cmd.extend(["--config", rules])
        else:
            cmd.extend(["--config", rules])
        
        if severity:
            cmd.extend(["--severity", severity])
        
        # 在容器内，路径相对于 /workspace
        cmd.append(safe_target_path)
        
        cmd_str = " ".join(cmd)
        
        try:
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd_str,
                host_workdir=self.project_root,
                timeout=300,
                network_mode="bridge"  # 🔥 Semgrep 需要网络来下载规则
            )

            # 🔥 Check if semgrep-core is missing in sandbox, fallback to subprocess
            if not result['success'] and 'semgrep-core' in result.get('stderr', ''):
                logger.info("[Semgrep] Sandbox semgrep-core not found, falling back to subprocess execution")
                return await self._execute_via_subprocess(safe_target_path, rules, severity, max_results)

            # 🔥 添加调试日志
            logger.info(f"[Semgrep] 执行结果: success={result['success']}, exit_code={result['exit_code']}, "
                       f"stdout_len={len(result.get('stdout', ''))}, stderr_len={len(result.get('stderr', ''))}")
            if result.get('error'):
                logger.warning(f"[Semgrep] 错误信息: {result['error']}")
            if result.get('stderr'):
                logger.warning(f"[Semgrep] stderr: {result['stderr'][:500]}")

            if not result["success"] and result["exit_code"] != 1:  # 1 means findings were found
                # 🔥 增强：优先使用 stderr，其次 stdout，最后用 error 字段
                stdout_preview = result.get('stdout', '')[:500]
                stderr_preview = result.get('stderr', '')[:500]
                error_msg = stderr_preview or stdout_preview or result.get('error') or "未知错误"
                logger.error(f"[Semgrep] 执行失败 (exit_code={result['exit_code']}): {error_msg}")
                if stdout_preview:
                    logger.error(f"[Semgrep] stdout: {stdout_preview}")
                return ToolResult(
                    success=False,
                    data=f"Semgrep 执行失败 (exit_code={result['exit_code']}): {error_msg}",
                    error=f"Semgrep 执行失败: {error_msg}",
                )

            # 解析结果
            stdout = result.get('stdout', '')
            try:
                # 尝试从 stdout 查找 JSON
                json_start = stdout.find('{')
                logger.debug(f"[Semgrep] JSON 起始位置: {json_start}, stdout 前200字符: {stdout[:200]}")

                if json_start >= 0:
                    json_str = stdout[json_start:]
                    results = json.loads(json_str)
                    logger.info(f"[Semgrep] JSON 解析成功, results 数量: {len(results.get('results', []))}")
                else:
                    logger.warning(f"[Semgrep] 未找到 JSON 起始符 '{{', stdout: {stdout[:500]}")
                    results = {}
            except json.JSONDecodeError as e:
                error_msg = f"无法解析 Semgrep 输出 (位置 {e.pos}): {e.msg}"
                logger.error(f"[Semgrep] JSON 解析失败: {error_msg}")
                logger.error(f"[Semgrep] 原始输出前500字符: {stdout[:500]}")
                return ToolResult(
                    success=False,
                    data=error_msg,  # 🔥 修复：设置 data 字段避免 None
                    error=error_msg,
                )
            
            findings = results.get("results", [])[:max_results]
            
            if not findings:
                return ToolResult(
                    success=True,
                    data=f"Semgrep 扫描完成，未发现安全问题 (规则集: {rules})",
                    metadata={"findings_count": 0, "rules": rules}
                )
            
            # 格式化输出
            output_parts = [f"🔍 Semgrep 扫描结果 (规则集: {rules})\n"]
            output_parts.append(f"发现 {len(findings)} 个问题:\n")
            
            severity_icons = {"ERROR": "🔴", "WARNING": "🟠", "INFO": "🟡"}
            
            for i, finding in enumerate(findings[:max_results]):
                sev = finding.get("extra", {}).get("severity", "INFO")
                icon = severity_icons.get(sev, "⚪")
                
                output_parts.append(f"\n{icon} [{sev}] {finding.get('check_id', 'unknown')}")
                output_parts.append(f"   文件: {finding.get('path', '')}:{finding.get('start', {}).get('line', 0)}")
                output_parts.append(f"   消息: {finding.get('extra', {}).get('message', '')[:200]}")
                
                # 代码片段
                lines = finding.get("extra", {}).get("lines", "")
                if lines:
                    output_parts.append(f"   代码: {lines[:150]}")
            
            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={
                    "findings_count": len(findings),
                    "rules": rules,
                    "findings": findings[:max_results],  # P3: 全量传递，不再截断为10
                }
            )
            
        except Exception as e:
            error_msg = f"Semgrep 执行错误: {str(e)}"
            return ToolResult(
                success=False,
                data=error_msg,  # 🔥 修复：设置 data 字段避免 None
                error=error_msg
            )



    async def _execute_via_subprocess(
        self,
        target_path: str = ".",
        rules: str = "p/security-audit",
        severity: Optional[str] = None,
        max_results: int = 50,
    ) -> ToolResult:
        """Fallback: execute semgrep via subprocess in backend container (not sandbox).
        
        Uses asyncio.create_subprocess_exec to avoid blocking the event loop,
        which would cause SSE heartbeat timeouts and stream disconnections.
         """
        _proxy_keys = ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"]
        clean_env = {k: v for k, v in os.environ.items() if not (k in _proxy_keys and not v.strip())}

        cmd = ["semgrep", "--json", "--quiet", "--max-target-bytes", "1000000"]
        if rules == "auto":
            cmd.extend(["--config", "p/security-audit", "--config", "p/owasp-top-ten"])
        elif rules.startswith("p/"):
            cmd.extend(["--config", rules])
        else:
            cmd.extend(["--config", rules])
        if severity:
            cmd.extend(["--severity", severity])
        cmd.append(target_path)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
                env=clean_env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=300
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(success=False, data="Semgrep subprocess timed out (300s)", error="timeout")

            stdout_text = stdout.decode('utf-8', errors='replace') if stdout else ""
            if stdout_text:
                results = json.loads(stdout_text[stdout_text.find('{'):] if '{' in stdout_text else '{}')
                raw_findings = results.get("results", [])[:max_results]

                findings = []
                for f in raw_findings:
                    start = f.get("start", {}) if isinstance(f.get("start"), dict) else {}
                    end = f.get("end", {}) if isinstance(f.get("end"), dict) else {}
                    extra = f.get("extra", {}) if isinstance(f.get("extra"), dict) else {}
                    findings.append({
                        "rule_id": f.get("check_id", "unknown"),
                        "file": f.get("path", ""),
                        "line_start": start.get("line", 0),
                        "line_end": end.get("line", 0),
                        "severity": extra.get("severity", "WARNING"),
                        "message": extra.get("message", ""),
                        "code": (extra.get("lines", "") or "")[:500],
                    })

                output_parts = [f"Semgrep (subprocess) scan results ({len(findings)} findings):\n"]
                for fd in findings[:15]:
                    sev_icon = {"ERROR": "??", "WARNING": "??", "INFO": "??"}.get(fd["severity"], "?")
                    output_parts.append(f"{sev_icon} {fd['rule_id']}")
                    output_parts.append(f"   File: {fd['file']}:{fd['line_start']}")
                    output_parts.append(f"   {fd['message'][:200]}\n")

                return ToolResult(
                    success=True,
                    data="\n".join(output_parts),
                    metadata={"findings_count": len(findings), "source": "semgrep_subprocess"}
                )
            else:
                return ToolResult(success=True, data="Semgrep (subprocess) completed with no output")
        except Exception as e:
            return ToolResult(success=False, data=f"Semgrep subprocess error: {e}", error=str(e))

# ============ Bandit 工具 (Python) ============

class BanditInput(BaseModel):
    """Bandit 扫描输入"""
    target_path: str = Field(
        default=".",
        description="要扫描的路径。使用 '.' 扫描整个项目（推荐），不要使用项目目录名！"
    )
    severity: str = Field(default="medium", description="最低严重程度: low, medium, high")
    confidence: str = Field(default="medium", description="最低置信度: low, medium, high")
    max_results: int = Field(default=50, description="最大返回结果数")

class BanditTool(AgentTool):
    """
    Bandit Python 安全扫描工具
    
    Bandit 是专门用于 Python 代码的安全分析工具，
    可以检测常见的 Python 安全问题，如：
    - 硬编码密码
    - SQL 注入
    - 命令注入
    - 不安全的随机数生成
    - 不安全的反序列化
    """
    
    def __init__(self, project_root: str, sandbox_manager: Optional["SandboxManager"] = None):
        super().__init__()
        # 🔥 将相对路径转换为绝对路径，Docker 需要绝对路径
        self.project_root = os.path.abspath(project_root)
        # 🔥 使用共享的 SandboxManager 实例，避免重复初始化
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "bandit_scan"
    
    @property
    def description(self) -> str:
        return """使用 Bandit 扫描 Python 代码的安全问题。
Bandit 是 Python 专用的安全分析工具。

⚠️ 重要提示: target_path 使用 '.' 扫描整个项目，不要使用项目目录名！

检测项目:
- shell/SQL 注入
- 硬编码密码
- 不安全的反序列化
- SSL/TLS 问题

仅适用于 Python 项目。"""
    
    @property
    def args_schema(self):
        return BanditInput
    
    async def _execute(
        self,
        target_path: str = ".",
        severity: str = "medium",
        confidence: str = "medium",
        max_results: int = 50,
        **kwargs
    ) -> ToolResult:
        """执行 Bandit 扫描"""
        # 确保 Docker 可用
        await self.sandbox_manager.initialize()
        if not self.sandbox_manager.is_available:
            error_msg = f"Bandit unavailable: {self.sandbox_manager.get_diagnosis()}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # 🔥 使用公共函数进行智能路径解析
        safe_target_path, host_check_path, error_msg = _smart_resolve_target_path(
            target_path, self.project_root, "Bandit"
        )
        if error_msg:
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # 构建命令
        severity_map = {"low": "l", "medium": "m", "high": "h"}
        confidence_map = {"low": "l", "medium": "m", "high": "h"}
        
        cmd = [
            "bandit", "-r", "-f", "json",
            "-ll" if severity == "low" else f"-l{severity_map.get(severity, 'm')}",
            f"-i{confidence_map.get(confidence, 'm')}",
            safe_target_path
        ]
        
        cmd_str = " ".join(cmd)
        
        try:
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd_str,
                host_workdir=self.project_root,
                timeout=120
            )
            
            try:
                # find json in output
                json_start = result['stdout'].find('{')
                if json_start >= 0:
                    results = json.loads(result['stdout'][json_start:])
                else:
                    results = {}
            except json.JSONDecodeError:
                error_msg = f"无法解析 Bandit 输出: {result['stdout'][:200]}"
                return ToolResult(success=False, data=error_msg, error=error_msg)
            
            findings = results.get("results", [])[:max_results]
            
            if not findings:
                return ToolResult(
                    success=True,
                    data="Bandit 扫描完成，未发现 Python 安全问题",
                    metadata={"findings_count": 0}
                )
            
            output_parts = ["🐍 Bandit Python 安全扫描结果\n"]
            output_parts.append(f"发现 {len(findings)} 个问题:\n")
            
            severity_icons = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}
            
            for finding in findings:
                sev = finding.get("issue_severity", "LOW")
                icon = severity_icons.get(sev, "⚪")
                
                output_parts.append(f"\n{icon} [{sev}] {finding.get('test_id', '')}: {finding.get('test_name', '')}")
                output_parts.append(f"   文件: {finding.get('filename', '')}:{finding.get('line_number', 0)}")
                output_parts.append(f"   消息: {finding.get('issue_text', '')[:200]}")
                output_parts.append(f"   代码: {finding.get('code', '')[:100]}")
            
            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={"findings_count": len(findings), "findings": findings[:10]}
            )
            
        except Exception as e:
            error_msg = f"Bandit 执行错误: {str(e)}"
            return ToolResult(success=False, data=error_msg, error=error_msg)


# ============ Gitleaks 工具 ============

class GitleaksInput(BaseModel):
    """Gitleaks 扫描输入"""
    target_path: str = Field(
        default=".",
        description="要扫描的路径。使用 '.' 扫描整个项目（推荐），不要使用项目目录名！"
    )
    no_git: bool = Field(default=True, description="不使用 git history，仅扫描文件")
    max_results: int = Field(default=50, description="最大返回结果数")


class GitleaksTool(AgentTool):
    """
    Gitleaks 密钥泄露检测工具
    
    Gitleaks 是一款专门用于检测代码中硬编码密钥的工具。
    可以检测：
    - API Keys (AWS, GCP, Azure, GitHub, etc.)
    - 私钥 (RSA, SSH, PGP)
    - 数据库凭据
    - OAuth tokens
    - JWT secrets
    """
    
    def __init__(self, project_root: str, sandbox_manager: Optional["SandboxManager"] = None):
        super().__init__()
        # 🔥 将相对路径转换为绝对路径，Docker 需要绝对路径
        self.project_root = os.path.abspath(project_root)
        # 🔥 使用共享的 SandboxManager 实例，避免重复初始化
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "gitleaks_scan"
    
    @property
    def description(self) -> str:
        return """使用 Gitleaks 检测代码中的密钥泄露。
Gitleaks 是专业的密钥检测工具，支持 150+ 种密钥类型。

⚠️ 重要提示: target_path 使用 '.' 扫描整个项目，不要使用项目目录名！

检测类型:
- AWS/GCP/Azure 凭据
- GitHub/GitLab Tokens
- 私钥 (RSA, SSH, PGP)
- 数据库连接字符串
- JWT Secrets

建议在代码审计早期使用此工具。"""
    
    @property
    def args_schema(self):
        return GitleaksInput
    
    async def _execute(
        self,
        target_path: str = ".",
        no_git: bool = True,
        max_results: int = 50,
        **kwargs
    ) -> ToolResult:
        """执行 Gitleaks 扫描"""
        # 确保 Docker 可用
        await self.sandbox_manager.initialize()
        if not self.sandbox_manager.is_available:
            error_msg = f"Gitleaks unavailable: {self.sandbox_manager.get_diagnosis()}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # 🔥 使用公共函数进行智能路径解析
        safe_target_path, host_check_path, error_msg = _smart_resolve_target_path(
            target_path, self.project_root, "Gitleaks"
        )
        if error_msg:
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # 🔥 修复：新版 gitleaks 需要使用 --report-path 输出到文件
        # 使用 /tmp 目录（tmpfs 可写）
        cmd = [
            "gitleaks", "detect",
            "--source", safe_target_path,
            "--report-format", "json",
            "--report-path", "/tmp/gitleaks-report.json",
            "--exit-code", "0"  # 🔥 不要因为发现密钥而返回非零退出码
        ]
        if no_git:
            cmd.append("--no-git")

        # 执行 gitleaks 并读取报告文件
        cmd_str = " ".join(cmd) + " && cat /tmp/gitleaks-report.json"

        try:
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd_str,
                host_workdir=self.project_root,
                timeout=180  # 🔥 增加超时时间
            )

            if result['exit_code'] != 0:
                stderr = result.get('stderr', '').lower()
                error_msg = result.get('error') or result.get('stderr', '')[:300] or '未知错误'

                # 🔥 修复：Gitleaks 未安装时回退到内置 Python 密钥扫描器
                if (
                    'command not found' in stderr
                    or 'gitleaks: not found' in stderr
                    or '没有那个文件或目录' in stderr
                    or 'no such file' in stderr
                ):
                    logger.warning("[Gitleaks] gitleaks binary not found in sandbox, falling back to built-in regex scanner")
                    return await self._run_fallback_scanner(safe_target_path, max_results)

                return ToolResult(success=False, data=f"Gitleaks 执行失败: {error_msg}", error=f"Gitleaks 执行失败: {error_msg}")

            stdout = result['stdout']
            
            if not stdout.strip():
                return ToolResult(
                    success=True,
                    data="🔐 Gitleaks 扫描完成，未发现密钥泄露",
                    metadata={"findings_count": 0}
                )
            
            try:
                # Find JSON start
                json_start = stdout.find('[')
                if json_start >= 0:
                     findings = json.loads(stdout[json_start:])
                else:
                     findings = []
            except json.JSONDecodeError:
                findings = []
            
            if not findings:
                 return ToolResult(
                    success=True,
                    data="🔐 Gitleaks 扫描完成，未发现密钥泄露",
                    metadata={"findings_count": 0}
                )
            
            findings = findings[:max_results]
            
            output_parts = ["🔐 Gitleaks 密钥泄露检测结果\n"]
            output_parts.append(f"⚠️ 发现 {len(findings)} 处密钥泄露!\n")
            
            for i, finding in enumerate(findings):
                output_parts.append(f"\n🔴 [{i+1}] {finding.get('RuleID', 'unknown')}")
                output_parts.append(f"   描述: {finding.get('Description', '')}")
                output_parts.append(f"   文件: {finding.get('File', '')}:{finding.get('StartLine', 0)}")
                
                # 部分遮盖密钥
                secret = finding.get('Secret', '')
                if len(secret) > 8:
                    masked = secret[:4] + '*' * (len(secret) - 8) + secret[-4:]
                else:
                    masked = '*' * len(secret)
                output_parts.append(f"   密钥: {masked}")
            
            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={
                    "findings_count": len(findings),
                    "findings": [
                        {"rule": f.get("RuleID"), "file": f.get("File"), "line": f.get("StartLine")}
                        for f in findings[:10]
                    ]
                }
            )
            
        except Exception as e:
            error_msg = f"Gitleaks 执行错误: {str(e)}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

    async def _run_fallback_scanner(
        self,
        target_path: str,
        max_results: int = 50,
    ) -> ToolResult:
        """
        🔥 Gitleaks 未安装时的内置回退扫描器
        使用 Python 正则匹配常见密钥模式，覆盖核心密钥类型
        """
        scanner_script = r'''
import os
import re
import json
import sys

TARGET = sys.argv[1]
MAX_RESULTS = int(sys.argv[2])

# 常见密钥/敏感信息正则模式
PATTERNS = [
    ("AWS Access Key ID", r"(?<![A-Za-z0-9])(AKIA[0-9A-Z]{16})(?![A-Za-z0-9])"),
    ("AWS Secret Access Key", r"(?<![A-Za-z0-9/+=])([0-9a-zA-Z/+]{40})(?![A-Za-z0-9/+=])"),
    ("GitHub Personal Access Token", r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    ("GitLab Personal Access Token", r"glpat-[A-Za-z0-9_\-]{20,}"),
    ("Slack Token", r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}(-[a-zA-Z0-9]{24})?"),
    ("OpenAI API Key", r"sk-[a-zA-Z0-9]{20,}"),
    ("Private Key", r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ("JWT Token", r"eyJ[A-Za-z0-9_\-]*\.eyJ[A-Za-z0-9_\-]*\.[A-Za-z0-9_\-]*"),
    ("Generic API Key", r"(?i)(api[_\-]?key|apikey|secret[_\-]?key|secretkey)[\s]*[=:][\s]*['\"][a-zA-Z0-9_\-]{16,}['\"]"),
    ("Password in Code", r"(?i)(password|passwd|pwd)[\s]*[=:][\s]*['\"][^'\"]{8,}['\"]"),
    ("Database Connection String", r"(?i)(mongodb|mysql|postgresql|postgres|redis|amqp)://[^\s\"']+"),
    ("Google API Key", r"AIza[0-9A-Za-z_\-]{35}"),
]

# 排除的文件/目录
EXCLUDE = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build', '.tox'}

findings = []

for root, dirs, files in os.walk(TARGET):
    # 排除目录
    dirs[:] = [d for d in dirs if d not in EXCLUDE]
    for filename in files:
        # 跳过明显非文本文件
        if any(filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.ttf', '.eot', '.pdf', '.zip', '.tar.gz', '.bin']):
            continue
        filepath = os.path.join(root, filename)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            continue

        for rule_name, pattern in PATTERNS:
            for match in re.finditer(pattern, content):
                line_no = content[:match.start()].count('\n') + 1
                secret = match.group(0)
                # 简单过滤：避免匹配注释中的示例
                line_content = content.split('\n')[line_no - 1].strip()
                if line_content.startswith('#') or line_content.startswith('//') or line_content.startswith('*'):
                    continue
                findings.append({
                    "RuleID": rule_name,
                    "Description": f"Potential {rule_name}",
                    "File": os.path.relpath(filepath, TARGET),
                    "StartLine": line_no,
                    "Secret": secret,
                })
                if len(findings) >= MAX_RESULTS:
                    break
            if len(findings) >= MAX_RESULTS:
                break
    if len(findings) >= MAX_RESULTS:
        break

print(json.dumps(findings, ensure_ascii=False))
'''

        # 将 scanner 脚本写入沙箱并执行
        import base64
        encoded = base64.b64encode(scanner_script.encode("utf-8")).decode("ascii")
        cmd = (
            f"echo '{encoded}' | base64 -d > /tmp/gitleaks_fallback.py && "
            f"python3 /tmp/gitleaks_fallback.py {target_path} {max_results}"
        )

        try:
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd,
                host_workdir=self.project_root,
                timeout=180,
            )

            if result['exit_code'] != 0:
                error_msg = result.get('error') or result.get('stderr', '')[:300] or '未知错误'
                return ToolResult(success=False, data=f"内置密钥扫描器失败: {error_msg}", error=f"内置密钥扫描器失败: {error_msg}")

            stdout = result['stdout']
            try:
                findings = json.loads(stdout) if stdout.strip() else []
            except json.JSONDecodeError:
                findings = []

            if not findings:
                return ToolResult(
                    success=True,
                    data="🔐 密钥扫描完成（Gitleaks 不可用，已使用内置扫描器），未发现密钥泄露",
                    metadata={"findings_count": 0, "scanner": "fallback"}
                )

            findings = findings[:max_results]

            output_parts = ["🔐 密钥泄露检测结果（Gitleaks 不可用，使用内置扫描器）\n"]
            output_parts.append(f"⚠️ 发现 {len(findings)} 处潜在密钥泄露!\n")

            for i, finding in enumerate(findings):
                output_parts.append(f"\n🔴 [{i+1}] {finding.get('RuleID', 'unknown')}")
                output_parts.append(f"   描述: {finding.get('Description', '')}")
                output_parts.append(f"   文件: {finding.get('File', '')}:{finding.get('StartLine', 0)}")

                secret = finding.get('Secret', '')
                if len(secret) > 8:
                    masked = secret[:4] + '*' * (len(secret) - 8) + secret[-4:]
                else:
                    masked = '*' * len(secret)
                output_parts.append(f"   密钥: {masked}")

            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={
                    "findings_count": len(findings),
                    "scanner": "fallback",
                    "findings": [
                        {"rule": f.get("RuleID"), "file": f.get("File"), "line": f.get("StartLine")}
                        for f in findings[:10]
                    ]
                }
            )

        except Exception as e:
            error_msg = f"内置密钥扫描器错误: {str(e)}"
            return ToolResult(success=False, data=error_msg, error=error_msg)


# ============ npm audit 工具 ============

class NpmAuditInput(BaseModel):
    """npm audit 扫描输入"""
    target_path: str = Field(default=".", description="包含 package.json 的目录")
    production_only: bool = Field(default=False, description="仅扫描生产依赖")


class NpmAuditTool(AgentTool):
    """
    npm audit 依赖漏洞扫描工具
    
    扫描 Node.js 项目的依赖漏洞，基于 npm 官方漏洞数据库。
    """
    
    def __init__(self, project_root: str, sandbox_manager: Optional["SandboxManager"] = None):
        super().__init__()
        # 🔥 将相对路径转换为绝对路径，Docker 需要绝对路径
        self.project_root = os.path.abspath(project_root)
        # 🔥 使用共享的 SandboxManager 实例，避免重复初始化
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "npm_audit"
    
    @property
    def description(self) -> str:
        return """使用 npm audit 扫描 Node.js 项目的依赖漏洞。
基于 npm 官方漏洞数据库，检测已知的依赖安全问题。

适用于:
- 包含 package.json 的 Node.js 项目
- 前端项目 (React, Vue, Angular 等)

需要先运行 npm install 安装依赖。"""
    
    @property
    def args_schema(self):
        return NpmAuditInput
    
    async def _execute(
        self,
        target_path: str = ".",
        production_only: bool = False,
        **kwargs
    ) -> ToolResult:
        """执行 npm audit"""
        # 确保 Docker 可用
        await self.sandbox_manager.initialize()
        if not self.sandbox_manager.is_available:
            error_msg = f"npm audit unavailable: {self.sandbox_manager.get_diagnosis()}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # 这里的 target_path 是相对于 project_root 的
        # 防止空路径
        safe_target_path = target_path if not target_path.startswith("/") else target_path.lstrip("/")
        if not safe_target_path:
            safe_target_path = "."

        # P1-7: Path Traversal 防护 —— 替代原来的 normpath+join，防止 ../../../etc 逃逸
        try:
            full_path = str(resolve_safe_path(self.project_root, safe_target_path))
        except UnsafePathError as e:
            error_msg = f"路径不安全: {e}"
            return ToolResult(success=False, data=error_msg, error=error_msg)
        
        # 宿主机预检查
        package_json = os.path.join(full_path, "package.json")
        if not os.path.exists(package_json):
            error_msg = f"未找到 package.json: {target_path}"
            return ToolResult(
                success=False,
                data=error_msg,
                error=error_msg,
            )
        
        cmd = ["npm", "audit", "--json"]
        if production_only:
            cmd.append("--production")
        
        # 组合命令: cd 到目标目录然后执行
        cmd_str = f"cd {safe_target_path} && {' '.join(cmd)}"
        
        try:
            # 清除代理设置，避免容器内网络问题
            proxy_env = {
                "HTTPS_PROXY": "",
                "HTTP_PROXY": "",
                "https_proxy": "",
                "http_proxy": ""
            }
            
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd_str,
                host_workdir=self.project_root,
                timeout=120,
                network_mode="bridge",
                env=proxy_env
            )
            
            try:
                # npm audit json starts with {
                json_start = result['stdout'].find('{')
                if json_start >= 0:
                    results = json.loads(result['stdout'][json_start:])
                else:
                    return ToolResult(success=True, data=f"npm audit 输出为空或格式错误: {result['stdout'][:100]}")
            except json.JSONDecodeError:
                return ToolResult(success=True, data=f"npm audit 输出格式错误")
            
            vulnerabilities = results.get("vulnerabilities", {})
            
            if not vulnerabilities:
                return ToolResult(
                    success=True,
                    data="📦 npm audit 完成，未发现依赖漏洞",
                    metadata={"findings_count": 0}
                )
            
            output_parts = ["📦 npm audit 依赖漏洞扫描结果\n"]
            
            severity_counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0}
            for name, vuln in vulnerabilities.items():
                severity = vuln.get("severity", "low")
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
            
            output_parts.append(f"漏洞统计: 🔴 Critical: {severity_counts['critical']}, 🟠 High: {severity_counts['high']}, 🟡 Moderate: {severity_counts['moderate']}, 🟢 Low: {severity_counts['low']}\n")
            
            severity_icons = {"critical": "🔴", "high": "🟠", "moderate": "🟡", "low": "🟢"}
            
            for name, vuln in list(vulnerabilities.items())[:20]:
                sev = vuln.get("severity", "low")
                icon = severity_icons.get(sev, "⚪")
                output_parts.append(f"\n{icon} [{sev.upper()}] {name}")
                output_parts.append(f"   版本范围: {vuln.get('range', 'unknown')}")
                
                via = vuln.get("via", [])
                if via and isinstance(via[0], dict):
                    output_parts.append(f"   来源: {via[0].get('title', '')[:100]}")
            
            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={
                    "findings_count": len(vulnerabilities),
                    "severity_counts": severity_counts,
                }
            )
            
        except Exception as e:
            error_msg = f"npm audit 错误: {str(e)}"
            return ToolResult(success=False, data=error_msg, error=error_msg)


# ============ Safety 工具 (Python 依赖) ============

class SafetyInput(BaseModel):
    """Safety 扫描输入"""
    requirements_file: str = Field(default="requirements.txt", description="requirements 文件路径")


class SafetyTool(AgentTool):
    """
    Safety Python 依赖漏洞扫描工具
    
    检查 Python 依赖中的已知安全漏洞。
    """
    
    def __init__(self, project_root: str, sandbox_manager: Optional["SandboxManager"] = None):
        super().__init__()
        # 🔥 将相对路径转换为绝对路径，Docker 需要绝对路径
        self.project_root = os.path.abspath(project_root)
        # 🔥 使用共享的 SandboxManager 实例，避免重复初始化
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "safety_scan"
    
    @property
    def description(self) -> str:
        return """使用 Safety 扫描 Python 依赖的安全漏洞。
基于 PyUp.io 漏洞数据库检测已知的依赖安全问题。

适用于:
- 包含 requirements.txt 的 Python 项目
- Pipenv 项目 (Pipfile.lock)
- Poetry 项目 (poetry.lock)"""
    
    @property
    def args_schema(self):
        return SafetyInput
    
    async def _execute(
        self,
        requirements_file: str = "requirements.txt",
        **kwargs
    ) -> ToolResult:
        """执行 Safety 扫描"""
        # 确保 Docker 可用
        await self.sandbox_manager.initialize()
        if not self.sandbox_manager.is_available:
            error_msg = f"Safety unavailable: {self.sandbox_manager.get_diagnosis()}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # P1-7: Path Traversal 防护
        safe_req_file = requirements_file if not requirements_file.startswith("/") else requirements_file.lstrip("/")
        try:
            full_path = str(resolve_safe_path(self.project_root, safe_req_file))
        except UnsafePathError as e:
            error_msg = f"路径不安全: {e}"
            return ToolResult(success=False, data=error_msg, error=error_msg)
        if not os.path.exists(full_path):
            error_msg = f"未找到依赖文件: {requirements_file}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # commands
        # requirements_file relative path inside container is just requirements_file (assuming it's relative to root)
        # If requirements_file is absolute, we need to make it relative.
        # But for security, `requirements_file` should be relative to project_root.

        cmd = ["safety", "check", "-r", safe_req_file, "--json"]
        cmd_str = " ".join(cmd)
        
        try:
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd_str,
                host_workdir=self.project_root,
                timeout=120
            )
            
            stdout = result['stdout']
            try:
                # Safety 输出的 JSON 格式可能不同版本有差异
                # find first { or [
                start_idx = -1
                for i, char in enumerate(stdout):
                    if char in ['{', '[']:
                        start_idx = i
                        break
                
                if start_idx >= 0:
                     output_json = stdout[start_idx:]
                     if "No known security" in output_json:
                          return ToolResult(
                            success=True,
                            data="🐍 Safety 扫描完成，未发现 Python 依赖漏洞",
                            metadata={"findings_count": 0}
                        )
                     results = json.loads(output_json)
                else:
                     return ToolResult(success=True, data=f"Safety 输出:\n{stdout[:1000]}")

            except (json.JSONDecodeError, ValueError):
                # L2: 只捕 JSON 解析错误，其他异常上抛
                return ToolResult(success=True, data=f"Safety 输出解析失败:\n{stdout[:1000]}")
            
            vulnerabilities = results if isinstance(results, list) else results.get("vulnerabilities", [])
            
            if not vulnerabilities:
                return ToolResult(
                    success=True,
                    data="🐍 Safety 扫描完成，未发现 Python 依赖漏洞",
                    metadata={"findings_count": 0}
                )
            
            output_parts = ["🐍 Safety Python 依赖漏洞扫描结果\n"]
            output_parts.append(f"发现 {len(vulnerabilities)} 个漏洞:\n")
            
            for vuln in vulnerabilities[:20]:
                if isinstance(vuln, list) and len(vuln) >= 4:
                    output_parts.append(f"\n🔴 {vuln[0]} ({vuln[1]})")
                    output_parts.append(f"   漏洞 ID: {vuln[4] if len(vuln) > 4 else 'N/A'}")
                    output_parts.append(f"   描述: {vuln[3][:200] if len(vuln) > 3 else ''}")
            
            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={"findings_count": len(vulnerabilities)}
            )
            
        except Exception as e:
            error_msg = f"Safety 执行错误: {str(e)}"
            return ToolResult(success=False, data=error_msg, error=error_msg)


# ============ TruffleHog 工具 ============

class TruffleHogInput(BaseModel):
    """TruffleHog 扫描输入"""
    target_path: str = Field(
        default=".",
        description="要扫描的路径。使用 '.' 扫描整个项目（推荐），不要使用项目目录名！"
    )
    only_verified: bool = Field(default=False, description="仅显示已验证的密钥")


class TruffleHogTool(AgentTool):
    """
    TruffleHog 深度密钥扫描工具
    
    TruffleHog 可以检测代码和 Git 历史中的密钥泄露，
    并可以验证密钥是否仍然有效。
    """
    
    def __init__(self, project_root: str, sandbox_manager: Optional["SandboxManager"] = None):
        super().__init__()
        # 🔥 将相对路径转换为绝对路径，Docker 需要绝对路径
        self.project_root = os.path.abspath(project_root)
        # 🔥 使用共享的 SandboxManager 实例，避免重复初始化
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "trufflehog_scan"
    
    @property
    def description(self) -> str:
        return """使用 TruffleHog 进行深度密钥扫描。

⚠️ 重要提示: target_path 使用 '.' 扫描整个项目，不要使用项目目录名！

特点:
- 支持 700+ 种密钥类型
- 可以验证密钥是否仍然有效
- 高精度，低误报

建议与 Gitleaks 配合使用。"""
    
    @property
    def args_schema(self):
        return TruffleHogInput
    
    async def _execute(
        self,
        target_path: str = ".",
        only_verified: bool = False,
        **kwargs
    ) -> ToolResult:
        """执行 TruffleHog 扫描"""
        # 确保 Docker 可用
        await self.sandbox_manager.initialize()
        if not self.sandbox_manager.is_available:
            error_msg = f"TruffleHog unavailable: {self.sandbox_manager.get_diagnosis()}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # 🔥 使用公共函数进行智能路径解析
        safe_target_path, host_check_path, error_msg = _smart_resolve_target_path(
            target_path, self.project_root, "TruffleHog"
        )
        if error_msg:
            return ToolResult(success=False, data=error_msg, error=error_msg)

        cmd = ["trufflehog", "filesystem", safe_target_path, "--json"]
        if only_verified:
            cmd.append("--only-verified")
        
        cmd_str = " ".join(cmd)
        
        try:
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd_str,
                host_workdir=self.project_root,
                timeout=180
            )
            
            stdout = result['stdout']
            
            if not stdout.strip():
                return ToolResult(
                    success=True,
                    data="🔍 TruffleHog 扫描完成，未发现密钥泄露",
                    metadata={"findings_count": 0}
                )
            
            # TruffleHog 输出每行一个 JSON 对象
            findings = []
            for line in stdout.strip().split('\n'):
                if line.strip():
                    try:
                        findings.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        # L2: 只捕 JSON 解析错误
                        pass
            
            if not findings:
                return ToolResult(
                    success=True,
                    data="🔍 TruffleHog 扫描完成，未发现密钥泄露",
                    metadata={"findings_count": 0}
                )
            
            output_parts = ["🔍 TruffleHog 密钥扫描结果\n"]
            output_parts.append(f"⚠️ 发现 {len(findings)} 处密钥泄露!\n")
            
            for i, finding in enumerate(findings[:20]):
                verified = "✅ 已验证有效" if finding.get("Verified") else "⚠️ 未验证"
                output_parts.append(f"\n🔴 [{i+1}] {finding.get('DetectorName', 'unknown')} - {verified}")
                output_parts.append(f"   文件: {finding.get('SourceMetadata', {}).get('Data', {}).get('Filesystem', {}).get('file', '')}")
            
            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={"findings_count": len(findings)}
            )
            
        except Exception as e:
            error_msg = f"TruffleHog 执行错误: {str(e)}"
            return ToolResult(success=False, data=error_msg, error=error_msg)


# ============ OSV-Scanner 工具 ============

class OSVScannerInput(BaseModel):
    """OSV-Scanner 扫描输入"""
    target_path: str = Field(
        default=".",
        description="要扫描的路径。使用 '.' 扫描整个项目（推荐），不要使用项目目录名！"
    )


class OSVScannerTool(AgentTool):
    """
    OSV-Scanner 开源漏洞扫描工具
    
    Google 开源的漏洞扫描工具，使用 OSV 数据库。
    支持多种包管理器和锁文件。
    """
    
    def __init__(self, project_root: str, sandbox_manager: Optional["SandboxManager"] = None):
        super().__init__()
        # 🔥 将相对路径转换为绝对路径，Docker 需要绝对路径
        self.project_root = os.path.abspath(project_root)
        # 🔥 使用共享的 SandboxManager 实例，避免重复初始化
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "osv_scan"
    
    @property
    def description(self) -> str:
        return """使用 OSV-Scanner 扫描开源依赖漏洞。
Google 开源的漏洞扫描工具。

⚠️ 重要提示: target_path 使用 '.' 扫描整个项目，不要使用项目目录名！

支持:
- package.json (npm)
- requirements.txt (Python)
- go.mod (Go)
- Cargo.lock (Rust)
- pom.xml (Maven)
- composer.lock (PHP)"""
    
    @property
    def args_schema(self):
        return OSVScannerInput
    
    async def _execute(
        self,
        target_path: str = ".",
        **kwargs
    ) -> ToolResult:
        """执行 OSV-Scanner"""
        # 确保 Docker 可用
        await self.sandbox_manager.initialize()
        if not self.sandbox_manager.is_available:
            error_msg = f"OSV-Scanner unavailable: {self.sandbox_manager.get_diagnosis()}"
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # 🔥 使用公共函数进行智能路径解析
        safe_target_path, host_check_path, error_msg = _smart_resolve_target_path(
            target_path, self.project_root, "OSV-Scanner"
        )
        if error_msg:
            return ToolResult(success=False, data=error_msg, error=error_msg)

        # OSV-Scanner
        cmd = ["osv-scanner", "--json", "-r", safe_target_path]
        cmd_str = " ".join(cmd)
        
        try:
            result = await self.sandbox_manager.execute_tool_command(
                command=cmd_str,
                host_workdir=self.project_root,
                timeout=120
            )
            
            stdout = result['stdout']
            
            try:
                results = json.loads(stdout)
            except (json.JSONDecodeError, ValueError):
                # L2: 只捕 JSON 解析错误
                if "no package sources found" in stdout.lower():
                    return ToolResult(success=True, data="OSV-Scanner: 未找到可扫描的包文件")
                return ToolResult(success=True, data=f"OSV-Scanner 输出:\n{stdout[:1000]}")
            
            vulns = results.get("results", [])
            
            if not vulns:
                return ToolResult(
                    success=True,
                    data="📋 OSV-Scanner 扫描完成，未发现依赖漏洞",
                    metadata={"findings_count": 0}
                )
            
            total_vulns = sum(len(r.get("vulnerabilities", [])) for r in vulns)
            
            output_parts = ["📋 OSV-Scanner 开源漏洞扫描结果\n"]
            output_parts.append(f"发现 {total_vulns} 个漏洞:\n")
            
            for result in vulns[:10]:
                source = result.get("source", {}).get("path", "unknown")
                for vuln in result.get("vulnerabilities", [])[:5]:
                    vuln_id = vuln.get("id", "")
                    summary = vuln.get("summary", "")[:100]
                    output_parts.append(f"\n🔴 {vuln_id}")
                    output_parts.append(f"   来源: {source}")
                    output_parts.append(f"   描述: {summary}")
            
            return ToolResult(
                success=True,
                data="\n".join(output_parts),
                metadata={"findings_count": total_vulns}
            )
            
        except Exception as e:
            error_msg = f"OSV-Scanner 执行错误: {str(e)}"
            return ToolResult(success=False, data=error_msg, error=error_msg)


# ============ 导出所有工具 ============

__all__ = [
    "SemgrepTool",
    "BanditTool",
    "GitleaksTool",
    "NpmAuditTool",
    "SafetyTool",
    "TruffleHogTool",
    "OSVScannerTool",
]

