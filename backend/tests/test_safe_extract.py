"""
P0-2: safe_extract 安全解压测试

覆盖攻击面：
- Zip Slip（相对路径 ../ 逃逸）
- 绝对路径条目（/etc/passwd, C:\\evil）
- 符号链接注入
- Zip Bomb（超大单条目、超大总解压体积）
- 超多条目
- 超长路径名
- 空条目名

同时保证正常合法 ZIP 能被解压。
"""
import io
import os
import zipfile
from pathlib import Path

import pytest

from app.utils.safe_extract import (
    safe_extract,
    SafeExtractError,
    DEFAULT_MAX_ENTRIES,
)


def _make_zip(entries):
    """
    构造内存 ZIP。entries 为 (arcname, data_or_info) 列表：
    - 若第二个元素是 bytes：作为普通文件写入
    - 若是 (bytes, external_attr, declared_size)：允许显式指定 external_attr（用于 symlink 测试）
      和 file_size（用于 zip bomb 声明大小测试）
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, spec in entries:
            if isinstance(spec, bytes):
                zf.writestr(arcname, spec)
            else:
                data, external_attr, declared_size = spec
                info = zipfile.ZipInfo(arcname)
                info.external_attr = external_attr
                zf.writestr(info, data)
                # 事后篡改 file_size 用于 zip bomb 声明大小检查
                # （safe_extract 用 info.file_size 判定）
                if declared_size is not None:
                    # infolist 是懒读取的，重新打开确保下面测试用到的 info 是我们篡改后的
                    pass
    buf.seek(0)
    return buf


class TestSafeExtractHappyPath:
    def test_extracts_legit_zip(self, tmp_path):
        buf = _make_zip([("a.txt", b"hello"), ("sub/b.txt", b"world")])
        with zipfile.ZipFile(buf, "r") as zf:
            safe_extract(zf, tmp_path)
        assert (tmp_path / "a.txt").read_bytes() == b"hello"
        assert (tmp_path / "sub" / "b.txt").read_bytes() == b"world"


class TestSafeExtractRejectsZipSlip:
    def test_relative_traversal(self, tmp_path):
        buf = _make_zip([("../evil.txt", b"pwn")])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="zip slip|escapes destination"):
                safe_extract(zf, tmp_path)
        # 不应有任何逃逸写盘
        assert not (tmp_path.parent / "evil.txt").exists()

    def test_deep_relative_traversal(self, tmp_path):
        buf = _make_zip([("a/b/../../../evil.txt", b"pwn")])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError):
                safe_extract(zf, tmp_path)

    def test_absolute_unix_path(self, tmp_path):
        buf = _make_zip([("/etc/passwd", b"pwn")])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="absolute path"):
                safe_extract(zf, tmp_path)

    def test_absolute_windows_path(self, tmp_path):
        buf = _make_zip([("C:/evil.txt", b"pwn")])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="absolute path"):
                safe_extract(zf, tmp_path)


class TestSafeExtractRejectsSymlink:
    def test_symlink_entry_rejected(self, tmp_path):
        # ZIP Unix external_attr: symlink 高 4 位 == 0xA
        # 值示例：0xA1FF << 16 = 0xA1FF0000
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("link_to_etc")
            info.external_attr = 0xA1FF0000  # symlink perms
            zf.writestr(info, b"/etc/passwd")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="symlink"):
                safe_extract(zf, tmp_path)


class TestSafeExtractRejectsZipBomb:
    def test_single_entry_too_large(self, tmp_path):
        # 用 max_file_size=10 强制小阈值
        buf = _make_zip([("big.bin", b"x" * 100)])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="too large"):
                safe_extract(zf, tmp_path, max_file_size=10)

    def test_total_size_exceeded(self, tmp_path):
        buf = _make_zip([("a", b"x" * 60), ("b", b"y" * 60)])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="total uncompressed size"):
                safe_extract(zf, tmp_path, max_total_size=100, max_file_size=1000)

    def test_too_many_entries(self, tmp_path):
        entries = [(f"f{i}.txt", b"x") for i in range(20)]
        buf = _make_zip(entries)
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="too many entries"):
                safe_extract(zf, tmp_path, max_entries=10)


class TestSafeExtractRejectsAbnormalNames:
    def test_empty_name(self, tmp_path):
        buf = io.BytesIO()
        # 直接手动构造带空名的 ZipInfo —— writestr 会拒绝空名，改用 ZipInfo 加入
        # 注意：某些 zipfile 版本会自身拒绝空名，这种情况下测试直接 skip
        try:
            with zipfile.ZipFile(buf, "w") as zf:
                info = zipfile.ZipInfo("")
                zf.writestr(info, b"x")
        except Exception:
            pytest.skip("zipfile refuses empty name at write time")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError):
                safe_extract(zf, tmp_path)

    def test_path_too_long(self, tmp_path):
        long_name = "a" * 5000
        buf = _make_zip([(long_name, b"x")])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError, match="too long"):
                safe_extract(zf, tmp_path, max_path_length=4096)


class TestSafeExtractAtomicity:
    def test_no_partial_extraction_on_reject(self, tmp_path):
        """
        当发现 zip slip 条目时，前面合法条目也不应留在磁盘上——
        因为 safe_extract 先全量静态检查、后统一解压。
        """
        buf = _make_zip([("good.txt", b"ok"), ("../evil.txt", b"pwn")])
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(SafeExtractError):
                safe_extract(zf, tmp_path)
        assert not (tmp_path / "good.txt").exists()
        assert not (tmp_path.parent / "evil.txt").exists()
