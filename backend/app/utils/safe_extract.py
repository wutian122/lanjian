"""
安全 ZIP 解压工具

用于替代直接调用 `zipfile.ZipFile.extractall()`，抵御 Zip Slip / Zip Bomb 攻击。

## 攻击面

1. **Zip Slip**：条目名包含 ``../`` 或绝对路径（如 ``/etc/passwd`` 或 Windows 绝对路径），
   ``extractall`` 会跟随符号在解压目录之外写文件；
2. **Zip Bomb**：极高压缩比条目（如 42.zip），可解出 TB 级数据打爆磁盘 / 内存；
3. **符号链接注入**：ZIP 支持 symlink 条目（``external_attr`` 高位 == 0xA），
   解压后如果被读代码继续解引用可能读到解压目录之外的文件；
4. **超大条目数量**：条目数无限时可拖慢遍历、耗尽 inode。

## 使用方式

```python
from app.utils.safe_extract import safe_extract, SafeExtractError
import zipfile

with zipfile.ZipFile(zip_path, "r") as zf:
    try:
        safe_extract(zf, dest_dir)
    except SafeExtractError as e:
        # 记录并拒绝上传，切勿静默继续
        raise HTTPException(status_code=400, detail=str(e))
```

## 阈值

默认阈值按项目当前一次代码审计的规模保守估计；如需针对某场景放宽，
显式传参，不要就地改常量。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
import zipfile


# 默认阈值 —— 一次代码审计上传的最大 ZIP 通常远低于此，
# 如触发说明大概率恶意。
DEFAULT_MAX_TOTAL_SIZE = 500 * 1024 * 1024  # 500 MB 总解压体积
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024   # 100 MB 单条目
DEFAULT_MAX_ENTRIES = 10_000                # 10k 条目
DEFAULT_MAX_PATH_LENGTH = 4096              # 单条路径 4KB，够长路径正常代码，短到能挡拒绝服务


class SafeExtractError(ValueError):
    """
    安全解压检查失败的统一异常。

    与 zipfile.BadZipFile 保持独立：BadZipFile 是格式坏，SafeExtractError 是格式对
    但内容可疑（Zip Slip / Zip Bomb / 符号链接等）。上层应把它当输入校验失败，
    返回 400，而不是 500。
    """


def _is_within(parent: Path, target: Path) -> bool:
    """target 是否严格在 parent 之内（等于 parent 本身也算，用于兼容 dest_dir 空条目）。"""
    try:
        target.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def safe_extract(
    zip_ref: zipfile.ZipFile,
    dest_dir: os.PathLike | str,
    *,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH,
) -> None:
    """
    安全地把 ``zip_ref`` 里的所有条目解压到 ``dest_dir``。

    等价于 ``assert_safe_zip(...)`` 通过后调用 ``zip_ref.extract`` 逐条写盘。
    通过所有检查后才写盘，确保任一条目违规时不留下部分产物。

    如果调用方需要**在解压过程中做别的事**（例如按条目粒度检查取消信号），
    使用 :func:`assert_safe_zip` 先做静态检查，再自行循环 ``zip_ref.extract``。

    Args:
        zip_ref: 已经 ``ZipFile(path, "r")`` 打开的对象，调用方负责关闭。
        dest_dir: 解压目标目录，不存在会被创建（但父目录必须存在）。

    Raises:
        SafeExtractError: 任一条目未通过安全检查。
    """
    dest = assert_safe_zip(
        zip_ref,
        dest_dir,
        max_total_size=max_total_size,
        max_file_size=max_file_size,
        max_entries=max_entries,
        max_path_length=max_path_length,
    )
    # 全量检查通过，逐条解压
    for info in zip_ref.infolist():
        zip_ref.extract(info, dest)


def assert_safe_zip(
    zip_ref: zipfile.ZipFile,
    dest_dir: os.PathLike | str,
    *,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH,
) -> Path:
    """
    对 ``zip_ref`` 做全量静态安全检查（**不解压**），返回已 resolve 的 ``dest_dir``。

    调用方可在此函数返回后自行循环 ``zip_ref.extract(info, dest)``，
    以便穿插取消检查、进度回调等副作用。

    检查条目：

    1. 条目数 ≤ ``max_entries``；
    2. 条目名不为空 / 长度 ≤ ``max_path_length``；
    3. 条目名不是绝对路径（如 ``/etc/passwd`` / Windows 带驱动器路径）；
    4. 拼接后的目标真实路径必须在 ``dest_dir`` 之内（防 ``../`` 逃逸）；
    5. 条目声明的 uncompressed size ≤ ``max_file_size``；
    6. 所有条目 uncompressed size 之和 ≤ ``max_total_size``；
    7. 拒绝符号链接条目（``external_attr`` 高位 == 0xA），避免符号链接欺骗。

    Returns:
        已 resolve 且已 mkdir 的 dest 路径。

    Raises:
        SafeExtractError: 任一条目未通过安全检查。
    """
    dest = Path(dest_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    infos = zip_ref.infolist()

    if len(infos) > max_entries:
        raise SafeExtractError(
            f"zip has too many entries: {len(infos)} > {max_entries}"
        )

    total_size = 0
    for info in infos:
        name = info.filename
        if not name:
            raise SafeExtractError("zip contains an entry with empty name")
        if len(name) > max_path_length:
            raise SafeExtractError(
                f"zip entry name too long ({len(name)} > {max_path_length}): {name[:80]}..."
            )

        # 拒绝绝对路径 / 驱动器（Windows ZIP 也可能带 C:）
        if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
            raise SafeExtractError(f"zip entry uses absolute path: {name!r}")

        # 拒绝符号链接条目（ZIP Unix 外部属性高 4 位 == 0xA 表示 symlink）
        if (info.external_attr >> 28) == 0xA:
            raise SafeExtractError(f"zip entry is a symlink (not allowed): {name!r}")

        # 拼接后落点必须在 dest 内
        target = (dest / name).resolve()
        if not _is_within(dest, target):
            raise SafeExtractError(
                f"zip entry escapes destination (zip slip): {name!r} -> {target}"
            )

        if info.file_size > max_file_size:
            raise SafeExtractError(
                f"zip entry too large: {name!r} declared {info.file_size} bytes "
                f"> {max_file_size}"
            )
        total_size += info.file_size
        if total_size > max_total_size:
            raise SafeExtractError(
                f"zip total uncompressed size exceeds {max_total_size} bytes "
                f"(reached {total_size} at entry {name!r})"
            )

    return dest
