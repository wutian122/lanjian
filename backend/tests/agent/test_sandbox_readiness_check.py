"""Task 6: 沙箱就绪真实检查 —— 镜像预检与真就绪事件。

覆盖 spec「沙箱就绪状态 MUST 真实可检」两个 Scenario：
- daemon 可达但镜像缺失 → is_available=False、诊断含镜像名、就绪事件 failed
- daemon 可达且镜像存在 → is_available=True、就绪事件 done（行为与现状一致）

mock 边界：docker SDK（from_env / ping / images.get）全部 monkeypatch，不连真 docker。
"""

from pathlib import Path
from unittest.mock import MagicMock

import docker
import pytest
from docker.errors import DockerException, ImageNotFound

from app.services.agent.tools.sandbox_tool import SandboxConfig, SandboxManager

SANDBOX_IMAGE = "wutian449/lanjian-sandbox:v6.1.0"
AGENT_TASKS_PATH = (
    Path(__file__).resolve().parents[2]
    / "app" / "api" / "v1" / "endpoints" / "agent_tasks.py"
)


class _FakeImages:
    """模拟 docker client.images：missing 集合中的镜像 get 时抛 ImageNotFound。"""

    def __init__(self, missing: set[str] | None = None):
        self._missing = missing or set()
        self.checked: list[str] = []

    def get(self, image):
        self.checked.append(image)
        if image in self._missing:
            raise ImageNotFound(f"No such image: {image}")
        return MagicMock(tags=[image])


class _FakeClient:
    """模拟 DockerClient：ping 可配置失败，images 用 _FakeImages。"""

    def __init__(self, images: _FakeImages, ping_error: Exception | None = None):
        self.images = images
        self._ping_error = ping_error
        self.ping_calls = 0

    def ping(self):
        self.ping_calls += 1
        if self._ping_error is not None:
            raise self._ping_error
        return True


def _make_manager() -> SandboxManager:
    return SandboxManager(config=SandboxConfig(image=SANDBOX_IMAGE))


def _patch_docker(monkeypatch, client: _FakeClient | None = None, from_env_error: Exception | None = None):
    """patch docker.from_env：返回 fake client 或抛连接错误。"""
    if from_env_error is not None:
        def _raise(*args, **kwargs):
            raise from_env_error
        monkeypatch.setattr(docker, "from_env", _raise)
    else:
        assert client is not None
        monkeypatch.setattr(docker, "from_env", lambda *args, **kwargs: client)
    return client


# ---------- Scenario 1: 镜像缺失 → 不可用 + 诊断含镜像名 ----------

@pytest.mark.asyncio
async def test_initialize_marks_unavailable_when_image_missing(monkeypatch):
    """daemon 正常（ping 通过）但 images.get 抛 ImageNotFound → 沙箱不可用。"""
    fake_images = _FakeImages(missing={SANDBOX_IMAGE})
    client = _FakeClient(images=fake_images)
    _patch_docker(monkeypatch, client=client)

    manager = _make_manager()
    await manager.initialize()

    # 镜像检查确实发生过，且查的是配置的镜像
    assert SANDBOX_IMAGE in fake_images.checked
    assert manager.is_available is False
    assert manager._init_error is not None
    # 诊断必须含镜像名（spec Scenario: 含镜像名与诊断）
    assert SANDBOX_IMAGE in manager._init_error
    assert SANDBOX_IMAGE in manager.get_diagnosis()
    # 必须与"docker 连不上"区分开
    assert "镜像" in manager._init_error


# ---------- Scenario 2: 全部就绪 → 可用 ----------

@pytest.mark.asyncio
async def test_initialize_available_when_image_present(monkeypatch):
    """daemon 可达且镜像存在 → is_available=True，诊断为可用。"""
    fake_images = _FakeImages(missing=set())
    client = _FakeClient(images=fake_images)
    _patch_docker(monkeypatch, client=client)

    manager = _make_manager()
    await manager.initialize()

    assert SANDBOX_IMAGE in fake_images.checked
    assert manager.is_available is True
    assert manager._init_error is None
    assert manager.get_diagnosis() == "Docker Service Available"


# ---------- daemon 连接失败：与镜像缺失区分 ----------

@pytest.mark.asyncio
async def test_initialize_unavailable_when_daemon_unreachable(monkeypatch):
    """from_env 抛错（daemon 不可达）→ 不可用，诊断归因为连接失败而非镜像。"""
    _patch_docker(monkeypatch, from_env_error=DockerException("Error while fetching server API version"))

    manager = _make_manager()
    await manager.initialize()

    assert manager.is_available is False
    assert manager._init_error is not None
    assert "Docker 连接失败" in manager._init_error
    # 不能误报成镜像缺失
    assert "镜像" not in manager._init_error
    assert "Docker Service Unavailable" in manager.get_diagnosis()


# ---------- 幂等与重试 ----------

@pytest.mark.asyncio
async def test_initialize_idempotent_after_success(monkeypatch):
    """成功初始化后再次 initialize 不重复连接/检查（_initialized 短路）。"""
    fake_images = _FakeImages()
    client = _FakeClient(images=fake_images)
    from_env_calls = []

    def _from_env(*args, **kwargs):
        from_env_calls.append(1)
        return client

    monkeypatch.setattr(docker, "from_env", _from_env)

    manager = _make_manager()
    await manager.initialize()
    assert manager.is_available is True
    await manager.initialize()  # 第二次应短路

    assert len(from_env_calls) == 1
    assert client.ping_calls == 1
    assert len(fake_images.checked) == 1


@pytest.mark.asyncio
async def test_initialize_allows_retry_after_image_missing(monkeypatch):
    """镜像缺失后 _initialized 保持 False：镜像补齐后重试可恢复为可用。"""
    fake_images = _FakeImages(missing={SANDBOX_IMAGE})
    client = _FakeClient(images=fake_images)
    _patch_docker(monkeypatch, client=client)

    manager = _make_manager()
    await manager.initialize()
    assert manager.is_available is False

    # 镜像被拉取到位（images.get 不再抛错），重试初始化
    fake_images._missing.clear()
    await manager.initialize()
    assert manager.is_available is True
    assert manager._init_error is None


# ---------- endpoint 就绪事件分支（源码契约；完整 endpoint 需 DB/Redis 重型 fixture） ----------

def test_agent_tasks_emits_failed_event_when_sandbox_unavailable():
    """agent_tasks.py 就绪事件点：is_available 为 False 时必须发射 failed 事件，
    metadata 含 init_status=failed / diagnosis / image，且任务不中断（无 raise）。"""
    content = AGENT_TASKS_PATH.read_text(encoding="utf-8")

    # 定位就绪事件发射区域（"Docker sandbox ready" 附近）
    marker = '"Docker sandbox ready"'
    idx = content.find(marker)
    assert idx != 0 and marker in content, "就绪事件发射点不存在"
    # 取发射点前后 1200 字符作为分支区域
    region = content[max(0, idx - 400): idx + 1200]

    # 必须以 is_available 作为分支条件（不再无条件发 ready）
    assert "sandbox_manager.is_available" in region, "就绪事件必须按 is_available 分支"
    # failed 分支三要素
    assert '"init_status": "failed"' in region, "失败分支必须带 init_status=failed"
    assert '"diagnosis"' in region, "失败分支 metadata 必须含 diagnosis"
    assert '"image"' in region, "失败分支 metadata 必须含 image"
    # 任务不中断：失败分支不得 raise（按缩进精确提取 else 块，排除后继代码）
    else_block = None
    region_lines = region.splitlines()
    for i, line in enumerate(region_lines):
        if line.lstrip().startswith("else:"):
            else_indent = len(line) - len(line.lstrip())
            block = [line]
            for nxt in region_lines[i + 1:]:
                if nxt.strip() == "":
                    block.append(nxt)
                    continue
                indent = len(nxt) - len(nxt.lstrip())
                if indent <= else_indent:
                    break
                block.append(nxt)
            else_block = "\n".join(block)
            break
    assert else_block is not None, "必须存在 else（失败）分支"
    assert "raise " not in else_block, "沙箱不可用不得中断任务"
    # 消息文本必须明确"任务继续但沙箱验证不可用"语义
    assert "任务继续" in else_block, "失败消息必须说明任务继续"
