"""P9 绑定组二（T4）根治测试（2026-10-08）：

1. P9-5 finding_id 三级优先（跨轮稳定，id 非字符串 str 化）
2. D1 sandbox_exec 去重键纳入 finding_id
3. P9-7 局部闸门清理 + 严匹配拼接去重（归一化剥 FINDING_ID/poc_N/tmp）
4. P9-4 报告空身份条目顺序对齐 + confirmed→static_confirmed 降级 + note
5. D12 aligned 条目分桶，不参与门禁真验证口径
6. P9-9 fingerprint 接线（同 fp 标注 duplicate_of 不静默删，空身份跳过）
7. 终局防御：持久归档 FIFO 2000 + orchestrator _final_evidence_sweep
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock


def _bare_verification_agent():
    """构造裸 VerificationAgent（不触发布料/LLM 装配）。"""
    from app.services.agent.agents.verification import VerificationAgent

    return VerificationAgent.__new__(VerificationAgent)


def _bare_orchestrator():
    from app.services.agent.agents.orchestrator import OrchestratorAgent

    return OrchestratorAgent.__new__(OrchestratorAgent)


# ---------------------------------------------------------------------------
# P9-5：finding_id 三级优先
# ---------------------------------------------------------------------------
class TestFindingIdStabilityP95:
    def test_existing_sandbox_id_highest_priority(self):
        agent = _bare_verification_agent()
        f = {
            "id": "orig-id-1",
            "_sandbox_finding_id": "stable-fid-1",
            "file_path": "app/a.py",
            "line_start": 10,
            "vulnerability_type": "sql_injection",
            "title": "SQLi",
        }
        agent._build_sandbox_commands([f])
        # 已分配的 _sandbox_finding_id 必须最高优先复用，不被 id 覆盖
        assert f["_sandbox_finding_id"] == "stable-fid-1"

    def test_fallback_to_id_field(self):
        agent = _bare_verification_agent()
        f = {
            "id": "field-id-22",
            "file_path": "app/b.py",
            "line_start": 5,
            "vulnerability_type": "xss",
            "title": "XSS 漏洞",
        }
        agent._build_sandbox_commands([f])
        assert f["_sandbox_finding_id"] == "field-id-22"

    def test_non_string_id_strified(self):
        agent = _bare_verification_agent()
        f = {
            "id": 9988,
            "file_path": "app/c.py",
            "line_start": 7,
            "vulnerability_type": "ssrf",
            "title": "SSRF 漏洞",
        }
        agent._build_sandbox_commands([f])
        fid = f["_sandbox_finding_id"]
        assert isinstance(fid, str)
        assert fid == "9988"

    def test_id_stable_across_rounds_and_new_finding_fresh(self):
        agent = _bare_verification_agent()
        old = {
            "file_path": "app/old.py",
            "line_start": 3,
            "vulnerability_type": "ssrf",
            "title": "旧漏洞",
        }
        # 第一轮：old 无 id → 生成短 ID
        agent._build_sandbox_commands([old])
        id_round1 = old["_sandbox_finding_id"]
        assert len(id_round1) == 8

        # 第二轮：old（已带 _sandbox_finding_id）+ 新 finding
        new = {
            "file_path": "app/new.py",
            "line_start": 9,
            "vulnerability_type": "xss",
            "title": "新漏洞",
        }
        agent._build_sandbox_commands([old, new])
        assert old["_sandbox_finding_id"] == id_round1  # 跨轮稳定
        new_fid = new["_sandbox_finding_id"]
        assert new_fid != id_round1
        assert len(new_fid) == 8


# ---------------------------------------------------------------------------
# D1：sandbox_exec 去重键纳入 finding_id
# ---------------------------------------------------------------------------
class TestSandboxExecKeyD1:
    def test_same_cmd_different_fid_different_key(self):
        from app.services.agent.agents.verification import _normalize_tool_key

        k1 = _normalize_tool_key(
            "sandbox_exec", {"command": "python3 x.py", "finding_id": "f1"}
        )
        k2 = _normalize_tool_key(
            "sandbox_exec", {"command": "python3 x.py", "finding_id": "f2"}
        )
        assert k1 != k2
        assert ":f1:" in k1
        assert ":f2:" in k2

    def test_same_fid_command_micro_variation_same_key(self):
        """同 finding 同命令微调（空白变化）仍归一键——微调检测不被削弱。"""
        from app.services.agent.agents.verification import _normalize_tool_key

        k1 = _normalize_tool_key(
            "sandbox_exec", {"command": "python3 a.py", "finding_id": "f1"}
        )
        k2 = _normalize_tool_key(
            "sandbox_exec", {"command": "python3  a.py ", "finding_id": "f1"}
        )
        assert k1 == k2

    def test_missing_fid_normalized_as_empty(self):
        from app.services.agent.agents.verification import _normalize_tool_key

        k1 = _normalize_tool_key("sandbox_exec", {"command": "python3 a.py"})
        k2 = _normalize_tool_key(
            "sandbox_exec", {"command": "python3 a.py", "finding_id": ""}
        )
        assert k1 == k2
        assert k1.startswith("sandbox_exec::")

    def test_should_block_repeat_call_regression(self):
        from app.services.agent.agents.verification import _should_block_repeat_call

        # 错误 1-3 连：放行（给修正机会）；错误 ≥4：拦截
        assert _should_block_repeat_call(1, 1) is False
        assert _should_block_repeat_call(3, 3) is False
        assert _should_block_repeat_call(4, 4) is True
        # 成功结果同参数 >3 次：拦截（真死循环）
        assert _should_block_repeat_call(4, 0) is True
        assert _should_block_repeat_call(3, 0) is False


# ---------------------------------------------------------------------------
# P9-4：vuln_type 相容规则
# ---------------------------------------------------------------------------
class TestVulnTypeCompat:
    def test_exact_after_normalize(self):
        from app.services.agent.agents.verification import _vuln_types_compatible

        assert _vuln_types_compatible("SSRF", "ssrf")
        assert _vuln_types_compatible("sql injection", "sql_injection")

    def test_one_side_other(self):
        from app.services.agent.agents.verification import _vuln_types_compatible

        assert _vuln_types_compatible("other", "xss")
        assert _vuln_types_compatible("ssrf", "other")

    def test_containment_with_ratio(self):
        from app.services.agent.agents.verification import _vuln_types_compatible

        # 包含且长度比 ≥0.5
        assert _vuln_types_compatible(
            "hardcoded_secret", "hardcoded_secret_key"
        )
        # 包含但长度比 <0.5 → 不相容
        assert not _vuln_types_compatible(
            "xss", "xss_reflected_stored_variant_type"
        )

    def test_disjoint_and_empty(self):
        from app.services.agent.agents.verification import _vuln_types_compatible

        assert not _vuln_types_compatible("xss", "ssrf")
        assert not _vuln_types_compatible("", "ssrf")
        assert not _vuln_types_compatible(None, None)


# ---------------------------------------------------------------------------
# P9-4：顺序对齐
# ---------------------------------------------------------------------------
class TestOrderAlignmentP94:
    def _agent(self):
        agent = _bare_verification_agent()
        agent._claimed_input_indices = set()
        return agent

    def test_align_when_equal_count(self):
        agent = self._agent()
        entries = [{"title": "无身份条目", "vulnerability_type": "ssrf"}]
        inputs = [
            {
                "title": "输入A",
                "vulnerability_type": "ssrf",
                "file_path": "app/s.py",
                "line_start": 22,
                "_sandbox_finding_id": "fid-a",
            }
        ]
        n = agent._align_unidentified_report_entries(entries, inputs)
        assert n == 1
        e = entries[0]
        assert e["_sandbox_finding_id"] == "fid-a"
        assert e["file_path"] == "app/s.py"
        assert e["line_start"] == 22
        assert e["_aligned_by_order"] is True
        assert 0 in agent._claimed_input_indices

    def test_no_align_when_unequal_count(self):
        agent = self._agent()
        entries = [{"title": "无身份条目", "vulnerability_type": "ssrf"}]
        inputs = [
            {
                "title": "输入A",
                "vulnerability_type": "ssrf",
                "file_path": "app/s.py",
                "line_start": 1,
                "_sandbox_finding_id": "fid-a",
            },
            {
                "title": "输入B",
                "vulnerability_type": "xss",
                "file_path": "app/x.py",
                "line_start": 2,
                "_sandbox_finding_id": "fid-b",
            },
        ]
        n = agent._align_unidentified_report_entries(entries, inputs)
        assert n == 0
        e = entries[0]
        assert "_sandbox_finding_id" not in e
        assert agent._claimed_input_indices == set()

    def test_no_align_when_inputs_all_claimed(self):
        """有身份条目已把输入全部认领 → 未认领数 0 ≠ 无身份条目数，不对齐。"""
        agent = self._agent()
        entries = [
            {
                "title": "有身份",
                "vulnerability_type": "ssrf",
                "file_path": "app/s.py",
            },
            {"title": "无身份", "vulnerability_type": "ssrf"},
        ]
        inputs = [
            {
                "title": "输入A",
                "vulnerability_type": "ssrf",
                "file_path": "app/s.py",
                "line_start": 22,
                "_sandbox_finding_id": "fid-a",
            }
        ]
        n = agent._align_unidentified_report_entries(entries, inputs)
        assert n == 0
        assert "_sandbox_finding_id" not in entries[1]

    def test_incompatible_vuln_type_aborts(self):
        agent = self._agent()
        entries = [{"title": "无身份", "vulnerability_type": "xss"}]
        inputs = [
            {
                "title": "输入A",
                "vulnerability_type": "ssrf",
                "file_path": "app/s.py",
                "line_start": 22,
                "_sandbox_finding_id": "fid-a",
            }
        ]
        n = agent._align_unidentified_report_entries(entries, inputs)
        assert n == 0
        assert "_sandbox_finding_id" not in entries[0]
        assert agent._claimed_input_indices == set()

    def test_identified_claim_first_then_align_unclaimed(self):
        agent = self._agent()
        entries = [
            {
                "title": "有身份",
                "vulnerability_type": "ssrf",
                "file_path": "app/s.py",
            },
            {"title": "无身份", "vulnerability_type": "xss"},
        ]
        inputs = [
            {
                "title": "输入A",
                "vulnerability_type": "ssrf",
                "file_path": "app/s.py",
                "line_start": 22,
                "_sandbox_finding_id": "fid-a",
            },
            {
                "title": "输入B",
                "vulnerability_type": "xss",
                "file_path": "app/x.py",
                "line_start": 8,
                "_sandbox_finding_id": "fid-b",
            },
        ]
        n = agent._align_unidentified_report_entries(entries, inputs)
        assert n == 1
        assert entries[1]["_sandbox_finding_id"] == "fid-b"
        assert entries[1]["file_path"] == "app/x.py"
        assert entries[0].get("_aligned_by_order") is not True


# ---------------------------------------------------------------------------
# P9-4 + D2：aligned confirmed 降级；非 aligned 不被波及
# ---------------------------------------------------------------------------
class TestAlignedDowngrade:
    def _confirmed_attempt(self):
        return {
            "success": True,
            "exit_code": 0,
            "evidence_summary": (
                "PoC stdout lines ... VULNERABILITY_CONFIRMED target reachable "
                "metadata endpoint responded " + "x" * 40
            ),
            "command": "python3 /tmp/poc_0.py",
            "target_ref": "app/s.py:22",
        }

    def test_aligned_confirmed_downgraded_to_static(self):
        agent = _bare_verification_agent()
        finding = {
            "file_path": "app/s.py",
            "line_start": 22,
            "vulnerability_type": "ssrf",
            "title": "SSRF 漏洞",
            "verdict": "confirmed",
            "_aligned_by_order": True,
            "sandbox_attempts": [self._confirmed_attempt()],
        }
        out = agent._normalize_verification_outcome(finding)
        from app.models.agent_task import VerificationStatus

        assert out["verification_status"] == VerificationStatus.STATIC_CONFIRMED
        assert out["verdict"] == VerificationStatus.STATIC_CONFIRMED
        assert out["is_verified"] is True
        assert "顺序归并证据，请人工复核" in out["verification_note"]
        # 对齐标记不得在归一化中丢失（供下游分桶）
        assert out.get("_aligned_by_order") is True

    def test_non_aligned_confirmed_stays_confirmed(self):
        agent = _bare_verification_agent()
        finding = {
            "file_path": "app/s.py",
            "line_start": 22,
            "vulnerability_type": "ssrf",
            "title": "SSRF 漏洞",
            "verdict": "confirmed",
            "sandbox_attempts": [self._confirmed_attempt()],
        }
        out = agent._normalize_verification_outcome(finding)
        from app.models.agent_task import VerificationStatus

        assert out["verification_status"] == VerificationStatus.CONFIRMED


# ---------------------------------------------------------------------------
# D12：aligned 分桶——门禁统计排除
# ---------------------------------------------------------------------------
class TestD12GateBuckets:
    def test_gate_evidence_findings_excludes_aligned_and_recon(self):
        o = _bare_orchestrator()
        o._all_findings = [
            {
                "file_path": "app/n.py",
                "line_start": 1,
                "vulnerability_type": "ssrf",
                "title": "正常",
            },
            {
                "file_path": "app/a.py",
                "line_start": 2,
                "vulnerability_type": "ssrf",
                "title": "对齐",
                "_aligned_by_order": True,
            },
            {
                "file_path": "app/r.py",
                "line_start": 3,
                "vulnerability_type": "ssrf",
                "title": "侦察",
                "source": "recon",
            },
        ]
        gate = o._gate_evidence_findings()
        assert len(gate) == 1
        assert gate[0]["title"] == "正常"

    def test_has_valid_sandbox_evidence_ignores_aligned(self):
        o = _bare_orchestrator()
        o._all_findings = [
            {
                "file_path": "app/a.py",
                "line_start": 2,
                "vulnerability_type": "ssrf",
                "title": "对齐",
                "_aligned_by_order": True,
                "verification_status": "static_confirmed",
                "sandbox_attempts": [{"command": "python3 p.py"}],
            },
            {
                "file_path": "app/n.py",
                "line_start": 1,
                "vulnerability_type": "xss",
                "title": "未验证",
                "verification_status": "needs_context",
            },
        ]
        # aligned 单独存在的"证据"不得满足真实验证门禁
        assert o._has_valid_sandbox_evidence() is False

        # 对照：追加真实 confirmed finding → True
        o._all_findings.append(
            {
                "file_path": "app/c.py",
                "line_start": 5,
                "vulnerability_type": "command_injection",
                "title": "真确认",
                "verification_status": "confirmed",
                "sandbox_attempts": [
                    {
                        "success": True,
                        "exit_code": 0,
                        "fabricated": False,
                        "command": "python3 c.py",
                    }
                ],
            }
        )
        assert o._has_valid_sandbox_evidence() is True

    def test_coverage_excludes_aligned_dimension(self):
        o = _bare_orchestrator()
        o._steps = []
        o._agent_results = {}
        o._all_findings = [
            {
                "file_path": "app/a.py",
                "line_start": 2,
                "vulnerability_type": "ssrf",
                "title": "对齐",
                "_aligned_by_order": True,
            }
        ]
        report = o._evaluate_current_coverage()
        # ssrf → D6；仅 aligned 存在时 D6 不得计为 COVERED
        from app.services.agent.coverage import CoverageStatus

        assert report.statuses["D6"] == CoverageStatus.UNCOVERED


# ---------------------------------------------------------------------------
# P9-9：fingerprint
# ---------------------------------------------------------------------------
class TestFingerprintP99:
    def test_compute_deterministic_and_normalized(self):
        from app.services.agent.utils.finding_fingerprint import (
            compute_finding_fingerprint,
        )

        fp1 = compute_finding_fingerprint("app/a.py", "ssrf", "Some Title")
        fp2 = compute_finding_fingerprint("app/a.py", "ssrf", "Some Title")
        assert fp1 is not None and fp1 == fp2
        assert len(fp1) == 16
        # 路径大小写/外围空白归一
        fp3 = compute_finding_fingerprint(" app/A.py ", "SSRF", "Some Title")
        assert fp3 == fp1
        # 类型不同 → fp 不同
        fp4 = compute_finding_fingerprint("app/a.py", "xss", "Some Title")
        assert fp4 != fp1

    def test_title_truncation_60_chars(self):
        from app.services.agent.utils.finding_fingerprint import (
            compute_finding_fingerprint,
        )

        prefix = "T" * 60
        fp_a = compute_finding_fingerprint("app/a.py", "ssrf", prefix + "DIFF")
        fp_b = compute_finding_fingerprint("app/a.py", "ssrf", prefix)
        assert fp_a == fp_b
        fp_c = compute_finding_fingerprint(
            "app/a.py", "ssrf", "X" + prefix[1:]
        )
        assert fp_c != fp_b

    def test_empty_identity_no_fingerprint(self):
        from app.services.agent.utils.finding_fingerprint import (
            compute_finding_fingerprint,
        )

        assert compute_finding_fingerprint("", "ssrf", "t") is None
        assert compute_finding_fingerprint("unknown", "ssrf", "t") is None
        assert compute_finding_fingerprint("app/a.py", "ssrf", "") is not None

    def test_assign_same_fp_annotates_duplicate_without_delete(self):
        from app.services.agent.utils.finding_fingerprint import (
            assign_fingerprints,
        )

        findings = [
            {"file_path": "app/a.py", "vulnerability_type": "ssrf", "title": "Same"},
            {"file_path": "app/a.py", "vulnerability_type": "ssrf", "title": "Same"},
        ]
        stats = assign_fingerprints(findings)
        assert stats["duplicates"] == 1
        assert findings[0].get("duplicate_of") is None
        fp0 = findings[0]["fingerprint"]
        assert findings[1]["fingerprint"] == fp0
        assert findings[1]["duplicate_of"] == fp0
        assert findings[1]["finding_metadata"]["duplicate_of"] == fp0
        # 不静默删除
        assert len(findings) == 2

    def test_save_findings_persists_fingerprint(self):
        from app.api.v1.endpoints.agent_tasks import _save_findings

        finding = {
            "title": "SQL注入",
            "vulnerability_type": "sql_injection",
            "file_path": "app/db.py",
            "line_start": 30,
            "confidence": 0.9,
            "severity": "high",
        }
        db = MagicMock()
        db.commit = AsyncMock()
        saved = asyncio.run(
            _save_findings(db, "t-fp", [finding], project_root=None)
        )
        assert saved == 1
        added = db.add.call_args[0][0]
        assert added.fingerprint == finding["fingerprint"]
        assert isinstance(added.fingerprint, str) and len(added.fingerprint) == 16


# ---------------------------------------------------------------------------
# 终局防御：持久归档 FIFO
# ---------------------------------------------------------------------------
class TestPersistentArchiveFIFO:
    def test_fifo_drops_oldest(self):
        agent = _bare_verification_agent()
        for i in range(2001):
            agent._archive_attempt(
                {"command": f"cmd-{i}", "exit_code": 0, "evidence_summary": f"ev-{i}"}
            )
        archive = agent._persistent_attempt_archive
        assert len(archive) == 2000
        assert archive[0]["command"] == "cmd-1"
        assert archive[-1]["command"] == "cmd-2000"

    def test_archive_dedupes_same_semantic_key(self):
        agent = _bare_verification_agent()
        agent._archive_attempt(
            {"command": "python3 x.py", "exit_code": 0, "evidence_summary": "ev"}
        )
        # 仅首行 FINDING_ID 注释不同——归一化后同键
        agent._archive_attempt(
            {
                "command": "# FINDING_ID:abc999\npython3 x.py",
                "exit_code": 0,
                "evidence_summary": "ev",
            }
        )
        assert len(agent._persistent_attempt_archive) == 1

    def test_record_sandbox_attempt_dual_writes_archive(self):
        agent = _bare_verification_agent()
        agent._sandbox_attempts = []
        agent._runtime_attempts_by_finding_id = {}
        agent._all_findings = [
            {
                "file_path": "app/a.py",
                "line_start": 10,
                "vulnerability_type": "ssrf",
                "_sandbox_finding_id": "fid-a",
            }
        ]
        agent._record_sandbox_attempt(
            {
                "command": "# FINDING_ID:fid-a\npython3 /tmp/poc_0.py",
                "finding_id": "fid-a",
            },
            "退出码: 0\n" + "x" * 60,
        )
        assert len(agent._persistent_attempt_archive) == 1

    def test_record_language_attempt_dual_writes_archive(self):
        agent = _bare_verification_agent()
        agent._sandbox_attempts = []
        agent._all_findings = [
            {
                "file_path": "app/a.py",
                "line_start": 10,
                "vulnerability_type": "ssrf",
                "_sandbox_finding_id": "fid-a",
            }
        ]
        agent._record_language_test_attempt(
            "python_test",
            {
                "code": "# FINDING_ID:fid-a\nprint('Target: app/a.py')",
                "file_path": "app/a.py",
            },
            "退出码: 0\n" + "x" * 30,
        )
        assert len(agent._persistent_attempt_archive) == 1


# ---------------------------------------------------------------------------
# 终局防御：orchestrator _final_evidence_sweep
# ---------------------------------------------------------------------------
class TestFinalEvidenceSweep:
    def _setup(self, archive_attempts):
        o = _bare_orchestrator()
        v = _bare_verification_agent()
        v._persistent_attempt_archive = archive_attempts
        o.sub_agents = {"verification": v}
        o._gate_observations = []
        o._sweep_bound_attempt_ids = set()
        return o, v

    def _confirmed_attempt(self, command, target_ref):
        return {
            "success": True,
            "exit_code": 0,
            "evidence_summary": (
                "PoC stdout lines ... VULNERABILITY_CONFIRMED target verified "
                "endpoint responded " + "x" * 40
            ),
            "command": command,
            "target_ref": target_ref,
        }

    def test_sweep_binds_recalculates_and_records_observation(self):
        attempt = self._confirmed_attempt(
            "python3 /tmp/poc.py", "app/vuln.py:15"
        )
        o, v = self._setup([attempt])
        finding = {
            "file_path": "app/vuln.py",
            "line_start": 15,
            "vulnerability_type": "ssrf",
            "title": "SSRF 漏洞",
            "verification_status": "needs_context",
            "is_verified": False,
        }
        o._all_findings = [finding]
        n = o._final_evidence_sweep()
        assert n == 1
        assert attempt in finding["sandbox_attempts"]
        assert finding["verification_status"] == "confirmed"
        assert finding["verdict"] == "confirmed"
        assert finding["is_verified"] is True
        sweep_obs = [
            x for x in o._gate_observations
            if x.get("gate") == "post_gate_evidence_sweep"
        ]
        assert len(sweep_obs) == 1
        assert "needs_context → confirmed" in sweep_obs[0]["reason"]
        assert id(attempt) in o._sweep_bound_attempt_ids

    def test_sweep_idempotent(self):
        attempt = self._confirmed_attempt(
            "python3 /tmp/poc.py", "app/vuln.py:15"
        )
        o, _ = self._setup([attempt])
        finding = {
            "file_path": "app/vuln.py",
            "line_start": 15,
            "vulnerability_type": "ssrf",
            "title": "SSRF 漏洞",
            "verification_status": "needs_context",
            "is_verified": False,
        }
        o._all_findings = [finding]
        assert o._final_evidence_sweep() == 1
        assert o._final_evidence_sweep() == 0
        sweep_obs = [
            x for x in o._gate_observations
            if x.get("gate") == "post_gate_evidence_sweep"
        ]
        assert len(sweep_obs) == 1
        assert len(finding["sandbox_attempts"]) == 1

    def test_sweep_then_gate_marking_does_not_exempt_bound(self):
        """sweep 在 gate release marking 之前：已绑定证据的 finding 不得再被豁免。"""
        attempt = self._confirmed_attempt(
            "python3 /tmp/poc.py", "app/vuln.py:15"
        )
        o, _ = self._setup([attempt])
        finding = {
            "file_path": "app/vuln.py",
            "line_start": 15,
            "vulnerability_type": "ssrf",
            "title": "SSRF 漏洞",
            "verification_status": "needs_context",
            "is_verified": False,
        }
        o._all_findings = [finding]
        o._final_evidence_sweep()
        marked = o._mark_released_unverified_findings(
            "orchestrator_max_iterations_exhausted"
        )
        assert marked == 0
        assert finding.get("sandbox_skip_reason") is None

        # 对照：不跑 sweep 时未验证 finding 会被标记
        o2, _ = self._setup([])
        finding2 = {
            "file_path": "app/other.py",
            "line_start": 15,
            "vulnerability_type": "ssrf",
            "title": "另一 SSRF 漏洞",
            "verification_status": "needs_context",
            "is_verified": False,
            "sandbox_attempts": None,
        }
        o2._all_findings = [finding2]
        assert o2._mark_released_unverified_findings("r") == 1
        assert finding2["sandbox_skip_reason"] == "r"

    def test_sweep_skips_aligned_empty_identity_and_explicit_fp(self):
        attempt = self._confirmed_attempt("python3 /tmp/p.py", "app/x.py:1")
        o, _ = self._setup([attempt])
        aligned = {
            "file_path": "app/x.py",
            "line_start": 1,
            "vulnerability_type": "ssrf",
            "title": "对齐",
            "_aligned_by_order": True,
        }
        no_identity = {
            "file_path": "",
            "line_start": 0,
            "vulnerability_type": "ssrf",
            "title": "空身份",
        }
        explicit_fp = {
            "file_path": "app/y.py",
            "line_start": 2,
            "vulnerability_type": "ssrf",
            "title": "显式误报",
            "verification_status": "false_positive",
            "verdict": "false_positive",
        }
        o._all_findings = [aligned, no_identity, explicit_fp]
        assert o._final_evidence_sweep() == 0
        for f in o._all_findings:
            assert not f.get("sandbox_attempts")
        assert o._gate_observations == []

    def test_sweep_suffix_fallback_lookup(self):
        # 归档文本只含末 2 段后缀，不含完整 file_path
        attempt = self._confirmed_attempt(
            "python3 app/v.py", "app/v.py:12"
        )
        o, _ = self._setup([attempt])
        finding = {
            "file_path": "service/deep/app/v.py",
            "line_start": 12,
            "vulnerability_type": "ssrf",
            "title": "SSRF",
            "verification_status": "needs_context",
            "is_verified": False,
        }
        o._all_findings = [finding]
        assert o._final_evidence_sweep() == 1
        assert attempt in finding["sandbox_attempts"]
