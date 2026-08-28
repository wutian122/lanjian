"""
Finding 文件路径解析工具

## 背景（E2E 2026-08-28 实证）

LLM 产出的 ``file_path`` 形态不可控（裸文件名 / 沙箱前缀 / 绝对路径 / 带行号 /
反斜杠分隔），而 ZIP 项目解压后实际文件可能位于 ``project_root/src/`` 等子目录
层级。旧逻辑用 ``os.path.join(project_root, clean_path)`` 单点校验，把已通过
确定性沙箱验证的真实 finding 误杀为"幻觉发现"（15 个声明仅 5 条落库，服务器
日志 ``[Orchestrator] 🚫 过滤幻觉发现: 文件不存在 'vuln.js'`` 铁证）。

## 语义

- 只在项目树中**实际找到**文件才返回解析结果——幻觉过滤能力不回退；
- 返回相对 ``project_root`` 的 POSIX 分隔符路径（可直接写库 / 用于挂载拼接）；
- 找不到返回 ``None``，由调用方决定过滤（保留既有"拒绝幻觉"语义）。

## 使用方式

```python
from app.services.agent.utils.finding_path import resolve_project_file

resolved = resolve_project_file(project_root, finding.get("file_path"))
if resolved is None:
    return None  # 幻觉，过滤
finding["file_path"] = resolved  # 归一化写回，后续验证/落库/展示统一
```
"""
from __future__ import annotations

import os
from typing import Iterator, Optional

# 容器/沙箱视角前缀（LLM 常把容器内路径当宿主机路径输出）
_SANDBOX_PREFIXES = ("/workspace/src/", "/workspace/")

# ZIP 样本常见子目录层级（打包时带 src/ 目录）
_SRC_SUBDIR = "src"

# basename 递归查找的目录深度上限（防性能退化）
_MAX_WALK_DEPTH = 5


def _iter_files_bounded(root: str, max_depth: int) -> Iterator[str]:
    """os.walk 限定目录深度，避免整树无界遍历。"""
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def resolve_project_file(project_root: str, file_path: str) -> Optional[str]:
    """把 LLM 输出的 file_path 解析为项目树中真实存在的文件。

    尝试顺序：
    1. 剥离 ``/workspace/src/``、``/workspace/`` 沙箱前缀后按相对路径拼接
    2. 原样相对路径拼接（含 ``src/...`` 前缀）
    3. 裸文件名时追加 ``src/`` 子目录层级
    4. 绝对路径直接存在性检查
    5. basename 限深度递归兜底（嵌套子目录场景）

    Args:
        project_root: 项目根目录（ZIP 解压目录 / clone 目录）。
        file_path: LLM 输出的路径（可能含 ``:行号``、反斜杠、沙箱前缀）。

    Returns:
        相对 ``project_root`` 的归一化路径（POSIX 分隔符）；找不到返回 ``None``。
    """
    raw = (file_path or "").strip()
    if not raw:
        return None

    # 剥离行号（"app.py:36" -> "app.py"；注意 Windows 盘符 "C:\x" 冒号误剥离风险，
    # 仅当冒号后为纯数字时才剥离）
    clean = raw
    if ":" in raw:
        head, _, tail = raw.partition(":")
        if tail.isdigit():
            clean = head.strip()

    # 剥离沙箱前缀
    for prefix in _SANDBOX_PREFIXES:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break

    # 绝对路径：仅当真实存在时返回（LLM 偶发输出宿主机绝对路径）
    if os.path.isabs(clean):
        if os.path.isfile(clean):
            return os.path.relpath(clean, project_root).replace(os.sep, "/")
        return None

    # 统一分隔符
    clean = clean.replace("\\", "/").lstrip("/")

    candidates = [clean]
    # 裸文件名（无目录层级）追加 src/ 子目录尝试
    if "/" not in clean:
        candidates.append(f"{_SRC_SUBDIR}/{clean}")

    for cand in candidates:
        full = os.path.join(project_root, cand)
        if os.path.isfile(full):
            return cand

    # basename 限深度递归兜底
    base = clean.rsplit("/", 1)[-1]
    if base:
        for found in _iter_files_bounded(project_root, _MAX_WALK_DEPTH):
            if os.path.basename(found) == base:
                return os.path.relpath(found, project_root).replace(os.sep, "/")

    return None
