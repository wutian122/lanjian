"""
P1-0: path_safety.resolve_safe_path 单元测试

覆盖：
- happy path（子目录 / 直接文件 / 深层子目录）
- 各种 Path Traversal（../ / ..\\ / 混合 / 绝对路径 / UNC / 空 / 空字节）
- 符号链接跟随后必须仍在 root 之内
"""
import os
from pathlib import Path

import pytest

from app.services.agent.utils.path_safety import (
    resolve_safe_path,
    is_safe_path,
    UnsafePathError,
)


@pytest.fixture
def project(tmp_path):
    """创建一个 project_root 目录及若干合法子文件。"""
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("print('a')")
    (root / "sub").mkdir()
    (root / "sub" / "b.py").write_text("print('b')")
    return root


class TestHappyPath:
    def test_direct_file(self, project):
        got = resolve_safe_path(project, "a.py")
        assert got == (project / "a.py").resolve()

    def test_nested_file(self, project):
        got = resolve_safe_path(project, "sub/b.py")
        assert got == (project / "sub" / "b.py").resolve()

    def test_backslash_nested_file_on_windows(self, project):
        # Windows 路径分隔符也应能识别
        got = resolve_safe_path(project, "sub\\b.py")
        assert got == (project / "sub" / "b.py").resolve()

    def test_nonexistent_file_ok(self, project):
        """只校验路径不逃逸，不要求文件存在（否则无法用于新建文件）。"""
        got = resolve_safe_path(project, "new_file.py")
        assert got == (project / "new_file.py").resolve()


class TestPathTraversalRejected:
    def test_relative_traversal(self, project):
        with pytest.raises(UnsafePathError, match="escapes project_root"):
            resolve_safe_path(project, "../evil.txt")

    def test_deep_relative_traversal(self, project):
        with pytest.raises(UnsafePathError, match="escapes project_root"):
            resolve_safe_path(project, "sub/../../evil.txt")

    def test_absolute_unix_path(self, project):
        with pytest.raises(UnsafePathError, match="absolute"):
            resolve_safe_path(project, "/etc/passwd")

    def test_absolute_windows_path(self, project):
        with pytest.raises(UnsafePathError, match="absolute"):
            resolve_safe_path(project, "C:/Windows/System32/config/sam")

    def test_unc_path(self, project):
        with pytest.raises(UnsafePathError, match="absolute|UNC"):
            resolve_safe_path(project, "\\\\attacker\\share\\evil.txt")


class TestInvalidInputs:
    def test_empty_user_path(self, project):
        with pytest.raises(UnsafePathError, match="empty"):
            resolve_safe_path(project, "")

    def test_whitespace_user_path(self, project):
        with pytest.raises(UnsafePathError, match="empty"):
            resolve_safe_path(project, "   ")

    def test_null_byte(self, project):
        with pytest.raises(UnsafePathError, match="NUL byte"):
            resolve_safe_path(project, "a.py\0.txt")

    def test_none_user_path(self, project):
        with pytest.raises(UnsafePathError):
            resolve_safe_path(project, None)

    def test_missing_project_root(self, tmp_path):
        with pytest.raises(UnsafePathError, match="does not exist"):
            resolve_safe_path(tmp_path / "nope", "a.py")

    def test_project_root_is_file(self, tmp_path):
        f = tmp_path / "afile.txt"
        f.write_text("x")
        with pytest.raises(UnsafePathError, match="not a directory"):
            resolve_safe_path(f, "a.py")

    def test_empty_project_root(self):
        with pytest.raises(UnsafePathError, match="empty"):
            resolve_safe_path("", "a.py")


class TestSymlinkFollowed:
    def test_symlink_target_outside_root_rejected(self, tmp_path, project):
        """
        project/link -> /tmp/other/secret.txt 这种符号链接必须被 resolve 后拒绝，
        即使 link 本身在 project 内。
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("pwned")

        link = project / "link"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")

        with pytest.raises(UnsafePathError, match="escapes project_root"):
            resolve_safe_path(project, "link")

    def test_symlink_target_inside_root_ok(self, project):
        """指向 root 内的符号链接允许。"""
        real = project / "a.py"
        link = project / "alias.py"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")

        got = resolve_safe_path(project, "alias.py")
        assert got == real.resolve()


class TestIsSafeHelper:
    def test_is_safe_true(self, project):
        assert is_safe_path(project, "a.py") is True

    def test_is_safe_false(self, project):
        assert is_safe_path(project, "../evil") is False
