"""P10 A' 步骤 2（T3）：检测器缺陷修复 + 1.15 过期清理。

权威方案：/root/coder/docs/p10-root-cause-plan-2026-10-09.html 第三章步骤 2。
HEAD=939f3b7 检测器两处窗口缺陷（撰写人真实运行复核）：

a 跨轮失明：``_deg_last_loop_check`` 不在轮首重置块（base.py:1282-1309）
  中清零。同一 198 字符循环文本 fresh 状态检测为 F2、上轮残留 stale=1500
  时为 None——新轮短于上轮长度时 F2 节流条件（len-stale>=间隔）恒假，
  F2 整段不跑。修复：轮首重置块补 ``self._deg_last_loop_check = 0``。

b content 混合窗口盲段：F2 仅扫 tail500，且片段起点上限硬编码 220。
  长干净基底（长思考/前缀）后正文循环，首命中实测迟至正文 330 字符
  （本文件实证）。修复：放开扫描上限至整个 tail500（延迟非漏检）。

c 死常量：DEGENERACY_LOOP_CHECK_INTERVAL=1024 全仓仅定义行/docstring
  引用——删除；节流硬编码 64 改用具名常量（常量复用其名，值改 24）。

d 1.15 过期清理：生产真基线 settings.LLM_REPETITION_PENALTY=1.2；
  types.py/service.py/litellm_adapter.py/endpoints/config.py 的过期注释
  与 getattr 回退、前端 SystemConfig.tsx 兜底值统一对齐 1.2。
"""

import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agents import base as base_module
from app.services.agent.agents.base import BaseAgent
from app.services.agent.agents.orchestrator import OrchestratorAgent

# 33 字符循环单元 × 6 = 198 字符（方案实证口径）
UNIT33 = "abcdefghijklmnopqrstuvwxyz0123456"
LOOP198 = UNIT33 * 6
assert len(UNIT33) == 33 and len(LOOP198) == 198


def _make_agent() -> OrchestratorAgent:
    # BaseAgent 为抽象类（run 未实现），用具体子类 __new__ 绕过构造
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.config = MagicMock()
    agent.config.name = "Test"
    agent.config.agent_type = MagicMock(value="orchestrator")
    agent.event_emitter = MagicMock()
    agent.event_emitter.emit = AsyncMock()
    agent._timeout_config = MagicMock()
    agent.llm_service = MagicMock()
    agent._last_empty_kind = None
    return agent


def _clean_prose(length: int) -> str:
    """逐行变化的干净审计散文（句读充足；公共后缀 23 字符 < F2 的 25）。"""
    out = ""
    i = 0
    while len(out) < length:
        out += f"第{i:04d}项检查：配置与输入校验正常完成，未发现异常。\n"
        i += 1
    return out[:length]


def _chunked_stream_factory(
    round_texts: List[str], sizes: Optional[List[int]] = None
):
    """多轮流工厂：每轮文本按对应 size 切片成 content token chunks + done。"""
    state = {"round": 0}

    def factory(**kwargs):
        idx = state["round"]
        state["round"] += 1
        text = round_texts[min(idx, len(round_texts) - 1)]
        size = (
            sizes[min(idx, len(sizes) - 1)]
            if sizes
            else 3
        )

        async def gen():
            acc = ""
            for i in range(0, len(text), size):
                piece = text[i : i + size]
                acc += piece
                yield {
                    "type": "token",
                    "kind": "content",
                    "content": piece,
                    "accumulated": acc,
                    "accumulated_content": acc,
                    "accumulated_reasoning": "",
                }
            yield {
                "type": "done",
                "content": text,
                "reasoning": "",
                "accumulated": text,
                "usage": {
                    "total_tokens": 10,
                    "prompt_tokens": 5,
                    "completion_tokens": 5,
                },
                "finish_reason": "stop",
            }

        return gen()

    return factory


async def _run(agent):
    with patch.object(
        agent, "_get_llm_rate_limiter",
        return_value=MagicMock(acquire=AsyncMock()),
    ), patch(
        "app.services.agent.agents.base.get_llm_circuit"
    ) as fake_cb:
        fake_cb.return_value.call = lambda fn: fn()
        return await agent.stream_llm_call(
            [{"role": "user", "content": "x"}]
        )


class TestF2CrossLoopBlindness:
    """a 跨轮失明：新轮短循环文本必须仍受 F2 保护（RED：stale 下不跑）。"""

    @pytest.mark.asyncio
    async def test_short_loop_in_new_round_caught_after_long_round(self):
        agent = _make_agent()
        long_text = _clean_prose(1680)
        agent.llm_service.chat_completion_stream = _chunked_stream_factory(
            [long_text, LOOP198], sizes=[28, 3]
        )

        out1, _ = await _run(agent)
        assert out1 == long_text, "首轮干净文本正常返回"
        # 实证 stale：上轮节流位置 > 新轮全文长度
        assert getattr(agent, "_deg_last_loop_check", 0) > len(LOOP198)

        out2, _ = await _run(agent)
        # RED（修复前）：F2 节流恒假 → 循环轮整体返回，断言失败
        assert out2 == "", "新轮循环崩坏必须掐断丢弃（跨轮不得失明）"
        assert agent._last_empty_kind == "degenerate"

    def test_198_loop_fresh_true_stale_false_premise(self):
        # 方案实证前提：同文本 fresh=F2、stale=1500 时 None
        fresh = _make_agent()
        assert fresh._detect_output_degeneracy(LOOP198) == "F2"
        stale = _make_agent()
        stale._deg_last_loop_check = 1500
        assert stale._detect_output_degeneracy(LOOP198) is None


class TestF2ContentWindow:
    """b content 混合窗口盲段：正文早期循环必须早命中（RED：迟至 330）。"""

    def test_content_loop_hit_early_after_4000_clean_base(self):
        agent = _make_agent()
        base = _clean_prose(4000)
        assert agent._detect_output_degeneracy(base) is None

        # 按真实 chunk 节奏（3 字符/chunk）增长正文，≤99 字符内必须命中。
        # 当前实现：片段起点上限 220、正文起点在 tail500 的 index 400 →
        # 首命中迟至正文 330 字符（本测试 RED）。
        hit_at = None
        for n in range(3, 100, 3):
            text = base + (UNIT33 * 5)[:n]
            if agent._detect_output_degeneracy(text) == "F2":
                hit_at = n
                break
        assert hit_at is not None, "正文循环在 99 字符内未命中（盲段）"
        assert hit_at <= 99

    def test_no_hardcoded_220_scan_cap(self):
        # 防回潮：检测器源码不再出现 220 硬编码扫描上限
        source = inspect.getsource(BaseAgent._detect_output_degeneracy)
        assert "220" not in source


class TestF2SameLineIncidentalJSON:
    """同线内 JSON 字段/路径引用偶合：合法的第 9 类形态（RED：被误杀）。

    生产 Final Answer 单行 JSON 中，同一文件路径被不同对象的相邻字段
    引用（entry_points 的 "file" 与 findings 的 "file_path" 后跟相同的
    "description" 键）会形成 25+ 字符窗口的偶合。它与真循环的区别：
    窗口之后真周期循环字符仍一致，偶合引用随即分歧（值不同）。
    """

    @staticmethod
    def _json_answer() -> str:
        import json as _json

        answer = {
            "entry_points": [
                {
                    "type": "entry",
                    "file": "src/sql_vuln.py",
                    "description": "main module",
                }
            ],
            "findings": [
                {
                    "title": "SQL Injection",
                    "file_path": "src/sql_vuln.py",
                    "description": "f-string SQL query",
                }
            ],
        }
        return "Final Answer: " + _json.dumps(answer, ensure_ascii=False)

    def test_incidental_same_line_repeat_not_flagged(self):
        agent = _make_agent()
        text = self._json_answer()
        assert len(text) >= 120
        # RED（无扩展后缀确认时）：同线配对直接判 F2
        assert agent._detect_output_degeneracy(text) is None

    def test_periodic_same_line_loop_still_flagged(self):
        # 对照：窗口之后字符仍一致的真周期循环必须命中
        agent = _make_agent()
        text = "前缀文本。" + ("sql_vuln_description_value" * 20) + "尾"
        assert agent._detect_output_degeneracy(text) == "F2"


class TestThrottleNamedConstant:
    """c 死常量清理 + 节流具名常量（24）。"""

    def test_constant_value_is_24(self):
        assert BaseAgent.DEGENERACY_LOOP_CHECK_INTERVAL == 24

    def test_no_dead_1024_constant(self):
        source = inspect.getsource(base_module)
        assert "DEGENERACY_LOOP_CHECK_INTERVAL = 1024" not in source

    def test_no_bare_64_throttle(self):
        # 节流间隔必须引用具名常量，不再裸写 64
        source = inspect.getsource(BaseAgent._detect_output_degeneracy)
        assert ">= 64" not in source
        assert "DEGENERACY_LOOP_CHECK_INTERVAL" in source

    def test_throttle_fires_at_24_interval(self):
        prefix = _clean_prose(100)
        text = prefix + UNIT33 * 4
        assert len(text) == 232
        # 距上次检查 23 < 24 → 不跑 F2
        blocked = _make_agent()
        blocked._deg_last_loop_check = len(text) - 23
        assert blocked._detect_output_degeneracy(text) is None
        # 距上次检查恰好 24 → 跑 F2 并命中
        ready = _make_agent()
        ready._deg_last_loop_check = len(text) - 24
        assert ready._detect_output_degeneracy(text) == "F2"


class TestNoStaleRp115Fallback:
    """d 1.15 过期清理防回潮：真基线 settings.LLM_REPETITION_PENALTY=1.2。"""

    def test_settings_default_is_1_2(self):
        from app.core.config import settings

        assert float(settings.LLM_REPETITION_PENALTY) == 1.2

    @pytest.mark.parametrize(
        "relpath",
        [
            "app/services/llm/types.py",
            "app/services/llm/service.py",
            "app/services/llm/adapters/litellm_adapter.py",
            "app/api/v1/endpoints/config.py",
        ],
    )
    def test_backend_files_have_no_1_15(self, relpath):
        # base.py: app/services/agent/agents/base.py → backend = parents[4]
        backend_root = Path(base_module.__file__).parents[4]
        assert backend_root.name == "backend"
        content = (backend_root / relpath).read_text(encoding="utf-8")
        assert "1.15" not in content, f"{relpath} 残留过期默认 1.15"

    def test_frontend_fallback_aligned_to_1_2(self):
        # base.py → backend=parents[4]、仓库根=parents[5]
        repo_root = Path(base_module.__file__).parents[5]
        target = (
            repo_root
            / "frontend/src/components/system/SystemConfig.tsx"
        )
        content = target.read_text(encoding="utf-8")
        assert "1.15" not in content, "前端兜底残留过期默认 1.15"
        assert "1.2" in content
