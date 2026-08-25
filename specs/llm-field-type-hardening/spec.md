# llm-field-type-hardening

## Purpose

The llm-field-type-hardening capability documents the published behavior for users and maintainers.

## Requirements

### Requirement: REQ-TH-1 数值字段必须有中心归一化工具
系统 MUST 提供 `_to_int`/`_to_float` 归一化工具：`_to_int` 将 "113"/"113.0"/113/None/""/非法值 归一到 int（失败返回 None），`_to_float` 同理归一到 float。

#### Scenario: 工具边界
- **WHEN** 传入 "113"/"113.0"/113/None/""/"abc" 到 `_to_int`
- **THEN** 分别返回 113/113/113/None/None/None，不抛异常

### Requirement: REQ-TH-2 分析标准化源头必须归一化数值字段
`analysis.py` 标准化 finding 时 MUST 将 `line_start`/`line_end` 转 int、`confidence`/`ai_confidence` 转 float（LLM 的 str/None 值在此归一），不得原样透传。

#### Scenario: 源头归一
- **WHEN** LLM finding 的 `line_start="113"`、`confidence="0.85"` 进入 analysis 标准化
- **THEN** 输出 finding 的 `line_start=113`（int）、`confidence=0.85`（float）

### Requirement: REQ-TH-3 严格校验与落库不得因类型崩溃
`strict_finding.py` 的 `line_start <= 0` 判定 MUST 经 `_to_int` 防御（str/None/非法不崩溃）；`agent_tasks.py` 落库前 `line_start`/`line_end` MUST 强制 int（失败置 None），MUST NOT 以 str 直传 DB Integer 列（防 asyncpg 整批 rollback）。

#### Scenario: 崩溃与回滚根治
- **WHEN** finding 的 `line_start="113"` 进入 `is_strict_finding` 与 `_save_findings`
- **THEN** 校验不崩溃、落库为 int 113，findings 正常提交不 rollback

### Requirement: REQ-TH-4 verification 数值比较不得因类型崩溃
`verification.py` 的 `ai_confidence >= 0.75`（软证据兜底）与 `exit_code == 0`/`!= 0` 判定 MUST 经 `_to_float`/`_to_int` 归一（str/None 不抛 TypeError、不静默误判）。

#### Scenario: verification 类型防御
- **WHEN** finding 的 `ai_confidence="0.9"`（str）进入软证据兜底，attempt 的 `exit_code="0"` 进入判定
- **THEN** 不崩溃；`0.9 >= 0.75` 成立、`exit_code == 0` 判定正确

### Requirement: REQ-TH-5 回归与既有语义不回退
类型防御 MUST 不改变 `compute_verification_status` 既有分支语义（B1-B6/R1-R7 不回退）；`tests/agent` 全量回归通过。

#### Scenario: 全量回归
- **WHEN** 实现完成运行 `tests/agent`
- **THEN** 既有用例全部通过

### Requirement: REQ-ER-1 中断/取消时必须绑定已执行证据
verification 在取消/超时返回结果前，MUST 对 findings_to_verify 执行确定性证据绑定（`_finalize_findings_without_final_answer`），MUST NOT 直接返回未绑定 findings。

#### Scenario: 超时取消不丢证据
- **WHEN** verification 确定性执行已跑完 PoC 后因 1200s 超时被取消（is_cancelled）
- **THEN** 返回的 findings 带 sandbox_attempts（已执行证据不随内存丢失）

### Requirement: REQ-ER-2 成功路径必须有最终兜底绑定
verification 在 R2 全量绑定后，对仍无沙箱证据的 finding MUST 执行最终兜底绑定（`_bind_unbound_runtime_evidence`，按 ID/位置）。

#### Scenario: 成功路径不落零证据
- **WHEN** LLM Final Answer 未覆盖某 finding 且 R2 绑定未命中（归一化缝隙）
- **THEN** 最终兜底绑定命中，finding 的 sandbox_attempts 非空

### Requirement: REQ-ER-3 验证超时与 PoC 策略治理
verification 子 agent 超时 MUST 放宽至 ≥1800s；系统提示 MUST 引导 LLM 使用轻量 PoC（禁止启动完整服务/框架）。

#### Scenario: 超时与提示词
- **WHEN** orchestrator 调度 verification 且 verification 系统提示生成
- **THEN** verification 超时 ≥1800s；提示词含"轻量 PoC/禁止启动完整服务"
