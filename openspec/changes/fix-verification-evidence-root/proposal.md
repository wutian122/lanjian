# Proposal: 验证引擎根治 - 确定性证据判定与门禁终止

## Background

生产任务 `3e62aadc`（Agent审计-nacos，2026-08-21）在既有 `fix-sandbox-evidence-and-recovery` 修复已生效的 v5.3.0 上仍以 `completed_with_gaps` 收场，暴露验证链路的**机制性缺陷**，非偶发故障：

1. **验证结论丢失**：Verification Agent 两次声称 "5/5 个发现已验证"，但落库全部 `verification_status=needs_context`、`is_verified=false`。`_normalize_verification_outcome` 只按 LLM 自述 verdict 降级（confirmed 无证据→not_reproducible），**从不因证据升级**（有 `VULNERABILITY_CONFIRMED` 铁证也不升 confirmed）。门禁 `_has_valid_sandbox_evidence` 因此永远 False。
2. **证据附加失败**：`_attach_runtime_sandbox_attempts` 只命中 1/5 发现（依赖 LLM Final Answer 里带 verification_method + 模糊路径匹配），4 个发现的 sandbox_attempts 为 null。
3. **伪造证据**：round 2 起沙箱输出 `Source file not found` + `Simulated trust-all context` + 硬编码假 JWT + `VULNERABILITY_CONFIRMED`，`_record_sandbox_attempt` 把它记为 success=True——伪造可被当有效证据。
4. **门禁失控循环**：orchestrator 对 finish 连续拒绝 8 次并强制重派 verification（02:52:35-02:55:35），无次数上限，烧 810 万 tokens 直到迭代耗尽兜底。
5. **Bug D 门禁被默认值击穿**：`unverified_findings` 判 `not verification_status`，但 analysis 默认写 `needs_context`（truthy）→ 门禁永不触发。
6. **observations 不落库**：模型字段存在但全代码库无写入点。

**核心根因**：验证结论由 LLM 自述（Final Answer verdict）决定，而非由运行时沙箱证据确定性推导。所有缺陷都源于此。

## Goal

把验证链路改为**确定性证据驱动**：
1. verification_status/is_verified 由 sandbox_attempts 证据代码化推导，不信任 LLM 自述。
2. 每个 finding 的运行时沙箱证据按 finding_id 强制绑定，不因 LLM 漏报丢失。
3. 伪造证据（Simulated/模拟输出）被识别并排除出判定与门禁。
4. 门禁连续拒绝达上限后确定性终止（completed_with_gaps + observations），杜绝 token 黑洞。
5. Bug D 全量验证门禁判定修复。
6. observations 持久化，缺口原因可追溯。

## Non-Goals

- 不重写 Verification Agent 的 ReAct 循环骨架（保留 LLM 参与的补充验证能力）。
- 不改变沙箱 Docker 镜像/预装包。
- 不修改 LLM 适配器/模型选择。
- 不改 D1-D10 覆盖率矩阵定义与阈值。
- 不改 SSE 协议与前端实时流（observations 前端展示不纳入本变更，仅保证数据落库）。

## Scope

### Files Modified (Backend)

| File | Changes |
|------|---------|
| `backend/app/services/agent/agents/verification.py` | R1 确定性状态引擎 `compute_verification_status`；R2 全量证据强制绑定；R3 确定性沙箱执行 + `fabricated` 标记 + 反伪造提示词 |
| `backend/app/services/agent/agents/orchestrator.py` | R4 门禁 3 次终止 + observations 记录；R5 Bug D 判定修正；R7 子 Agent 中断收口 |
| `backend/app/services/agent/config.py` | 新增 `verification_max_force_redispatch` 配置 |
| `backend/app/api/v1/endpoints/agent_tasks.py` | R6 observations 持久化到 `agent_tasks.observations` |

### Files Modified (Tests)

| File | Changes |
|------|---------|
| `backend/tests/agent/test_verification_evidence.py` (new) | 确定性判定矩阵、fabricated 排除、ID 强制绑定 |
| `backend/tests/agent/test_orchestrator_gates.py` (new) | 门禁 3 次终止、Bug D 判定、observations 写入 |

### Files Modified (Specs)

| File | Changes |
|------|---------|
| `openspec/changes/fix-verification-evidence-root/specs/audit-engine/spec.md` | Delta spec: 确定性验证结论引擎 + 证据绑定 + 反伪造 + 门禁终止 |

## Total: 6 files (backend 4 + tests 2) + 1 migration 无

## Risk Assessment

- **状态口径变化（中）**：R1 后部分历史 finding 由 needs_context 变为 confirmed/not_reproducible，属预期且正确的方向；需全量单测兜底。
- **验证耗时增加（低-中）**：R3 确定性执行每个 finding 一条预生成 PoC（约 30s），可配置开关。
- **无 DB 迁移**：observations/sandbox_attempts 字段已存在。
- **不触碰覆盖率门禁阈值**：D1-D10 定义不变，仅门禁终止逻辑调整。

## Verification Approach

1. 单测：确定性判定矩阵（证据→状态全组合）、fabricated 排除、ID 强制绑定、门禁 3 次终止、Bug D 判定修正、observations 写入。
2. 回归：`uv run pytest` 全量；`uv run ruff check .`。
3. 真实项目回归（可选，待部署后）：nacos 项目重跑，验证 5 个发现的验证状态被正确判定并落库。
