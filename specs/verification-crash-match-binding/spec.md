# verification-crash-match-binding

## Purpose

修复验证过程的四个缺陷：空 file_path 生成崩溃 PoC、铁证被匹配逻辑丢弃、无 ID finding 证据绑定丢失、网络依赖漏洞验证死循环，确保"有过验证 = 有正确结果"。

## Requirements

### Requirement: REQ-CM-1 空 file_path 不生成崩溃 PoC

`_gen_sandbox_command` SHALL 对 `file_path` 为空的 finding 不生成会打开目录的 PoC（IsADirectoryError 崩溃）。

#### Scenario: 空 file_path 跳过硬编码 src

- **WHEN** 一个 finding 的 `file_path` 为空且调用 `_gen_sandbox_command`
- **THEN** 生成的 PoC 不得包含 `src = '/workspace/src/'` 且随后 open(src) 的结构
- **AND** 返回 None 或生成"按标题关键词搜索项目源码"的变体

#### Scenario: 空 file_path 不崩溃

- **WHEN** 提取并执行生成 PoC 的命令
- **THEN** 无 `IsADirectoryError` / 无 exit 1 的目录打开错误

### Requirement: REQ-CM-2 铁证 matching 不因 līn 缺失丢弃

`_sandbox_attempt_matches_finding` SHALL 在 attempt 有 `VULNERABILITY_CONFIRMED` 证据且文件路径匹配时，不因 `target_ref` 缺 `:line` 拒绝匹配。

#### Scenario: linear-free target_ref 判 confirmed

- **WHEN** attempt 含 `VULNERABILITY_CONFIRMED`、success=True、exit 0，`target_ref` 只有文件路径（无 `:line`）
- **AND** finding 的 `file_path` 与 `target_ref` 匹配（含子串/后缀）
- **THEN** `compute_verification_status` 判 `confirmed`

#### Scenario: file_path 子串兜底

- **WHEN** B1 兜底查询时 finding 的 `file_path` 或 vuln_type 词出现在 evidence/command
- **THEN** 匹配成功（含文件路径 base name 匹配）

### Requirement: REQ-CM-3 无 _sandbox_finding_id 的 finding 仍绑定证据

确定性执行与绑定层 SHALL 为无 `_sandbox_finding_id` 的 finding 提供降级匹配，使运行时证据落入 `sandbox_attempts`。

#### Scenario: 无 ID 仍绑定

- **WHEN** finding 无 `_sandbox_finding_id`（metadata 只有 discovery_source），确定性执行已运行对应 PoC
- **THEN** `_attach_runtime_sandbox_attempts` 经扁平列表 + 位置/路径兜底成功绑定（sandbox_attempts 非 null）
- **AND** 结果 `verification_status` 不再是 needs_context-when-attempt-exists

### Requirement: REQ-CM-4 网络依赖漏洞沙箱尝试受限

对沙箱 `network=none` 下不可真实复现的漏洞类型（ssrf），单 finding 的沙箱尝试次数 SHALL 受上限约束，防 LLM 百次死循环。

#### Scenario: 单 finding 尝试上限

- **WHEN** verification 对某 finding 的沙箱执行次数达到上限
- **THEN** 停止重复沙箱尝试，evidence 标注"沙箱网络受限不可复现"（或对应的 needs_context 理由），不再门禁反复拒绝

#### Scenario: 常规验证不受限

- **WHEN** 可离线复现的漏洞类型（命令注入/XSS 等）
- **THEN** 不受该上限限制
