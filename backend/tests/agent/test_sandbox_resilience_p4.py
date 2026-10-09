"""P4 沙箱韧性三修复（2026-10-03，A 机任务 026ead34 实证）。

实证故障链：Flash-Next 发出空调用（java_test/read_file 参数:无）→ 工具
TypeError → 同参数重试被"超过3次"死循环防护误杀 → 沙箱验证断路 →
验证 Agent 被迫静态降级（skip_reason 如实记录）。

契约：
1. 漏参精准引导：工具错误含 missing argument 时，返回文本必须点名缺失
   参数并给出"参数必须为 JSON 对象"的精准修正指引；
2. 错误重试白名单：同参数"工具错误"重试 3 次内不被死循环防护拦截
   （真死循环=同参数同成功结果，语义保留）；
3. 沙箱断路兜底：LLM 沙箱类工具连续失败 ≥3 → 强制补跑确定性 PoC
   （幂等台账自动只跑未落账命令）。
"""
import pytest

from app.services.agent.agents.base import BaseAgent
from app.services.agent.agents.verification import (
    _should_block_repeat_call,
    _should_force_deterministic_rerun,
)


class TestMissingArgGuidanceP4:
    def test_missing_arg_error_gets_precise_guidance(self):
        raw = "FileReadTool._execute() missing 1 required positional argument: 'file_path'"
        out = BaseAgent._enhance_missing_arg_error(raw)
        assert "file_path" in out
        assert "JSON" in out
        assert "空调用" in out or "参数缺失" in out

    def test_unrelated_error_untouched(self):
        raw = "connection refused"
        assert BaseAgent._enhance_missing_arg_error(raw) == raw


class TestErrorRetryWhitelistP4:
    def test_error_retry_within_3_not_blocked(self):
        # 同参数第 4 次调用，但前 3 次都是工具错误 → 允许继续重试
        assert _should_block_repeat_call(count=4, error_streak=3) is False

    def test_error_retry_beyond_3_blocked(self):
        assert _should_block_repeat_call(count=7, error_streak=4) is True

    def test_successful_repeat_still_blocked_at_3(self):
        # 成功结果的同参数重复：死循环语义保留
        assert _should_block_repeat_call(count=4, error_streak=0) is True


class TestDeterministicRerunP4:
    def test_sandbox_fail_streak_triggers_rerun(self):
        assert _should_force_deterministic_rerun(sandbox_fail_streak=3, rerun_done=False) is True

    def test_below_threshold_no_rerun(self):
        assert _should_force_deterministic_rerun(sandbox_fail_streak=2, rerun_done=False) is False

    def test_rerun_only_once(self):
        assert _should_force_deterministic_rerun(sandbox_fail_streak=9, rerun_done=True) is False
