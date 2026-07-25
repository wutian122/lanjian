"""
沙箱执行工具
在 Docker 沙箱中执行代码和命令进行漏洞验证
"""

import asyncio
import json
import logging
import tempfile
import os
import shutil
import shlex
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from dataclasses import dataclass

from .base import AgentTool, ToolResult
from app.core.config import settings
from ..utils.path_safety import resolve_safe_path, UnsafePathError

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    """沙箱配置"""
    image: str = None  # 默认从 settings.SANDBOX_IMAGE 读取
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout: int = 60
    network_mode: str = "none"  # none, bridge, host
    read_only: bool = True
    user: str = "1000:1000"
    network_enabled: bool = False  # 是否允许沙箱联网

    def __post_init__(self):
        if self.image is None:
            self.image = settings.SANDBOX_IMAGE
        # 从系统配置读取联网设置
        try:
            self.network_enabled = getattr(settings, 'SANDBOX_NETWORK_ENABLED', False)
        except Exception:
            pass
        if self.network_enabled:
            self.network_mode = "bridge"


class SandboxManager:
    """
    沙箱管理器
    管理 Docker 容器的创建、执行和清理
    """
    
    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self._docker_client = None
        self._initialized = False
        self._init_error = None
    
    async def initialize(self):
        """初始化 Docker 客户端"""
        if self._initialized:
            logger.info("✅ SandboxManager already initialized")
            return

        try:
            import docker
            logger.info(f"🔄 Attempting to connect to Docker... (lib: {docker.__file__})")
            self._docker_client = docker.from_env()
            # 测试连接
            self._docker_client.ping()
            self._initialized = True
            self._init_error = None
            logger.info("✅ Docker sandbox manager initialized successfully")
        except ImportError as e:
            logger.error(f"❌ Docker library not installed: {e}")
            self._docker_client = None
            self._init_error = f"ImportError: {e}"
        except Exception as e:
            logger.warning(f"❌ Docker not available: {e}")
            import traceback
            logger.warning(f"Docker connection traceback: {traceback.format_exc()}")
            self._docker_client = None
            self._init_error = f"{type(e).__name__}: {str(e)}"
    
    @property
    def is_available(self) -> bool:
        """检查 Docker 是否可用"""
        return self._docker_client is not None
        
    def get_diagnosis(self) -> str:
        """获取诊断信息"""
        if self.is_available:
            return "Docker Service Available"
        return f"Docker Service Unavailable. Error: {self._init_error or 'Not initialized'}"
    
    async def execute_command(
        self,
        command: str,
        working_dir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        network_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        在沙箱中执行命令
        
        Args:
            command: 要执行的命令
            working_dir: 工作目录
            env: 环境变量
            timeout: 超时时间（秒）
            
        Returns:
            执行结果
        """
        if not self.is_available:
            return {
                "success": False,
                "error": "Docker 不可用",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
            }
        
        timeout = timeout or self.config.timeout

        # 禁用代理环境变量：从宿主机环境显式剔除代理变量，避免空字符串干扰 pip/curl
        _proxy_keys = {
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy",
        }
        container_env = {k: v for k, v in os.environ.items() if k not in _proxy_keys}
        container_env["NO_PROXY"] = "*"
        container_env["no_proxy"] = "*"
        container_env["HOME"] = "/home/sandbox"
        # 注入 pip 镜像源配置，确保运行时 pip install 使用国内源
        container_env["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
        container_env["PIP_TRUSTED_HOST"] = "pypi.tuna.tsinghua.edu.cn"
        # 合并用户传入的环境变量（用户变量优先）
        container_env.update(env or {})

        # 清除代理环境变量：在命令前添加 unset（双重保险，与 execute_tool_command 一致）
        unset_proxy_prefix = "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy 2>/dev/null; "
        wrapped_command = unset_proxy_prefix + command

        try:
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                # 准备容器配置
                container_config = {
                    "image": self.config.image,
                    "command": ["sh", "-c", wrapped_command],
                    "detach": True,
                    "mem_limit": self.config.memory_limit,
                    "cpu_period": 100000,
                    "cpu_quota": int(100000 * self.config.cpu_limit),
                    "network_mode": network_mode or self.config.network_mode,
                    "user": self.config.user,
                    "read_only": self.config.read_only,
                    "volumes": {
                        temp_dir: {"bind": "/workspace", "mode": "rw"},
                    },
                    "tmpfs": {
                            "/home/sandbox": "rw,size=512m,mode=1777",
                            "/tmp": "rw,size=256m,mode=1777"
                        },
                    "working_dir": working_dir or "/workspace",
                    "environment": container_env,
                    # 安全配置
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    # 防止 Docker SDK 从 Docker Hub 拉取镜像
                    "use_config_proxy": False,
                }
                
                # 创建并启动容器
                container = await asyncio.to_thread(
                    self._docker_client.containers.run,
                    **container_config
                )
                
                try:
                    # 等待执行完成
                    result = await asyncio.wait_for(
                        asyncio.to_thread(container.wait),
                        timeout=timeout
                    )
                    
                    # 获取日志
                    stdout = await asyncio.to_thread(
                        container.logs, stdout=True, stderr=False
                    )
                    stderr = await asyncio.to_thread(
                        container.logs, stdout=False, stderr=True
                    )
                    
                    return {
                        "success": result["StatusCode"] == 0,
                        "stdout": stdout.decode('utf-8', errors='ignore')[:50000],
                        "stderr": stderr.decode('utf-8', errors='ignore')[:10000],
                        "exit_code": result["StatusCode"],
                        "error": None,
                    }
                    
                except asyncio.TimeoutError:
                    await asyncio.to_thread(container.kill)
                    return {
                        "success": False,
                        "error": f"执行超时 ({timeout}秒)",
                        "stdout": "",
                        "stderr": "",
                        "exit_code": -1,
                    }
                    
                finally:
                    # 清理容器
                    await asyncio.to_thread(container.remove, force=True)
                    
        except Exception as e:
            logger.error(f"Sandbox execution error: {e}")
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
            }
    
    async def execute_tool_command(
        self,
        command: str,
        host_workdir: str,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        network_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        在沙箱中对指定目录执行工具命令
        
        Args:
            command: 要执行的命令
            host_workdir: 宿主机上的工作目录（将被挂载到 /workspace）
            timeout: 超时时间
            env: 环境变量
            network_mode: 网络模式 (none, bridge, host)
            
        Returns:
            执行结果
        """
        if not self.is_available:
            return {
                "success": False,
                "error": "Docker 不可用",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
            }
        
        timeout = timeout or self.config.timeout

        # read network mode from config if not specified
        if network_mode is None:
            network_mode = "bridge" if self.config.network_enabled else "none"


        # 禁用代理环境变量：从宿主机环境显式剔除代理变量
        _proxy_keys = {
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy",
        }
        container_env = {k: v for k, v in os.environ.items() if k not in _proxy_keys}
        container_env["NO_PROXY"] = "*"
        container_env["no_proxy"] = "*"
        container_env["HOME"] = "/home/sandbox"
        # 注入 pip 镜像源配置
        container_env["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
        container_env["PIP_TRUSTED_HOST"] = "pypi.tuna.tsinghua.edu.cn"
        # 合并用户传入的环境变量（用户变量优先）
        container_env.update(env or {})

        try:
            host_workdir = os.path.abspath(host_workdir)
            if not os.path.isdir(host_workdir):
                return {
                    "success": False,
                    "error": f"项目目录不存在: {host_workdir}",
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                }
            if len(host_workdir) < 2 or host_workdir == os.path.abspath(os.sep):
                return {
                    "success": False,
                    "error": f"无效的工作目录: {host_workdir}",
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                }

            # 清除代理环境变量：在命令前添加 unset（双重保险）
            unset_proxy_prefix = "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy 2>/dev/null; "
            wrapped_command = unset_proxy_prefix + command

            # 准备容器配置
            container_config = {
                "image": self.config.image,
                "command": ["sh", "-c", wrapped_command],
                "detach": True,
                "mem_limit": self.config.memory_limit,
                "cpu_period": 100000,
                "cpu_quota": int(100000 * self.config.cpu_limit),
                "network_mode": network_mode,
                "user": self.config.user,
                "read_only": self.config.read_only,
                "volumes": {
                    host_workdir: {"bind": "/workspace/src", "mode": "rw"},  # 根因1: 统一挂载到 /workspace/src（与 execute_with_files 一致），PoC 脚本的 /workspace/src/{file_path} 可找到文件
                },
                "tmpfs": {
                    "/home/sandbox": "rw,size=512m,mode=1777",
                    "/tmp": "rw,size=256m,mode=1777"  # 添加 /tmp 目录供工具写入临时文件
                },
                "working_dir": "/tmp",  # 根因1: 挂载点改 /workspace/src 后，working_dir 用 /tmp（tmpfs 可写）
                "environment": container_env,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                # 防止 Docker SDK 从 Docker Hub 拉取镜像
                "use_config_proxy": False,
            }
            
            # 创建并启动容器
            container = await asyncio.to_thread(
                self._docker_client.containers.run,
                **container_config
            )
            
            try:
                # 等待执行完成
                result = await asyncio.wait_for(
                    asyncio.to_thread(container.wait),
                    timeout=timeout
                )
                
                # 获取日志
                stdout = await asyncio.to_thread(
                    container.logs, stdout=True, stderr=False
                )
                stderr = await asyncio.to_thread(
                    container.logs, stdout=False, stderr=True
                )
                
                return {
                    "success": result["StatusCode"] == 0,
                    "stdout": stdout.decode('utf-8', errors='ignore')[:50000], # 增大日志限制
                    "stderr": stderr.decode('utf-8', errors='ignore')[:5000],
                    "exit_code": result["StatusCode"],
                    "error": None,
                }
                
            except asyncio.TimeoutError:
                await asyncio.to_thread(container.kill)
                return {
                    "success": False,
                    "error": f"执行超时 ({timeout}秒)",
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                }
                
            finally:
                # 清理容器
                await asyncio.to_thread(container.remove, force=True)
                
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return {
                "success": False,
                "error": str(e),
                "stderr": "",
                "exit_code": -1,
            }

    async def execute_with_files(
        self,
        command: str,
        host_project_dir: str,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        network_mode: str = "none",
    ) -> Dict[str, Any]:
        """
        在沙箱中执行命令，同时挂载项目源文件（只读）和 PoC 工作区（可读写）
        
        挂载结构：
          /workspace/src/ -> 宿主机项目目录（只读）
          /workspace/poc/ -> 临时目录（可读写，用于写 PoC 脚本）
        """
        if not self.is_available:
            return {
                "success": False, "error": "Docker not available",
                "stdout": "", "stderr": "", "exit_code": -1,
            }

        timeout = timeout or self.config.timeout
        # 禁用代理环境变量：从宿主机环境剔除代理变量
        _proxy_keys = {
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy",
        }
        container_env = {k: v for k, v in os.environ.items() if k not in _proxy_keys}
        container_env["NO_PROXY"] = "*"
        container_env["no_proxy"] = "*"
        container_env["HOME"] = "/home/sandbox"
        container_env["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
        container_env["PIP_TRUSTED_HOST"] = "pypi.tuna.tsinghua.edu.cn"
        container_env.update(env or {})

        try:
            host_project_dir = os.path.abspath(host_project_dir)
            if not os.path.isdir(host_project_dir):
                return {
                    "success": False,
                    "error": "Project dir not found: " + host_project_dir,
                    "stdout": "", "stderr": "", "exit_code": -1,
                }

            with tempfile.TemporaryDirectory() as poc_dir:
                unset_proxy = "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy 2>/dev/null; "
                wrapped_command = unset_proxy + command

                container_config = {
                    "image": self.config.image,
                    "command": ["sh", "-c", wrapped_command],
                    "detach": True,
                    "mem_limit": self.config.memory_limit,
                    "cpu_period": 100000,
                    "cpu_quota": int(100000 * self.config.cpu_limit),
                    "network_mode": network_mode,
                    "user": self.config.user,
                    "read_only": self.config.read_only,
                    "volumes": {
                        host_project_dir: {"bind": "/workspace/src", "mode": "ro"},
                        poc_dir: {"bind": "/workspace/poc", "mode": "rw"},
                    },
                    "tmpfs": {
                        "/home/sandbox": "rw,size=512m,mode=1777",
                        "/tmp": "rw,size=256m,mode=1777",
                    },
                    "working_dir": "/workspace/poc",
                    "environment": container_env,
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    # 防止 Docker SDK 从 Docker Hub 拉取镜像
                    "use_config_proxy": False,
                }

                container = await asyncio.to_thread(
                    self._docker_client.containers.run, **container_config
                )

                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(container.wait), timeout=timeout
                    )
                    stdout = await asyncio.to_thread(
                        container.logs, stdout=True, stderr=False
                    )
                    stderr = await asyncio.to_thread(
                        container.logs, stdout=False, stderr=True
                    )
                    return {
                        "success": result["StatusCode"] == 0,
                        "stdout": stdout.decode('utf-8', errors='ignore')[:50000],
                        "stderr": stderr.decode('utf-8', errors='ignore')[:5000],
                        "exit_code": result["StatusCode"],
                        "error": None,
                    }
                except asyncio.TimeoutError:
                    await asyncio.to_thread(container.kill)
                    return {
                        "success": False, "error": "Timeout (" + str(timeout) + "s)",
                        "stdout": "", "stderr": "", "exit_code": -1,
                    }
                finally:
                    await asyncio.to_thread(container.remove, force=True)

        except Exception as e:
            logger.error("Sandbox execute_with_files error: " + str(e))
            return {
                "success": False, "error": str(e),
                "stdout": "", "stderr": "", "exit_code": -1,
            }

    async def execute_poc(
        self,
        poc_code: str,
        host_project_dir: str,
        timeout: int = 60,
        network_enabled: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute PoC code in sandbox with base64 encoding (avoids quoting issues).
        Mounts project source files to /workspace/src/
        """
        import base64
        encoded = base64.b64encode(poc_code.encode("utf-8")).decode("ascii")
        command = "echo '" + encoded + "' | base64 -d > /workspace/poc/__poc.py && python3 /workspace/poc/__poc.py"
        
        net_mode = "bridge" if (network_enabled or self.config.network_enabled) else "none"
        
        return await self.execute_with_files(
            command=command,
            host_project_dir=host_project_dir,
            timeout=timeout,
            network_mode=net_mode,
        )

    async def execute_python(
        self,
        code: str,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        在沙箱中执行 Python 代码
        
        Args:
            code: Python 代码
            timeout: 超时时间
            
        Returns:
            执行结果
        """
        # 将代码通过 base64 写入临时文件执行，避免 python3 -c + sh -c 的引号嵌套问题
        # 根因1: 挂载点改 /workspace/src 后，PoC 写 /tmp（tmpfs 可写）
        import base64
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        command = f"echo '{encoded}' | base64 -d > /tmp/__poc.py && python3 /tmp/__poc.py"
        return await self.execute_command(command, timeout=timeout)
    
    async def execute_http_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[str] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        在沙箱中执行 HTTP 请求
        
        Args:
            method: HTTP 方法
            url: URL
            headers: 请求头
            data: 请求体
            timeout: 超时
            
        Returns:
            HTTP 响应
        """
        # 构建 curl 命令
        curl_parts = ["curl", "-s", "-S", "-w", "'\\n%{http_code}'", "-X", method]
        
        if headers:
            for key, value in headers.items():
                curl_parts.extend(["-H", f"'{key}: {value}'"])
        
        if data:
            curl_parts.extend(["-d", f"'{data}'"])
        
        curl_parts.append(f"'{url}'")
        
        command = " ".join(curl_parts)
        
        # 使用带网络的镜像
        original_network = self.config.network_mode
        self.config.network_mode = "bridge"  # 允许网络访问
        
        try:
            result = await self.execute_command(command, timeout=timeout)
            
            if result["success"] and result["stdout"]:
                lines = result["stdout"].strip().split('\n')
                if lines:
                    status_code = lines[-1].strip()
                    body = '\n'.join(lines[:-1])
                    return {
                        "success": True,
                        "status_code": int(status_code) if status_code.isdigit() else 0,
                        "body": body[:5000],
                        "error": None,
                    }
            
            return {
                "success": False,
                "status_code": 0,
                "body": "",
                "error": result.get("error") or result.get("stderr"),
            }
            
        finally:
            self.config.network_mode = original_network
    
    async def verify_vulnerability(
        self,
        vulnerability_type: str,
        target_url: str,
        payload: str,
        expected_pattern: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        验证漏洞
        
        Args:
            vulnerability_type: 漏洞类型
            target_url: 目标 URL
            payload: 攻击载荷
            expected_pattern: 期望在响应中匹配的模式
            
        Returns:
            验证结果
        """
        verification_result = {
            "vulnerability_type": vulnerability_type,
            "target_url": target_url,
            "payload": payload,
            "is_vulnerable": False,
            "evidence": None,
            "error": None,
        }
        
        try:
            # 发送请求
            response = await self.execute_http_request(
                method="GET" if "?" in target_url else "POST",
                url=target_url,
                data=payload if "?" not in target_url else None,
            )
            
            if not response["success"]:
                verification_result["error"] = response.get("error")
                return verification_result
            
            body = response.get("body", "")
            status_code = response.get("status_code", 0)
            
            # 检查响应
            if expected_pattern:
                import re
                if re.search(expected_pattern, body, re.IGNORECASE):
                    verification_result["is_vulnerable"] = True
                    verification_result["evidence"] = f"响应中包含预期模式: {expected_pattern}"
            else:
                # 根据漏洞类型进行通用检查
                if vulnerability_type == "sql_injection":
                    error_patterns = [
                        r"SQL syntax",
                        r"mysql_fetch",
                        r"ORA-\d+",
                        r"PostgreSQL.*ERROR",
                        r"SQLite.*error",
                        r"ODBC.*Driver",
                    ]
                    for pattern in error_patterns:
                        if re.search(pattern, body, re.IGNORECASE):
                            verification_result["is_vulnerable"] = True
                            verification_result["evidence"] = f"SQL错误信息: {pattern}"
                            break
                
                elif vulnerability_type == "xss":
                    if payload in body:
                        verification_result["is_vulnerable"] = True
                        verification_result["evidence"] = "XSS payload 被反射到响应中"
                
                elif vulnerability_type == "command_injection":
                    # 检查命令执行结果
                    if "uid=" in body or "root:" in body:
                        verification_result["is_vulnerable"] = True
                        verification_result["evidence"] = "命令执行成功"
            
            verification_result["response_status"] = status_code
            verification_result["response_length"] = len(body)
            
        except Exception as e:
            verification_result["error"] = str(e)
        
        return verification_result


class SandboxCommandInput(BaseModel):
    """沙箱命令输入"""
    command: str = Field(description="要执行的命令")
    timeout: int = Field(default=30, description="超时时间（秒）")
    network_enabled: bool = Field(default=False, description="允许网络访问（SSRF/HTTP 验证时设为 true）")
    language: str = Field(default="", description="可选的执行语言提示")
    files: Optional[Dict[str, str]] = Field(default=None, description="写入沙箱的临时文件 {文件名: 内容}")


class SandboxTool(AgentTool):
    """
    沙箱执行工具
    在安全隔离的环境中执行代码和命令
    """

    # 允许的命令前缀 - 放宽限制以支持更灵活的测试
    ALLOWED_COMMANDS = [
        # 编程语言解释器
        "python", "python3", "node", "php", "ruby", "perl",
        "go", "java", "javac", "bash", "sh",
        # 网络工具
        "curl", "wget", "nc", "netcat",
        # 文件操作
        "cat", "head", "tail", "grep", "find", "ls", "wc",
        "sed", "awk", "cut", "sort", "uniq", "tr", "xargs",
        # 系统信息（用于验证命令执行）
        "echo", "printf", "test", "id", "whoami", "uname",
        "env", "printenv", "pwd", "hostname",
        # 编码/解码工具
        "base64", "xxd", "od", "hexdump",
        # 其他实用工具
        "timeout", "time", "sleep", "true", "false",
        "md5sum", "sha256sum", "strings",
    ]
    
    def __init__(self, sandbox_manager: Optional[SandboxManager] = None, project_root: Optional[str] = None):
        super().__init__()
        self.sandbox_manager = sandbox_manager or SandboxManager()
        self.project_root = os.path.abspath(project_root) if project_root else None
    
    @property
    def name(self) -> str:
        return "sandbox_exec"
    
    @property
    def description(self) -> str:
        return """在安全沙箱中执行命令或代码。
用于验证漏洞、测试 PoC 或执行安全检查。

⚠️ 安全限制:
- 命令在 Docker 容器中执行
- 网络默认隔离 (network_enabled: false)
- **SSRF/网络验证请设置 network_enabled: true**
- 资源有限制
- 只允许特定命令

参数:
- command: 要执行的命令 (必填)
- timeout: 超时秒数 (默认30)
- network_enabled: 是否允许网络 (默认false, SSRF/HTTP验证设true)

允许的命令: python, python3, node, curl, wget, cat, grep, find, ls, echo, id

使用场景:
- 验证命令注入漏洞
- 执行 PoC 代码
- SSRF/网络漏洞验证 (network_enabled: true)
- 测试 payload 效果"""
    
    @property
    def args_schema(self):
        return SandboxCommandInput
    
    async def _execute(
        self,
        command: str,
        timeout: int = 30,
        network_enabled: bool = False,
        **kwargs
    ) -> ToolResult:
        """执行沙箱命令"""
        # 初始化沙箱
        await self.sandbox_manager.initialize()
        
        if not self.sandbox_manager.is_available:
            return ToolResult(
                success=False,
                error="沙箱环境不可用（Docker 未安装或未运行）",
            )
        
        # 安全检查：验证命令是否允许
        cmd_parts = command.strip().split()
        if not cmd_parts:
            return ToolResult(success=False, error="命令不能为空")
        
        base_cmd = cmd_parts[0]
        if base_cmd not in self.ALLOWED_COMMANDS and not any(
            base_cmd.startswith(allowed) for allowed in ["python", "php", "node", "ruby", "go", "java"]
        ):
            return ToolResult(
                success=False,
                error=f"命令 '{base_cmd}' 不在允许列表中。允许的命令: {', '.join(self.ALLOWED_COMMANDS)}",
            )
        
        # C1-fix: detect python -c commands and use base64 encoding to avoid quoting issues
        import re as _re
        _py_inline_match = _re.match(r'(python3?)\s+-c\s+(.+)', command.strip(), _re.DOTALL)
        if _py_inline_match:
            _inline_code = _py_inline_match.group(2).strip()
            if len(_inline_code) >= 2 and _inline_code[0] == _inline_code[-1] and _inline_code[0] in ('"', "'"):
                _inline_code = _inline_code[1:-1]
            logger.info("[SandboxTool] python -c detected, using base64 encoding to avoid quoting issues")
            _py_result = await self.sandbox_manager.execute_python(_inline_code, timeout=timeout)
            # Format result the same way as non-python path
            _output_parts = ["Sandbox result (python -c, base64 encoded)\n"]
            _output_parts.append(f"SandboxTool python -c (base64 encoded)")
            _output_parts.append(f"\u9000\u51fa\u7801: {_py_result['exit_code']}")
            if _py_result["stdout"]:
                _output_parts.append(f"\n\u6807\u51c6\u8f93\u51fa:\n```\n{_py_result['stdout']}\n```")
            if _py_result["stderr"]:
                _output_parts.append(f"\n\u6807\u51c6\u9519\u8bef:\n```\n{_py_result['stderr']}\n```")
            if _py_result.get("error"):
                _output_parts.append(f"\n\u9519\u8bef: {_py_result['error']}")
            return ToolResult(
                success=_py_result["success"],
                data="\n".join(_output_parts),
                error=_py_result.get("error"),
                metadata={
                    "command": command,
                    "exit_code": _py_result["exit_code"],
                    "network_mode": "none",
                    "timeout": timeout,
                    "stdout_summary": (_py_result.get("stdout") or "")[:2000],
                    "stderr_summary": (_py_result.get("stderr") or "")[:1000],
                    "success": _py_result["success"],
                },
            )

        effective_workdir = (
            os.path.abspath(self.project_root)
            if self.project_root and os.path.isdir(self.project_root)
            else None
        )
        needs_project_mount = self._command_needs_project_mount(command)
        network_mode = "bridge" if network_enabled else "none"

        if effective_workdir and needs_project_mount:
            result = await self.sandbox_manager.execute_tool_command(
                command=command,
                host_workdir=effective_workdir,
                timeout=timeout,
                network_mode=network_mode,
            )
        else:
            result = await self.sandbox_manager.execute_command(
                command=command,
                timeout=timeout,
                network_mode=network_mode,
            )
        
        # 格式化输出
        output_parts = ["🐳 沙箱执行结果\n"]
        output_parts.append(f"命令: {command}")
        output_parts.append(f"网络模式: {'bridge (允许网络)' if network_enabled else 'none (隔离)'}")
        output_parts.append(f"退出码: {result['exit_code']}")
        
        if result["stdout"]:
            output_parts.append(f"\n标准输出:\n```\n{result['stdout']}\n```")
        
        if result["stderr"]:
            output_parts.append(f"\n标准错误:\n```\n{result['stderr']}\n```")
        
        if result.get("error"):
            output_parts.append(f"\n错误: {result['error']}")
        
        return ToolResult(
            success=result["success"],
            data="\n".join(output_parts),
            error=result.get("error"),
            metadata={
                "command": command,
                "exit_code": result["exit_code"],
                "network_mode": "bridge" if network_enabled else "none",
                "timeout": timeout,
                "stdout_summary": (result.get("stdout") or "")[:2000],
                "stderr_summary": (result.get("stderr") or "")[:1000],
                "success": result["success"],
            }
        )

    @staticmethod
    def _command_needs_project_mount(command: str) -> bool:
        # 根因1修复: 优先检查是否含 /workspace/ 路径引用（包括 heredoc 写入的脚本内容），
        # 再对纯 heredoc 写入（不引用项目文件）返回 False
        if '/workspace/' in command:
            return True
        if '<<' in command:
            return False

        try:
            parts = shlex.split(command, posix=True)
        except ValueError:
            return True

        if not parts:
            return False

        interpreter_prefixes = {"python", "python3", "node", "php", "ruby", "perl", "sh", "bash"}
        inline_flags = ("-c", "-e", "-r")
        if parts[0] in interpreter_prefixes and any(flag in parts for flag in inline_flags):
            return False

        for part in parts[1:]:
            if part.startswith("-"):
                continue
            if "/" in part or "\\" in part:
                return True
            _, ext = os.path.splitext(part)
            if ext in {".py", ".js", ".ts", ".php", ".rb", ".pl", ".sh", ".java", ".go"}:
                return True
        return False


class HttpRequestInput(BaseModel):
    """HTTP 请求输入"""
    method: str = Field(default="GET", description="HTTP 方法 (GET, POST, PUT, DELETE)")
    url: str = Field(description="请求 URL")
    headers: Optional[Dict[str, str]] = Field(default=None, description="请求头")
    data: Optional[str] = Field(default=None, description="请求体")
    timeout: int = Field(default=30, description="超时时间（秒）")


class SandboxHttpTool(AgentTool):
    """
    沙箱 HTTP 请求工具
    在沙箱中发送 HTTP 请求
    """
    
    def __init__(self, sandbox_manager: Optional[SandboxManager] = None):
        super().__init__()
        self.sandbox_manager = sandbox_manager or SandboxManager()
    
    @property
    def name(self) -> str:
        return "sandbox_http"
    
    @property
    def description(self) -> str:
        return """在沙箱中发送 HTTP 请求。
用于测试 Web 漏洞如 SQL 注入、XSS、SSRF 等。

输入:
- method: HTTP 方法
- url: 请求 URL
- headers: 可选，请求头
- data: 可选，请求体
- timeout: 超时时间

使用场景:
- 验证 SQL 注入漏洞
- 测试 XSS payload
- 验证 SSRF 漏洞
- 测试认证绕过"""
    
    @property
    def args_schema(self):
        return HttpRequestInput
    
    async def _execute(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        data: Optional[str] = None,
        timeout: int = 30,
        **kwargs
    ) -> ToolResult:
        """执行 HTTP 请求"""
        try:
            await self.sandbox_manager.initialize()
        except Exception as e:
            logger.warning(f"Sandbox init failed during execution: {e}")
        
        if not self.sandbox_manager.is_available:
            return ToolResult(
                success=False,
                error="沙箱环境不可用 (Docker Unavailable)",
            )
        
        result = await self.sandbox_manager.execute_http_request(
            method=method,
            url=url,
            headers=headers,
            data=data,
            timeout=timeout,
        )
        
        output_parts = ["🌐 HTTP 请求结果\n"]
        output_parts.append(f"请求: {method} {url}")
        
        if headers:
            output_parts.append(f"请求头: {json.dumps(headers, ensure_ascii=False)}")
        
        if data:
            output_parts.append(f"请求体: {data[:500]}")
        
        output_parts.append(f"\n状态码: {result.get('status_code', 'N/A')}")
        
        if result.get("body"):
            body = result["body"]
            if len(body) > 2000:
                body = body[:2000] + f"\n... (截断，共 {len(result['body'])} 字符)"
            output_parts.append(f"\n响应内容:\n```\n{body}\n```")
        
        if result.get("error"):
            output_parts.append(f"\n错误: {result['error']}")
        
        return ToolResult(
            success=result["success"],
            data="\n".join(output_parts),
            error=result.get("error"),
            metadata={
                "method": method,
                "url": url,
                "status_code": result.get("status_code"),
                "response_length": len(result.get("body", "")),
            }
        )


class SandboxBrowserInput(BaseModel):
    """Q2: 沙箱浏览器验证输入"""
    action: str = Field(
        description="浏览器动作: navigate(导航), screenshot(截图), eval(执行JS), click(点击), get_text(提取文本)"
    )
    url: Optional[str] = Field(default=None, description="目标 URL（navigate/screenshot/eval/click/get_text 需要）")
    selector: Optional[str] = Field(default=None, description="CSS 选择器（click/get_text 需要）")
    script: Optional[str] = Field(default=None, description="要执行的 JS 代码（eval 需要）")
    timeout: int = Field(default=30, description="超时时间（秒）")


class SandboxBrowserTool(AgentTool):
    """
    Q2: 沙箱浏览器验证工具
    用 playwright 在 Docker 沙箱内驱动 chromium 执行浏览器自动化验证。
    用于 XSS（反射型/DOM型）、开放重定向、SSRF 等需真实浏览器渲染的漏洞验证。
    复用 sandbox_manager.execute_python 执行 playwright 脚本，不放宽 ALLOWED_COMMANDS。
    """

    def __init__(self, sandbox_manager: Optional[SandboxManager] = None):
        super().__init__()
        self.sandbox_manager = sandbox_manager or SandboxManager()

    @property
    def name(self) -> str:
        return "sandbox_browser"

    @property
    def description(self) -> str:
        return """在沙箱中用无头浏览器(chromium)执行验证，用于 XSS/开放重定向/SSRF 等需浏览器渲染的漏洞。
动作:
- navigate: 导航到 URL，返回页面标题/最终URL/状态
- screenshot: 导航并截图（返回截图信息）
- eval: 导航后执行 JS，返回结果（用于检测 DOM 型 XSS）
- click: 导航后点击元素
- get_text: 导航后提取元素文本
参数: action, url, selector, script, timeout
示例: sandbox_browser(action="navigate", url="http://target/xss?payload=<script>alert(1)</script>")
示例: sandbox_browser(action="eval", url="http://target/", script="document.body.innerHTML")"""

    @property
    def args_schema(self):
        return SandboxBrowserInput

    def _build_playwright_script(self, action: str, url: Optional[str], selector: Optional[str], script: Optional[str], timeout: int) -> str:
        """根据 action 生成 playwright python 脚本"""
        timeout_ms = timeout * 1000
        # chromium 启动参数：Docker 内必需 --no-sandbox；--disable-dev-shm-usage 规避 /dev/shm 不足
        launch_args = '["--no-sandbox", "--headless", "--disable-gpu", "--disable-dev-shm-usage"]'

        action_body = ""
        if action == "navigate":
            action_body = f'''
        page.goto({url!r}, wait_until="domcontentloaded", timeout={timeout_ms})
        result = {{"title": page.title(), "url": page.url, "status": "ok"}}
'''
        elif action == "screenshot":
            action_body = f'''
        page.goto({url!r}, wait_until="domcontentloaded", timeout={timeout_ms})
        path = "/workspace/screenshot.png"
        page.screenshot(path=path)
        result = {{"screenshot": path, "title": page.title(), "url": page.url}}
'''
        elif action == "eval":
            action_body = f'''
        page.goto({url!r}, wait_until="domcontentloaded", timeout={timeout_ms})
        result = {{"result": str(page.evaluate({script!r}))}}
'''
        elif action == "click":
            action_body = f'''
        page.goto({url!r}, wait_until="domcontentloaded", timeout={timeout_ms})
        page.click({selector!r}, timeout={timeout_ms})
        result = {{"clicked": True, "url": page.url}}
'''
        elif action == "get_text":
            # selector 为 None 时提取 body 文本（用 None 字面量而非字符串）
            if selector:
                action_body = f'''
        page.goto({url!r}, wait_until="domcontentloaded", timeout={timeout_ms})
        text = page.inner_text({selector!r})
        result = {{"text": text[:2000], "url": page.url}}
'''
            else:
                action_body = f'''
        page.goto({url!r}, wait_until="domcontentloaded", timeout={timeout_ms})
        text = page.inner_text("body")
        result = {{"text": text[:2000], "url": page.url}}
'''
        else:
            action_body = f'''
        result = {{"error": "unknown action: {action}"}}
'''

        return f'''import json
from playwright.sync_api import sync_playwright

result = {{"error": "not executed"}}
with sync_playwright() as p:
    browser = p.chromium.launch(args={launch_args})
    try:
        page = browser.new_page()
        page.set_default_timeout({timeout_ms})
{action_body}
    except Exception as e:
        result = {{"error": str(e), "type": type(e).__name__}}
    finally:
        browser.close()

print(json.dumps(result, ensure_ascii=False))
'''

    async def _execute(
        self,
        action: str,
        url: Optional[str] = None,
        selector: Optional[str] = None,
        script: Optional[str] = None,
        timeout: int = 30,
        **kwargs
    ) -> ToolResult:
        """执行浏览器验证"""
        try:
            await self.sandbox_manager.initialize()
        except Exception as e:
            logger.warning(f"Sandbox init failed during browser execution: {e}")

        if not self.sandbox_manager.is_available:
            return ToolResult(
                success=False,
                error="沙箱环境不可用 (Docker Unavailable)",
            )

        if action in ("navigate", "screenshot", "eval", "click", "get_text") and not url:
            return ToolResult(success=False, error="url 参数必填（navigate/screenshot/eval/click/get_text 需要）")
        if action == "click" and not selector:
            return ToolResult(success=False, error="selector 参数必填（click 需要）")

        pw_script = self._build_playwright_script(action, url, selector, script, timeout)

        try:
            result = await self.sandbox_manager.execute_python(pw_script, timeout=timeout + 10)
        except Exception as e:
            return ToolResult(success=False, error=f"浏览器执行异常: {e}")

        if not result.get("success"):
            return ToolResult(
                success=False,
                error=f"浏览器执行失败: {result.get('stderr') or result.get('error', 'unknown')}",
                data=result.get("stdout"),
            )

        stdout = result.get("stdout", "").strip()
        output_parts = [f"🌐 浏览器验证结果 (action={action})"]
        if url:
            output_parts.append(f"URL: {url}")

        # 解析最后一行 JSON 结果
        parsed = None
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    break
                except Exception:
                    continue

        if parsed:
            if parsed.get("error"):
                return ToolResult(success=False, error=f"浏览器错误: {parsed['error']}", data="\n".join(output_parts))
            output_parts.append(f"结果: {json.dumps(parsed, ensure_ascii=False)}")
        else:
            output_parts.append(f"输出:\n{stdout[:2000]}")

        return ToolResult(
            success=True,
            data="\n".join(output_parts),
            metadata={"action": action, "url": url, "parsed": parsed},
        )


class VulnerabilityVerifyInput(BaseModel):
    """漏洞验证输入"""
    vulnerability_type: str = Field(description="漏洞类型 (sql_injection, xss, command_injection, etc.)")
    target_url: str = Field(description="目标 URL")
    payload: str = Field(description="攻击载荷")
    expected_pattern: Optional[str] = Field(default=None, description="期望在响应中匹配的正则模式")


class VulnerabilityVerifyTool(AgentTool):
    """
    漏洞验证工具
    在沙箱中验证漏洞是否真实存在
    """
    
    def __init__(self, sandbox_manager: Optional[SandboxManager] = None):
        super().__init__()
        self.sandbox_manager = sandbox_manager or SandboxManager()
    
    @property
    def name(self) -> str:
        return "verify_vulnerability"
    
    @property
    def description(self) -> str:
        return """验证漏洞是否真实存在。
发送包含攻击载荷的请求，分析响应判断漏洞是否可利用。

输入:
- vulnerability_type: 漏洞类型
- target_url: 目标 URL
- payload: 攻击载荷
- expected_pattern: 可选，期望在响应中匹配的模式

支持的漏洞类型:
- sql_injection: SQL 注入
- xss: 跨站脚本
- command_injection: 命令注入
- path_traversal: 路径遍历
- ssrf: 服务端请求伪造"""
    
    @property
    def args_schema(self):
        return VulnerabilityVerifyInput
    
    async def _execute(
        self,
        vulnerability_type: str,
        target_url: str,
        payload: str,
        expected_pattern: Optional[str] = None,
        **kwargs
    ) -> ToolResult:
        """执行漏洞验证"""
        try:
            await self.sandbox_manager.initialize()
        except Exception as e:
            logger.warning(f"Sandbox init failed during execution: {e}")
        
        if not self.sandbox_manager.is_available:
            return ToolResult(
                success=False,
                error="沙箱环境不可用 (Docker Unavailable)",
            )
        
        result = await self.sandbox_manager.verify_vulnerability(
            vulnerability_type=vulnerability_type,
            target_url=target_url,
            payload=payload,
            expected_pattern=expected_pattern,
        )
        
        output_parts = ["🔍 漏洞验证结果\n"]
        output_parts.append(f"漏洞类型: {vulnerability_type}")
        output_parts.append(f"目标: {target_url}")
        output_parts.append(f"Payload: {payload[:200]}")
        
        if result["is_vulnerable"]:
            output_parts.append(f"\n🔴 结果: 漏洞已确认!")
            output_parts.append(f"证据: {result.get('evidence', 'N/A')}")
        else:
            output_parts.append(f"\n🟢 结果: 未能确认漏洞")
            if result.get("error"):
                output_parts.append(f"错误: {result['error']}")
        
        if result.get("response_status"):
            output_parts.append(f"\nHTTP 状态码: {result['response_status']}")
        
        return ToolResult(
            success=True,
            data="\n".join(output_parts),
            metadata={
                "vulnerability_type": vulnerability_type,
                "is_vulnerable": result["is_vulnerable"],
                "evidence": result.get("evidence"),
            }
        )


# ============ PHP 测试工具 ============

class PhpTestInput(BaseModel):
    """PHP 测试输入"""
    php_code: Optional[str] = Field(default=None, description="要执行的 PHP 代码（可选，与 file_path 二选一）")
    file_path: Optional[str] = Field(default=None, description="要测试的 PHP 文件路径（可选，与 php_code 二选一）")
    get_params: Optional[Dict[str, str]] = Field(default=None, description="模拟的 GET 参数，如 {'cmd': 'whoami'}")
    post_params: Optional[Dict[str, str]] = Field(default=None, description="模拟的 POST 参数")
    timeout: int = Field(default=30, description="超时时间（秒）")


class PhpTestTool(AgentTool):
    """
    PHP 代码测试工具
    在沙箱中执行 PHP 代码，支持模拟 GET/POST 参数
    """

    def __init__(self, sandbox_manager: Optional[SandboxManager] = None, project_root: str = "."):
        super().__init__()
        self.sandbox_manager = sandbox_manager or SandboxManager()
        self.project_root = project_root

    @property
    def name(self) -> str:
        return "php_test"

    @property
    def description(self) -> str:
        return """在沙箱中测试 PHP 代码，支持模拟 GET/POST 参数。
专门用于验证 PHP 漏洞（如命令注入、SQL 注入等）。

输入 (二选一):
- php_code: 直接提供要执行的 PHP 代码
- file_path: 项目中的 PHP 文件路径

模拟参数:
- get_params: 模拟 $_GET 参数，如 {"cmd": "whoami", "id": "1"}
- post_params: 模拟 $_POST 参数

示例:
1. 测试命令注入:
   {"file_path": "vuln.php", "get_params": {"cmd": "whoami"}}

2. 直接测试代码:
   {"php_code": "<?php echo shell_exec($_GET['cmd']); ?>", "get_params": {"cmd": "id"}}

⚠️ 在沙箱中执行，不影响真实环境。"""

    @property
    def args_schema(self):
        return PhpTestInput

    async def _execute(
        self,
        php_code: Optional[str] = None,
        file_path: Optional[str] = None,
        get_params: Optional[Dict[str, str]] = None,
        post_params: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        **kwargs
    ) -> ToolResult:
        """执行 PHP 测试"""
        try:
            await self.sandbox_manager.initialize()
        except Exception as e:
            logger.warning(f"Sandbox init failed: {e}")

        if not self.sandbox_manager.is_available:
            return ToolResult(
                success=False,
                error="沙箱环境不可用 (Docker Unavailable)",
            )

        # 构建 PHP 代码
        if file_path:
            # 从文件读取
            import os
            # P1-3: Path Traversal 防护
            try:
                full_path = str(resolve_safe_path(self.project_root, file_path))
            except UnsafePathError as e:
                return ToolResult(success=False, error=f"路径不安全: {e}")
            if not os.path.exists(full_path):
                return ToolResult(
                    success=False,
                    error=f"文件不存在: {file_path}",
                )
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                php_code = f.read()

        if not php_code:
            return ToolResult(
                success=False,
                error="必须提供 php_code 或 file_path",
            )

        # 构建模拟 $_GET 和 $_POST 的包装代码
        wrapper_parts = ["<?php"]

        # 模拟 $_GET
        if get_params:
            for key, value in get_params.items():
                # 安全转义
                escaped_value = value.replace("'", "\\'")
                wrapper_parts.append(f"$_GET['{key}'] = '{escaped_value}';")

        # 模拟 $_POST
        if post_params:
            for key, value in post_params.items():
                escaped_value = value.replace("'", "\\'")
                wrapper_parts.append(f"$_POST['{key}'] = '{escaped_value}';")

        # 移除 php_code 开头的 <?php 标签
        clean_code = php_code.strip()
        if clean_code.startswith("<?php"):
            clean_code = clean_code[5:].strip()
        if clean_code.startswith("<?"):
            clean_code = clean_code[2:].strip()
        if clean_code.endswith("?>"):
            clean_code = clean_code[:-2].strip()

        wrapper_parts.append(clean_code)
        wrapper_parts.append("?>")

        full_php_code = "\n".join(wrapper_parts)

        # 通过 base64 写入临时文件执行，避免 php -r + sh -c 的引号嵌套问题
        import base64
        encoded = base64.b64encode(full_php_code.encode("utf-8")).decode("ascii")
        command = f"echo '{encoded}' | base64 -d > /workspace/__poc.php && php /workspace/__poc.php"

        result = await self.sandbox_manager.execute_command(
            command=command,
            timeout=timeout,
        )

        # 格式化输出
        output_parts = ["🐘 PHP 测试结果\n"]

        if get_params:
            output_parts.append(f"模拟 GET 参数: {get_params}")
        if post_params:
            output_parts.append(f"模拟 POST 参数: {post_params}")

        output_parts.append(f"\n退出码: {result['exit_code']}")

        if result["stdout"]:
            stdout = result["stdout"][:3000]
            output_parts.append(f"\n输出:\n```\n{stdout}\n```")

        if result["stderr"]:
            stderr = result["stderr"][:1000]
            output_parts.append(f"\n错误:\n```\n{stderr}\n```")

        # 判断是否执行成功
        is_vulnerable = False
        evidence = None

        if result["exit_code"] == 0 and result["stdout"]:
            # 检查是否有命令执行输出
            stdout_lower = result["stdout"].lower()
            if get_params and "cmd" in get_params:
                cmd_value = get_params["cmd"].lower()
                # 检查常见命令输出
                if cmd_value in ["whoami", "id"]:
                    if "root" in stdout_lower or "uid=" in stdout_lower or "www-data" in stdout_lower:
                        is_vulnerable = True
                        evidence = f"命令 '{get_params['cmd']}' 执行成功，输出: {result['stdout'][:200]}"
                elif cmd_value.startswith("echo "):
                    expected = cmd_value[5:].lower()
                    if expected in stdout_lower:
                        is_vulnerable = True
                        evidence = f"Echo 命令执行成功"
                else:
                    # 通用检查：有输出就可能成功
                    if len(result["stdout"].strip()) > 0:
                        is_vulnerable = True
                        evidence = f"命令可能执行成功，输出: {result['stdout'][:200]}"

        if is_vulnerable:
            output_parts.append(f"\n🔴 **漏洞确认**: {evidence}")
        else:
            output_parts.append(f"\n🟡 未能确认漏洞执行（可能需要检查输出）")

        return ToolResult(
            success=True,
            data="\n".join(output_parts),
            metadata={
                "exit_code": result["exit_code"],
                "is_vulnerable": is_vulnerable,
                "evidence": evidence,
                "stdout": result["stdout"][:500] if result["stdout"] else None,
            }
        )


# ============ 命令注入专用测试工具 ============

class CommandInjectionTestInput(BaseModel):
    """命令注入测试输入"""
    target_file: str = Field(description="目标文件路径（如 'vuln.php'）")
    param_name: str = Field(default="cmd", description="注入参数名（默认 'cmd'）")
    test_command: str = Field(default="id", description="测试命令（默认 'id'）")
    language: str = Field(default="php", description="目标语言 (php, python, node)")


class CommandInjectionTestTool(AgentTool):
    """
    命令注入专用测试工具
    智能检测和验证命令注入漏洞
    """

    def __init__(self, sandbox_manager: Optional[SandboxManager] = None, project_root: str = "."):
        super().__init__()
        self.sandbox_manager = sandbox_manager or SandboxManager()
        self.project_root = project_root

    @property
    def name(self) -> str:
        return "test_command_injection"

    @property
    def description(self) -> str:
        return """专门用于测试命令注入漏洞的工具。

输入:
- target_file: 目标文件路径
- param_name: 注入参数名（默认 'cmd'）
- test_command: 测试命令（默认 'id'，也可用 'whoami', 'echo test'）
- language: 目标语言（php, python, node）

示例:
{"target_file": "ttt/t.php", "param_name": "cmd", "test_command": "whoami"}

自动执行:
1. 读取目标文件代码
2. 构建包含测试命令的执行环境
3. 在沙箱中执行并分析结果
4. 判断命令注入是否成功"""

    @property
    def args_schema(self):
        return CommandInjectionTestInput

    async def _execute(
        self,
        target_file: str,
        param_name: str = "cmd",
        test_command: str = "id",
        language: str = "php",
        **kwargs
    ) -> ToolResult:
        """执行命令注入测试"""
        try:
            await self.sandbox_manager.initialize()
        except Exception as e:
            logger.warning(f"Sandbox init failed: {e}")

        if not self.sandbox_manager.is_available:
            return ToolResult(
                success=False,
                error="沙箱环境不可用 (Docker Unavailable)",
            )

        import os
        # P1-3: Path Traversal 防护
        try:
            full_path = str(resolve_safe_path(self.project_root, target_file))
        except UnsafePathError as e:
            return ToolResult(success=False, error=f"路径不安全: {e}")

        if not os.path.exists(full_path):
            return ToolResult(
                success=False,
                error=f"文件不存在: {target_file}",
            )

        # 读取文件内容
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            code_content = f.read()

        output_parts = ["🎯 命令注入测试\n"]
        output_parts.append(f"目标文件: {target_file}")
        output_parts.append(f"注入参数: {param_name}")
        output_parts.append(f"测试命令: {test_command}")
        output_parts.append(f"语言: {language}")

        # 根据语言构建测试
        if language.lower() == "php":
            result = await self._test_php_injection(code_content, param_name, test_command)
        elif language.lower() == "python":
            result = await self._test_python_injection(code_content, param_name, test_command)
        else:
            return ToolResult(
                success=False,
                error=f"暂不支持语言: {language}",
            )

        output_parts.append(f"\n退出码: {result['exit_code']}")

        if result.get("stdout"):
            output_parts.append(f"\n命令输出:\n```\n{result['stdout'][:2000]}\n```")

        if result.get("stderr"):
            output_parts.append(f"\n错误输出:\n```\n{result['stderr'][:500]}\n```")

        # 分析结果
        is_vulnerable = False
        evidence = None
        poc = None

        if result["exit_code"] == 0 and result.get("stdout"):
            stdout = result["stdout"].strip()
            # 检查命令执行特征
            if test_command in ["id", "whoami"]:
                if "uid=" in stdout or "root" in stdout or "www-data" in stdout or stdout.strip():
                    is_vulnerable = True
                    evidence = f"命令 '{test_command}' 成功执行，输出: {stdout[:200]}"
                    poc = f"curl 'http://target/{target_file}?{param_name}={test_command}'"
            elif test_command.startswith("echo "):
                expected = test_command[5:]
                if expected in stdout:
                    is_vulnerable = True
                    evidence = f"Echo 测试成功"
                    poc = f"curl 'http://target/{target_file}?{param_name}=echo+test'"
            else:
                if len(stdout) > 0:
                    is_vulnerable = True
                    evidence = f"命令可能执行成功，输出: {stdout[:200]}"
                    poc = f"curl 'http://target/{target_file}?{param_name}={test_command}'"

        if is_vulnerable:
            output_parts.append(f"\n\n🔴 **漏洞已确认!**")
            output_parts.append(f"证据: {evidence}")
            output_parts.append(f"\nPoC: `{poc}`")
        else:
            output_parts.append(f"\n\n🟡 未能确认漏洞")
            if result.get("stderr"):
                output_parts.append(f"可能原因: 执行错误或参数未正确传递")

        return ToolResult(
            success=True,
            data="\n".join(output_parts),
            metadata={
                "is_vulnerable": is_vulnerable,
                "evidence": evidence,
                "poc": poc,
                "exit_code": result["exit_code"],
            }
        )

    async def _test_php_injection(self, code: str, param_name: str, test_command: str) -> Dict[str, Any]:
        """测试 PHP 命令注入"""
        # 构建模拟环境
        wrapper = f"""<?php
$_GET['{param_name}'] = '{test_command}';
$_POST['{param_name}'] = '{test_command}';
$_REQUEST['{param_name}'] = '{test_command}';
"""
        # 移除原代码的 PHP 标签
        clean_code = code.strip()
        if clean_code.startswith("<?php"):
            clean_code = clean_code[5:]
        elif clean_code.startswith("<?"):
            clean_code = clean_code[2:]
        if clean_code.endswith("?>"):
            clean_code = clean_code[:-2]

        full_code = wrapper + clean_code + "\n?>"

        # 转义并执行
        # 通过 base64 写入临时文件执行，避免引号嵌套问题
        import base64
        encoded = base64.b64encode(full_code.encode("utf-8")).decode("ascii")
        command = f"echo '{encoded}' | base64 -d > /workspace/__poc.php && php /workspace/__poc.php"
        return await self.sandbox_manager.execute_command(command, timeout=30)

    async def _test_python_injection(self, code: str, param_name: str, test_command: str) -> Dict[str, Any]:
        """测试 Python 命令注入"""
        # 模拟 request.args.get
        wrapper = f"""
import sys
class MockArgs:
    def get(self, key, default=None):
        if key == '{param_name}':
            return '{test_command}'
        return default

class MockRequest:
    args = MockArgs()
    form = MockArgs()

request = MockRequest()
sys.argv = ['script.py', '{test_command}']

"""
        full_code = wrapper + code

        # 通过 base64 写入临时文件执行，避免引号嵌套问题
        import base64
        encoded = base64.b64encode(full_code.encode("utf-8")).decode("ascii")
        command = f"echo '{encoded}' | base64 -d > /workspace/__poc.py && python3 /workspace/__poc.py"

        return await self.sandbox_manager.execute_command(command, timeout=30)
