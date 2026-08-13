"""
Path Traversal 防护工具

## 攻击面

Agent 系列工具（sandbox_vuln / sandbox_language / sandbox_tool / smart_scan_tool /
run_code / reporting_tool / external_tools）都在把 LLM 返回的相对路径与
``self.project_root`` 拼接后直接 ``open()`` / ``exec()`` / ``read_text()``。

LLM 完全可以返回 ``../../etc/passwd`` 或 ``/etc/shadow`` 甚至 ``file.txt`` 后跟一个
指向宿主 ``/etc`` 的符号链接。任何一处不校验都会让 Agent 读到项目根之外的文件，
在多租户 / 沙箱不隔离的部署里等同远程任意文件读取。

## 使用方式

```python
from app.services.agent.utils.path_safety import resolve_safe_path, UnsafePathError

try:
    full_path = resolve_safe_path(self.project_root, target_file)
except UnsafePathError as e:
    return ToolResult(success=False, error=f"路径不安全: {e}")
```

替换原来的：
```python
full_path = os.path.join(self.project_root, target_file)   # 危险，允许 ../ 逃逸
```

## 语义约束

- ``project_root`` 必须已存在（用户上传/克隆的项目根）；否则视为编程错误抛
  ``UnsafePathError``（不吞异常，让上层看到）
- ``user_path`` 允许字符串或 ``PathLike``；空字符串/绝对路径/含空字节都拒绝
- 返回的 :class:`pathlib.Path` **已 resolve**，可直接给 ``open()`` 用；如需字符串
  ``str(safe)`` 即可
- 校验时不要求目标文件存在（否则无法用来创建新文件）；上层根据业务决定是否再
  ``exists()`` 检查
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

PathLike = Union[str, os.PathLike]


class UnsafePathError(ValueError):
    """
    路径通不过 Path Traversal 检查时抛出。

    独立于 ``FileNotFoundError`` / ``OSError``：调用方应把它当"输入不合法"处理
    （通常是 4xx / 工具返回错误结果），而不是把内部路径细节返回给 LLM。
    """


def resolve_safe_path(project_root: PathLike, user_path: PathLike) -> Path:
    """
    把 ``user_path`` 解析为一个安全的、约束在 ``project_root`` 之内的绝对路径。

    检查项：

    1. ``project_root`` 非空且存在（否则任何 ``user_path`` 都无从判断范围）
    2. ``user_path`` 非空、非纯空白、不含空字节 ``\\0``
    3. ``user_path`` 不能是绝对路径（Unix ``/etc/passwd`` 或 Windows ``C:\\foo``）
    4. 拼接并 ``resolve()`` 后的真实路径必须严格在 ``project_root.resolve()`` 之内
       （相等也允许 —— 允许调用方传空文件名以定位根本身的极少数场景是
       :func:`resolve_safe_root`，此函数不允许空）
    5. 拒绝 UNC 路径（Windows ``\\\\server\\share\\...``）

    这里选择用 ``resolve()`` 处理符号链接：**返回值指向 symlink 的最终 target**，
    然后再检查 target 是否在 project_root 之内。这样即使 ``project_root/link``
    是一个指向 ``/etc`` 的符号链接，也会因为 target 不在项目内而被拒绝。

    Args:
        project_root: 项目根目录（用户上传的 ZIP 解压目录 / clone 目录）。
        user_path: 由 LLM / 上游工具产生的相对路径。

    Returns:
        安全的绝对 ``Path``，可直接用于文件 IO。

    Raises:
        UnsafePathError: 任意一项检查失败。
    """
    # 1) project_root 校验
    if project_root is None or (isinstance(project_root, str) and not project_root.strip()):
        raise UnsafePathError("project_root is empty")
    root = Path(project_root)
    if not root.exists():
        raise UnsafePathError(f"project_root does not exist: {root}")
    if not root.is_dir():
        raise UnsafePathError(f"project_root is not a directory: {root}")

    # 2) user_path 基本合法性
    if user_path is None:
        raise UnsafePathError("user_path is None")
    raw = os.fspath(user_path)
    if not raw or not raw.strip():
        raise UnsafePathError("user_path is empty")
    if "\0" in raw:
        raise UnsafePathError("user_path contains NUL byte")

    # 3) 拒绝绝对路径 / UNC 路径
    if os.path.isabs(raw):
        raise UnsafePathError(f"user_path is absolute: {raw!r}")
    # Windows UNC 会被 os.path.isabs 认成 True，但保险再挡一次
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise UnsafePathError(f"user_path uses UNC form: {raw!r}")

    # 4) resolve 后必须落在 root 之内（跟随符号链接）
    root_real = root.resolve()
    target_real = (root_real / raw).resolve()

    try:
        target_real.relative_to(root_real)
    except ValueError:
        raise UnsafePathError(
            f"path escapes project_root: {raw!r} -> {target_real} (root={root_real})"
        )

    return target_real


def is_safe_path(project_root: PathLike, user_path: PathLike) -> bool:
    """
    :func:`resolve_safe_path` 的布尔版本，便于在条件表达式里使用。

    只在调用方明确想"不安全就静默跳过"时用，绝大多数场景应直接调用
    :func:`resolve_safe_path` 并让异常自然传播 —— 静默跳过会掩盖攻击尝试。
    """
    try:
        resolve_safe_path(project_root, user_path)
        return True
    except UnsafePathError:
        return False
