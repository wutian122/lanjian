"""
Wave 0.1 P0 修复测试：docker-compose 拆分 --reload

根因：服务器上 `/root/lanjian/docker-compose.yml` 的 backend command 带 `--reload`，
      文件变更会触发 uvicorn 重启，SSE 连接全断，内存 orchestrator 状态丢失。
修复：主 docker-compose.yml 明确不带 --reload（走镜像 CMD 或显式指定 --workers 1）；
      docker-compose.override.yml 保留开发用 --reload。

参考：openspec/changes/fix-sse-realtime-stream/design.md D0 决策
"""
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_compose(name: str) -> dict:
    path = REPO_ROOT / name
    assert path.exists(), f"{name} 应存在于仓库根目录"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_backend_command(cfg: dict) -> str:
    """提取 backend 服务的 command 字段（可能是 str 或 list）"""
    backend = (cfg.get("services") or {}).get("backend") or {}
    cmd = backend.get("command")
    if cmd is None:
        return ""
    if isinstance(cmd, list):
        return " ".join(str(c) for c in cmd)
    return str(cmd)


class TestProdComposeNoReload:
    """生产 compose (docker-compose.prod.yml 与主 docker-compose.yml) 后端不得带 --reload"""

    def test_prod_compose_backend_command_has_no_reload(self):
        """docker-compose.prod.yml 若显式指定 backend command，不得含 --reload"""
        cfg = _load_compose("docker-compose.prod.yml")
        cmd = _get_backend_command(cfg)
        # 允许无 command（走镜像 CMD，entrypoint 已确认无 reload）
        # 若有 command，必须不含 --reload
        assert "--reload" not in cmd, (
            f"生产 compose backend.command 不得含 --reload，当前: {cmd!r}"
        )

    def test_main_compose_backend_command_has_no_reload(self):
        """docker-compose.yml (主) backend.command 不得含 --reload

        主 compose 是 `docker compose up` 默认使用的，若带 --reload，
        任何文件变更都会重启后端进程，SSE 全断。
        --reload 只应在 override.yml 里出现（开发者显式选用）。
        """
        cfg = _load_compose("docker-compose.yml")
        cmd = _get_backend_command(cfg)
        assert "--reload" not in cmd, (
            f"主 compose backend.command 不得含 --reload（应移到 override.yml），当前: {cmd!r}"
        )


class TestOverrideComposeHasReload:
    """docker-compose.override.yml 保留开发用 --reload（可选，允许缺失）"""

    def test_override_backend_command_when_present_has_reload(self):
        """如果 override.yml 定义了 backend.command，则应含 --reload（开发用）"""
        cfg = _load_compose("docker-compose.override.yml")
        backend = (cfg.get("services") or {}).get("backend")
        if not backend:
            pytest.skip("override.yml 未定义 backend 服务（可选）")
        cmd = _get_backend_command(cfg)
        if not cmd:
            pytest.skip("override.yml backend 未定义 command（可选）")
        assert "--reload" in cmd, (
            f"override.yml backend.command 存在时应含 --reload（开发热更），当前: {cmd!r}"
        )
