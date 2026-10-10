"""P10 A' 步骤 0（T1）：崩坏采集钩子测试。

契约（权威方案 /root/coder/docs/p10-root-cause-plan-2026-10-09.html
第三章步骤 0）：
- ``_detect_output_degeneracy`` 返回形态枚举：F1 字符墙 / F2 循环 /
  F3 字符异常墙 / F4 标点漂移；未命中返回 None（流循环调用点判真值，
  与旧 bool 形态兼容）；
- 掐断分支在 accumulated 等三累计变量置空「之前」，把尾部样本追加写
  ``<AUDIT_TRACE_DIR>/<task_id[:8]>/degenerate_samples.jsonl``——目录
  解析复用 audit_trace.py 的 ``base_dir / task_id[:8]`` 约定；
- JSONL 记录字段：ts / task_id / agent / iteration / degeneracy /
  len / tail / usage；文件权限 0600；追加不覆盖；
- 防护铁律：崩坏文本不对用户固化展示。样本通道独立于 event_manager
  （留存过程零 emit）与 audit_trace 写点。SSE 中会落库的固化事件
  （除 thinking_token/content_token 实时通道外的全部事件，对照
  event_manager skip_db_events）的 message/metadata 不含崩坏片段；
  content_end 的 accumulated 为空；
- 样本写入失败仅 logger.debug，绝不阻断掐断主流程（照常 break、
  返回空串、置 degenerate 形态）。
"""

import json
import os
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents.base import AgentType
from app.services.agent.agents.orchestrator import OrchestratorAgent

TASK_ID = "abcdef1234567890abcdef1234567890abcdef"
TASK_DIR_NAME = TASK_ID[:8]
SAMPLES_FILE = "degenerate_samples.jsonl"


class _FullCapture:
    """捕获完整 AgentEventData（含 message/metadata，供泄漏断言）。"""

    def __init__(self) -> None:
        self.events: List[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def _make_agent() -> OrchestratorAgent:
    # BaseAgent 为抽象类（run 未实现），用具体子类 __new__ 绕过构造
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    config = MagicMock()
    config.name = "TestOrch"
    config.agent_type = AgentType.ORCHESTRATOR
    agent.config = config
    agent.capture = _FullCapture()
    agent.event_emitter = agent.capture
    agent._timeout_config = {
        "llm_first_token_timeout": 30,
        "llm_stream_timeout": 60,
    }
    agent.llm_service = MagicMock()
    agent.task_id = TASK_ID
    agent._iteration = 3
    agent._last_round_usage = None
    agent._last_empty_kind = None
    agent._get_llm_rate_limiter = lambda: MagicMock(acquire=AsyncMock())
    return agent


def _charwall_stream(**kwargs):
    """字符墙流工厂：第 2 块 Q*40 后检测命中 F1，第 3 块永不被拉取。"""

    async def _gen():
        acc = ""
        for piece in (
            "正常审计开头说明。",
            "Q" * 40,
            "崩坏之后的内容不会被拉取" * 10,
        ):
            acc += piece
            yield {
                "type": "token",
                "kind": "content",
                "content": piece,
                "accumulated": acc,
                "accumulated_content": acc,
                "accumulated_reasoning": "",
            }

    return _gen()


async def _run_stream(agent: OrchestratorAgent):
    with patch(
        "app.services.agent.agents.base.get_llm_circuit"
    ) as fake_cb:
        fake_cb.return_value.call = lambda fn: fn()
        return await agent.stream_llm_call(
            [{"role": "user", "content": "x"}]
        )


def _read_records(path) -> List[Dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


class TestDegeneracyFormEnum:
    """形态枚举：四亚型各返回正确标签，无命中返回 None。"""

    def test_f1_char_wall(self):
        agent = _make_agent()
        text = "正常审计分析文本。" + "A" * 35 + "继续"
        assert agent._detect_output_degeneracy(text) == "F1"

    def test_f2_loop(self):
        agent = _make_agent()
        frag = "international law human rights rule of lawdemocracy"[:30]
        text = "分析开始。" + (frag + frag + frag + frag)[:300] + "结尾"
        assert agent._detect_output_degeneracy(text) == "F2"

    def test_f3_garbled_wall(self):
        agent = _make_agent()
        text = "normal prefix n\xffd\xbd\xf4\xff\xe9\xfa\xc7 nginx release"
        assert agent._detect_output_degeneracy(text) == "F3"

    def test_f4_punctuation_drift(self):
        agent = _make_agent()
        # 每段内嵌递增编号（间距 15 字符 < F2 的 25 字符窗口），杜绝重复
        # 子串被 F2 先抓；全文无句读标记、总长 ≥800 → F4。
        text = "".join(
            f"编号{i:04d}无标点漂移检测样本" for i in range(60)
        )
        assert len(text) >= 800
        assert agent._detect_output_degeneracy(text) == "F4"

    def test_none_when_healthy(self):
        agent = _make_agent()
        text = (
            "## 分析结论\n\n"
            "1. src/http/ngx_http_request.c:88 存在路径拼接风险（confidence 0.72）\n"
            "2. 建议 dispatch verification 验证该候选\n"
            "3. " + "-" * 28 + " 分隔线\n"
        )
        assert agent._detect_output_degeneracy(text) is None
        assert agent._detect_output_degeneracy("") is None
        assert agent._detect_output_degeneracy("正常短文本") is None


class TestJSONLSampleRetention:
    @pytest.mark.asyncio
    async def test_sample_written_with_all_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        agent = _make_agent()
        agent.llm_service.chat_completion_stream = _charwall_stream

        out, _ = await _run_stream(agent)

        sample = tmp_path / TASK_DIR_NAME / SAMPLES_FILE
        assert sample.exists(), "掐断后必须留存崩坏样本"
        records = _read_records(sample)
        assert len(records) == 1
        rec = records[0]
        # 字段齐全
        for key in ("ts", "task_id", "agent", "iteration", "degeneracy",
                    "len", "tail", "usage"):
            assert key in rec, f"缺少字段 {key}"
        assert rec["ts"], "时间戳非空"
        assert rec["task_id"] == TASK_ID
        assert rec["agent"] == "TestOrch"
        assert rec["iteration"] == 3
        assert rec["degeneracy"] == "F1"
        expected_len = len("正常审计开头说明。" + "Q" * 40)
        assert rec["len"] == expected_len
        assert rec["len"] > 0
        assert rec["tail"].endswith("Q" * 40)
        assert rec["tail"] == ("正常审计开头说明。" + "Q" * 40)[-800:]
        # 当轮 usage 尚未到达（done 前掐断）→ None（T1 允许；保留原键，
        # 端点真值若可得仍写）
        assert rec["usage"] is None
        # P10 终审 Fix-1：usage 不可得时以本地估算凭证核账——
        # est_completion_tokens 对崩坏 accumulated 现算（>0），
        # est_prompt_tokens 为轮启动处记录的请求侧估算（键存在且 >0）。
        assert "est_completion_tokens" in rec
        assert "est_prompt_tokens" in rec
        assert rec["est_completion_tokens"] > 0
        assert rec["est_prompt_tokens"] is not None
        assert rec["est_prompt_tokens"] > 0
        # 返回值已清空（固化视角无崩坏文本）
        assert out == ""
        # 文件权限 0600
        assert (sample.stat().st_mode & 0o777) == 0o600

    @pytest.mark.asyncio
    async def test_append_does_not_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        agent = _make_agent()
        agent.llm_service.chat_completion_stream = _charwall_stream

        await _run_stream(agent)
        await _run_stream(agent)

        sample = tmp_path / TASK_DIR_NAME / SAMPLES_FILE
        records = _read_records(sample)
        assert len(records) == 2, "追加模式：两次掐断两条记录，不覆盖"
        assert records[0]["ts"] <= records[1]["ts"]

    @pytest.mark.asyncio
    async def test_tail_captured_before_accumulated_cleared(
        self, tmp_path, monkeypatch
    ):
        """置空前唯一窗口留存：返回值已空，但样本 tail 非空。"""
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        agent = _make_agent()
        agent.llm_service.chat_completion_stream = _charwall_stream

        out, _ = await _run_stream(agent)

        records = _read_records(tmp_path / TASK_DIR_NAME / SAMPLES_FILE)
        assert out == "", "置空后返回值为空"
        assert records[0]["tail"], "tail 必须在 accumulated 置空前抓取（非空）"

    @pytest.mark.asyncio
    async def test_usage_snapshot_recorded(self, tmp_path, monkeypatch):
        """当轮 usage 快照随样本留存（崩坏轮浪费核账原始凭证）。"""
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        agent = _make_agent()
        agent._last_round_usage = {
            "prompt_tokens": 123,
            "completion_tokens": 45,
        }
        await agent._persist_degenerate_sample("F2", "X" * 100)

        records = _read_records(tmp_path / TASK_DIR_NAME / SAMPLES_FILE)
        assert records[0]["degeneracy"] == "F2"
        assert records[0]["usage"] == {
            "prompt_tokens": 123,
            "completion_tokens": 45,
        }
        assert records[0]["len"] == 100
        # Fix-1：即使端点 usage 真值可得，估算凭证仍随样本写入
        assert records[0]["est_completion_tokens"] > 0
        # 直接调用未经轮启动挂点 → None（键必须存在，语义不伪造）
        assert "est_prompt_tokens" in records[0]
        assert records[0]["est_prompt_tokens"] is None


class TestTaskDirPermissions:
    """P10 终审 Fix-4：样本任务目录权限 0700（收紧既有目录同样生效）。"""

    @pytest.mark.asyncio
    async def test_task_dir_chmod_0700(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        # 预置 0755 的任务目录：证明 chmod 在 mkdir(exist_ok) 之后实际收紧
        task_dir = tmp_path / TASK_DIR_NAME
        task_dir.mkdir(mode=0o755)
        os.chmod(task_dir, 0o755)

        agent = _make_agent()
        await agent._persist_degenerate_sample("F1", "X" * 100)

        assert (task_dir.stat().st_mode & 0o777) == 0o700

    @pytest.mark.asyncio
    async def test_task_dir_created_as_0700(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        agent = _make_agent()
        await agent._persist_degenerate_sample("F2", "Y" * 80)

        task_dir = tmp_path / TASK_DIR_NAME
        assert task_dir.is_dir()
        assert (task_dir.stat().st_mode & 0o777) == 0o700


class TestNoLeakToSSE:
    @pytest.mark.asyncio
    async def test_persisted_events_contain_no_degenerate_fragments(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        agent = _make_agent()
        agent.llm_service.chat_completion_stream = _charwall_stream

        await _run_stream(agent)

        events = agent.capture.events
        # content_end 是正文固化的最终兜底：accumulated 必须为空
        content_ends = [e for e in events if e.event_type == "content_end"]
        assert len(content_ends) == 1
        assert content_ends[0].metadata["accumulated"] == ""
        # thinking_end（stream_has_kind）携带仅思考累计，本轮为 ""
        thinking_ends = [e for e in events if e.event_type == "thinking_end"]
        assert thinking_ends[0].metadata["accumulated"] == ""

        # 掐断 warning：固定文案，不含样本、不含形态以外信息
        warnings = [e for e in events if e.event_type == "warning"]
        assert len(warnings) == 1
        assert "崩坏" in warnings[0].message
        assert set(warnings[0].metadata.keys()) == {"agent_name"}

        # 固化事件（会落库的事件；对照 event_manager
        # skip_db_events={"thinking_token","content_token"}）的
        # message 与 metadata 全文不得含崩坏片段——崩坏文本不固化展示。
        fragments = ["Q" * 31, "正常审计开头说明"]
        transient = {"thinking_token", "content_token"}
        for event in events:
            if event.event_type in transient:
                # 实时 token 通道是瞬时流（不落库，前端随后清空），
                # 不属固化展示面；其存在仅证明流式时序本身。
                continue
            blob = event.message + json.dumps(
                event.metadata, ensure_ascii=False
            )
            for frag in fragments:
                assert frag not in blob, (
                    f"固化事件 {event.event_type} 泄漏崩坏片段"
                )

        # 留存通道独立：事件类型集合中不得出现样本通道事件
        assert not any(
            "sample" in e.event_type or "degenerate_" in e.event_type
            for e in events
        )


class TestWriteFailureNonBlocking:
    @pytest.mark.asyncio
    async def test_write_failure_does_not_block_break(
        self, tmp_path, monkeypatch
    ):
        # task 目录位置预置普通文件 → mkdir 必失败（对 root 同样有效）
        base = tmp_path / "traces"
        base.mkdir()
        (base / TASK_DIR_NAME).write_text("blocker", encoding="utf-8")
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(base)
        )
        agent = _make_agent()
        agent.llm_service.chat_completion_stream = _charwall_stream

        out, _ = await _run_stream(agent)

        # 主流程不受影响：照常掐断、空串返回、形态置位
        assert out == ""
        assert agent._last_empty_kind == "degenerate"

    @pytest.mark.asyncio
    async def test_missing_task_id_skips_retention(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.core.config.settings.AUDIT_TRACE_DIR", str(tmp_path)
        )
        agent = _make_agent()
        agent.task_id = None
        agent.llm_service.chat_completion_stream = _charwall_stream

        out, _ = await _run_stream(agent)
        assert out == ""
        assert not list(tmp_path.rglob(SAMPLES_FILE))
