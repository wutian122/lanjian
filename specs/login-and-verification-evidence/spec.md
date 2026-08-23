# login-and-verification-evidence

## Purpose

The login-and-verification-evidence capability documents the published behavior for users and maintainers.

## Requirements

### Requirement: REQ-LR-1 登录成功后固定落仪表盘
用户在登录页完成登录并认证成功后，系统 MUST 将用户导航至 `/dashboard`，无论登录前从哪个页面跳转而来。

#### Scenario: 会话失效于业务页后重新登录
- **WHEN** 用户在 `/projects/123` 会话失效，被守卫带 `state.from=/projects/123` 踢到 `/login`，随后登录成功
- **THEN** 前端导航至 `/dashboard`，不回跳 `/projects/123`

#### Scenario: 直接访问登录页
- **WHEN** 用户直接访问 `/login`（无 `location.state`）并登录成功
- **THEN** 前端导航至 `/dashboard`

### Requirement: REQ-VE-1 失败沙箱尝试必须如实绑定到 finding
证据绑定层（`_attach_runtime_sandbox_attempts` 及确定性执行直写路径）MUST NOT 以 `success=True` 作为绑定前置条件；`success=False` 的 attempt MUST 与成功 attempt 一样按 finding_id 绑定到对应 finding 的 `sandbox_attempts`，由 `compute_verification_status` 据实推导状态。

#### Scenario: 全部尝试失败
- **WHEN** 某 finding 的运行时沙箱尝试全部为 `success=False`（有执行、未复现），LLM Final Answer 未携带该 finding
- **THEN** 该 finding 落库的 `sandbox_attempts` 非空（含失败记录），`verification_status` 为 `not_reproducible`，而非 `needs_context`

#### Scenario: 成功尝试不再被绑定层丢弃
- **WHEN** 某 finding 存在 finding_id 匹配的 attempt，无论 success 与否
- **THEN** 绑定结果包含该 finding_id 的全部 attempt（不过滤）

### Requirement: REQ-VE-2 失败标记判定必须结合退出码与输出段
`SANDBOX_FAILURE_MARKERS` 中的 `"Error:"`、`"Traceback"` 等子串 MUST 仅在 `exit_code != 0`，或子串位于输出的 stderr/错误段内时才将 attempt 判为失败；`exit_code == 0` 且证据摘要含漏洞触发标记（`VULN_EVIDENCE_MARKERS`）的输出 MUST NOT 因 incidental 的失败子串被误杀为失败。

#### Scenario: 成功输出含 incidental 失败子串
- **WHEN** attempt 输出 `exit_code=0`、evidence_summary 含 `VULNERABILITY_CONFIRMED`，且正文某处 incidental 出现 `Error:` 字样（如变量名 `error_code` 不匹配，但 `cmd error=` 拼写为 `Error:` 的边缘）
- **THEN** attempt 的 `success` 判定为 `True`

#### Scenario: 真失败仍被识别
- **WHEN** attempt `exit_code=1` 且 stdout 含 `Traceback`
- **THEN** attempt 的 `success` 判定为 `False`

### Requirement: REQ-VE-3 LLM 空 Final Answer 时必须回填运行时证据
当 LLM Final Answer 的 findings 为空（走 fallback 分支）而运行时已存在确定性执行或 LLM 路径的沙箱尝试时，系统 MUST 按 finding_id 将运行时尝试回填到各 finding 的 `sandbox_attempts` 后再归一化状态；MUST NOT 直接以 `needs_context` 覆盖有证据的 finding。

#### Scenario: 空 Final Answer + 确定性证据存在
- **WHEN** 3 个 finding 的预生成 PoC 已确定性执行并记录尝试，LLM Final Answer findings 为空
- **THEN** 3 个 finding 均带 `sandbox_attempts` 回填，状态由 `compute_verification_status` 据实判定（成功+铁证 → confirmed/static_confirmed；执行未复现 → not_reproducible），落库 `sandbox_attempts` 非 NULL

### Requirement: REQ-VE-4 确定性沙箱执行必须按 finding_id 直写证据索引
`_run_deterministic_sandbox_commands` 在执行每条预生成命令时 MUST 以该命令的 `finding_id` 为键，将执行结果直接登记到运行时证据索引（按 finding_id → attempts 的映射），供绑定与回填路径消费；绑定正确性 MUST NOT 依赖从命令文本反解 `# FINDING_ID:` 注释（注释可保留用于日志可读性）。

#### Scenario: 确定性执行后证据可按 id 检索
- **WHEN** 确定性执行完成 N 条预生成命令（每条携带 finding_id）
- **THEN** 运行时证据索引中 N 个 finding_id 均有对应 attempt 记录，且不依赖命令文本解析

### Requirement: REQ-VE-5 verification 会话上下文必须有界
Verification Agent 的 conversation_history MUST 有界：单条 observation 写入历史前 MUST 截断至可配置上限（保留头尾）；累计历史长度超过阈值时 MUST 对最早批次消息做摘要化压缩。Final Answer 前的有效上下文 MUST 保持在配置上限内。

#### Scenario: 长会话不失控
- **WHEN** verification 循环迭代产生大量长 observation（超过单条上限与累计阈值）
- **THEN** conversation_history 中单条消息与总长度均不超过配置上限，Final Answer 仍可被正常解析（findings 非空或走 fallback 且证据已回填）

### Requirement: REQ-VE-6 模板 PoC 证据必须与目标源码挂钩
预生成模板 PoC 的输出 MUST 引用目标源码实际内容做断言：源码文件不存在或断言不成立时 MUST NOT 输出漏洞确认标记；纯演示性验证（内存 SQLite、自建 mock 自证，与目标源码无数据流因果）的 attempt MUST 标记 `static_evidence=True`，其最高可判状态为 `static_confirmed`，MUST NOT 判 `confirmed`。

#### Scenario: 演示性模板确认被降档
- **WHEN** SQL 注入模板 PoC 在内存 SQLite 演示注入成功（打印 `VULNERABILITY_CONFIRMED`），该输出与目标源码无数据流因果（源码断言不成立或仅静态读取）
- **THEN** 该 attempt 携带 `static_evidence=True`，对应 finding 状态为 `static_confirmed` 而非 `confirmed`

#### Scenario: 源码缺失不得输出确认标记
- **WHEN** 模板 PoC 在沙箱中找不到目标源码文件
- **THEN** PoC 输出 `Source not found` 类标记且不输出确认标记，attempt 不判成功

### Requirement: REQ-VR-1 验证状态机输入完整性（对既有 R1 的细化）
`compute_verification_status` 的状态机逻辑不变（R1 语义不回退），但其输入 MUST 满足：进入归一化的每个 finding 的 `sandbox_attempts` 已按 REQ-VE-1/3/4 完成如实绑定/回填。

#### Scenario: 状态机输入完整
- **WHEN** 任一 verification 轮次结束进入归一化
- **THEN** 每个 finding 的 sandbox_attempts 已包含其全部运行时尝试（无论成败），状态推导仅取决于证据内容

### Requirement: REQ-VS-1 版本清单同步至 6.0.0
backend pyproject、frontend package.json、各 docker-compose 变体、README 与 AGENTS 文档的版本号 MUST 一致为 6.0.0。

#### Scenario: 版本一致性
- **WHEN** 运行 `ssf version 6.0.0` 后检查全部清单
- **THEN** 所有版本引用均为 6.0.0，无残留 5.4.0（compose 中 sandbox 镜像锁 v5.1.0 除外，本次不重建沙箱镜像）
