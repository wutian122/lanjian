# verification-completeness

## Purpose

The verification-completeness capability documents the published behavior for users and maintainers.

## Requirements

### Requirement: REQ-VC-1 待验清单必须全量送验
Verification Agent 的待验证清单 MUST NOT 被固定数量上限截断：`findings_to_verify` 必须包含调度方传入的全部 finding；orchestrator 向 verification 的交接 MUST 以 `_all_findings`（累计全量，按 severity 排序）为数据源，不得仅取最后一轮 analysis 的返回。

#### Scenario: 跨多轮 analysis 的 finding 全部进入验证
- **WHEN** 项目跨 2 轮 analysis 累计产出 25 个 finding（第 1 轮 10 个、第 2 轮 15 个），orchestrator 调度 verification
- **THEN** verification 的 `findings_to_verify` 包含全部 25 个 finding（不被 `[:20]` 截断），且每个 finding 均生成并执行对应的确定性沙箱 PoC（attempts 记录非空）

### Requirement: REQ-VC-2 验证门禁放行前必须程序化补验
当验证证据门禁（R4）因达到 `verification_max_force_redispatch` 上限而放行时，orchestrator MUST 在收尾前对仍未验证的 finding 子集**程序化直接调度** verification（不经 LLM 的 ReAct 决策），确保"从未成功调度 verification"也不会导致全量零证据收尾；该收口调度 MUST 幂等（已全部验证则不触发）。

#### Scenario: LLM 从未调度成功 verification
- **WHEN** 审计过程 LLM 从未成功调度 verification（verification_count=0），finish 门禁连续拒绝达到上限触发 R4 放行
- **THEN** orchestrator 在放行收尾前直接调用 verification 调度，未验证 finding 获得确定性沙箱执行记录（attempts 非空），而非全量零证据收尾

### Requirement: REQ-VC-3 验证结果合并必须按 finding_id 回写
verification 返回的结果合并回 orchestrator 的 `_all_findings` 时 MUST 优先按 `_sandbox_finding_id` 匹配原对象（路径/行号漂移不阻断回写），MUST NOT 产生"已验证副本 + 零证据原件"并存。

#### Scenario: 路径格式漂移不产生双份
- **WHEN** verification 输出的 finding `file_path` 与原件格式不同（如路径归一化/追加行号），但 `_sandbox_finding_id` 一致
- **THEN** 合并后 `_all_findings` 中只有一份该 finding，且带验证证据（无零证据原件残留）

### Requirement: REQ-VE-1 预生成模板 PoC 必须可执行不崩溃
`_gen_sandbox_command` 全部模板生成的 PoC 源码 MUST 通过 Python 语法与正则编译检查；其中 ssrf（2 处）、path_traversal、deserialization 模板的正则转义 bug MUST 修复——生成文本中所有 `re` pattern 必须是合法的可编译正则（不得出现未闭合分组）。

#### Scenario: 三模板 PoC 正则可编译
- **WHEN** 生成 ssrf / path_traversal / deserialization 模板的 PoC 命令文本
- **THEN** 文本中每个正则 pattern（`r'...'` 字面量）均可被 `re.compile` 成功编译；PoC 在沙箱执行不再因 `re.error: missing ), unterminated subpattern` 崩溃

### Requirement: REQ-VE-2 验证器崩溃必须与未复现分档
沙箱 attempt 记录 MUST 检测"验证器自身错误"（输出含 Traceback / SyntaxError / re.error 等 PoC 自身崩溃特征）并标记 `poc_error=True`；当某 finding 的全部 attempt 均为 `poc_error` 时，其验证状态 MUST 为 `needs_context`（notes 注明"pre-generated PoC crashed"），MUST NOT 判 `not_reproducible`（该状态只留给"PoC 正常执行但未复现"）；软证据兜底（static_confirmed 升级路径）MUST 排除全部 attempts 为 poc_error 的 finding。

#### Scenario: 模板崩溃不冒充未复现
- **WHEN** 某 finding 的 2 条 attempt 输出均含 `re.error: missing ), unterminated subpattern`（exit 1）
- **THEN** 两条 attempt 均标记 `poc_error=True`，finding 状态为 `needs_context`（notes 注明验证器崩溃），且不被软证据兜底升级为 static_confirmed

#### Scenario: PoC 正常执行未复现仍是 not_reproducible
- **WHEN** 某 finding 的 attempt exit 0 且无漏洞触发标记（PoC 正常跑完但未复现），或 exit!=0 但无 Traceback 类崩溃特征
- **THEN** 状态为 `not_reproducible`（既有语义不回退）

### Requirement: REQ-VP-1 模板演示确认必须挂源码 sink 断言
6 类模板（sql_injection/command_injection/xss/auth_missing/tenant_isolation/idor）的演示性确认输出 `VULNERABILITY_CONFIRMED(STATIC)` MUST 以源码断言段统计的对应 sink 关键词计数 > 0 为前提；计数为 0（目标源码不存在对应漏洞模式）时 MUST NOT 输出确认标记，改为输出 `NO_SINK` 类提示（该 attempt 不进入 static_confirmed 判定）。

#### Scenario: 无对应 sink 不确认
- **WHEN** sql_injection 模板 PoC 的目标源码存在但源码断言段统计 SQL sink 关键词计数为 0
- **THEN** PoC 输出 `NO_SINK` 提示，不输出 `VULNERABILITY_CONFIRMED(STATIC)`；对应 finding 不判 static_confirmed

#### Scenario: 有对应 sink 保持确认
- **WHEN** 同一模板 PoC 的目标源码含 SQL sink 关键词（计数 > 0）
- **THEN** PoC 输出 `VULNERABILITY_CONFIRMED(STATIC)`；对应 finding 保持 static_confirmed（既有 B6 语义）

### Requirement: REQ-VP-2 static_confirmed 必须有证据才计入沙箱验证
orchestrator 的 `_has_valid_sandbox_evidence` 对 `verification_status == static_confirmed` 的 finding MUST 额外要求 `sandbox_attempts` 非空，否则不视为"有有效沙箱证据"（防止免沙箱路径击穿 finish 门禁）。

#### Scenario: 免沙箱 static_confirmed 不再击穿门禁
- **WHEN** finding 状态为 static_confirmed 但 `sandbox_attempts` 为空（免沙箱路径产生）
- **THEN** `_has_valid_sandbox_evidence` 返回 False；finish 验证门禁据此仍判定"无有效沙箱证据"

### Requirement: REQ-VQ-1 证据摘要与去重必须保全证据
attempt 的 `evidence_summary` 截断 MUST 采用保头尾策略（与历史截断一致，保留尾部确认标记）；同一 finding 的 attempt 去重 MUST 优先保留"含漏洞触发证据"的 attempt（不得因先到者无证据而丢弃后到的带证据 attempt）。

#### Scenario: 大输出不丢尾部确认标记
- **WHEN** LLM 自写 PoC 输出超过 5000 字符且 `VULNERABILITY_CONFIRMED` 位于输出尾部
- **THEN** 落库的 `evidence_summary` 保头尾截断后仍包含该确认标记（不因纯头部截断丢失）

#### Scenario: 同键去重保留证据
- **WHEN** 同一 finding 出现两条 command/exit_code 相同的 attempt：先到者无漏洞触发证据，后到者含确认标记
- **THEN** 去重后保留含确认标记的 attempt（finding 状态据实升级）

### Requirement: REQ-VR-1 既有验证语义不回退（约束）
V6 的 B1-B6 与 R1-R7 语义 MUST 不回退：绑定层如实转运、失败标记收窄、空 Final Answer 回填、finding_id 索引、会话瘦身、模板源码断言（缺失 exit 1）、R1 状态机既有分支判定、门禁 R4 终止与 observations 落库、R7 中断收口。

#### Scenario: 全量回归
- **WHEN** 本变更实现完成后运行 `tests/agent` 全量
- **THEN** 既有测试（含 test_verification_evidence.py / test_orchestrator_gates.py / test_strict_verification_states.py）全部通过，无断言语义回退
