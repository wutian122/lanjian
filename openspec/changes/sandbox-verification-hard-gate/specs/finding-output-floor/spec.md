# Delta Spec: finding-output-floor

## ADDED Requirements

### Requirement: Analysis SHALL 输出分层候选而非仅高置信发现

ANALYSIS_SYSTEM_PROMPT 的产出策略 SHALL 从"宁可漏报，不可误报"的绝对表述调整为分级产出：高置信发现（confidence ≥ 0.7）正常报告；低置信可疑点（0 < confidence < 0.7）SHALL 作为候选输出并标记 `needs_verification=true`（交由 Verification Agent 沙箱验证证实或证伪）；Anti-hallucination 规则保留（仅报告实际读取代码中看到的模式），但其表述 MUST 不阻止候选产出。Analysis 不得把"验证可利用性"作为报告前置条件——可利用性验证是 Verification Agent 的职责。

#### Scenario: 低置信可疑点以候选形式产出
- **WHEN** Analysis 判断某数据流"疑似可注入但被中间层部分缓解"
- **THEN** Final Answer findings 中出现该候选（confidence 0.3-0.6、needs_verification=true），而非直接丢弃

#### Scenario: 幻觉防护仍然生效
- **WHEN** Analysis 未在项目代码中实际看到某模式
- **THEN** 不得输出该模式的发现或候选（既有防幻觉规则不放松）

### Requirement: 强制总结 SHALL 有维度级产出下限

`_run_forced_summary`（现 analysis.py:419-467 行）SHALL 要求：对每个未覆盖维度（D1-D10 缺口），输出至少 1 个候选发现或显式书面豁免（说明为何该维度在本项目不适用）；解析后候选数与豁免数 SHALL 回写结果 data（`dimension_gaps_reported`）。强制总结结果为"0 候选且 0 豁免"时 SHALL 触发一次重试提示，仍为空则 orchestrator 侧记录为覆盖不足证据（不再自动放行 finish）。

#### Scenario: 未覆盖维度必须给候选或豁免
- **WHEN** 强制总结时 D3（SSRF）与 D7（反序列化）无覆盖
- **THEN** 输出包含这两维度各自的候选或"本项目为 Java 服务无 SSRF 入口"类书面豁免，data 含 dimension_gaps_reported

#### Scenario: 全空总结不再静默通过
- **WHEN** 强制总结连续两次 0 候选 0 豁免
- **THEN** orchestrator 记录 `observations: {gate: "output_floor", ...}` 并按覆盖不足语义收口（completed_with_gaps），报告中呈现"分析未按要求产出候选"

### Requirement: Analysis 0 产出时 Semgrep 发现 SHALL 兜底落库

Orchestrator 在 Analysis 全部派发完成且 `_all_findings` 为空时，SHALL 将 Semgrep 预扫发现（去重、按规则映射 vulnerability_type/severity）作为待验证候选写入 findings（confidence 取规则置信度默认 0.5、`needs_verification=true`、`source="semgrep_fallback"`），这些候选 SHALL 进入 Verification Agent 验证队列。兜底落库的候选在报告中 SHALL 标注来源，与 Analysis 高置信发现区分。

#### Scenario: nacos 类任务不再两手空空
- **WHEN** Analysis 三轮派发后 0 findings 且 Semgrep 预扫有 63 条发现
- **THEN** 去重后的 Semgrep 发现作为候选落库并送沙箱验证，任务不再是 0 findings

#### Scenario: 兜底候选标注来源
- **WHEN** 报告生成时存在 `source="semgrep_fallback"` 的候选
- **THEN** 报告与前端详情标注"静态扫描兜底候选"，与人工级发现区分

### Requirement: orchestrator 候选口径 SHALL 纳入门禁判定

`has_findings`/`_all_findings` 相关的门禁判定（沙箱证据门禁、硬覆盖率门禁、全量验证门禁）SHALL 将 `needs_verification=true` 的候选纳入口径：候选存在即触发 verification 派发与验证完成度检查；`verification.py` 的 `findings_to_verify` SHALL 接受候选（该输入已支持 needs_verification 字段）。

#### Scenario: 仅候选时验证门禁照常工作
- **WHEN** 任务只有 3 个候选（无高置信发现）
- **THEN** verification 被派发验证 3 个候选，门禁按候选口径计算覆盖率与验证完成度
