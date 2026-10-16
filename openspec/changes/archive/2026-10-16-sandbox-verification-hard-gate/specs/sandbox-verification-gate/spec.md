# Delta Spec: sandbox-verification-gate

## ADDED Requirements

### Requirement: 基础设施故障 MUST NOT 伪装成漏洞未复现

`_record_sandbox_attempt` SHALL 识别基础设施错误签名（"Docker not available"、"沙箱环境不可用"、"ImageNotFound"、"No such image"、"pull access denied"、docker `APIError` 文本）并将该 attempt 标记 `infra_error=true`。`compute_verification_status` SHALL 在"有尝试无铁证 → not_reproducible"分支（现 196-200 行）之前判定：若 finding 的全部真实尝试均为 `infra_error=true`，终态 SHALL 为 `needs_context`（附诊断说明），`is_verified=false`。`not_reproducible` 终态 SHALL 仅用于沙箱真实执行过且未复现的情况。

#### Scenario: 沙箱镜像缺失时 finding 不会被标记为未复现
- **WHEN** 某 finding 的全部沙箱尝试均因 "ImageNotFound/pull access denied" 失败
- **THEN** `verification_status="needs_context"`，verification_details 含基础设施诊断，前端可见"沙箱环境故障"而非"不可复现"

#### Scenario: 真实执行未复现仍为 not_reproducible
- **WHEN** PoC 在沙箱内正常执行（exit_code=0）但无确认证据
- **THEN** 终态仍为 `not_reproducible`（行为不变）

### Requirement: 每个 finding 终态前 SHALL 至少一次沙箱执行或显式豁免标记

Verification Agent 输出终态前，每个 finding MUST 满足以下之一：(1) `sandbox_attempts` 非空（至少一次真实沙箱执行）；(2) `sandbox_skip_reason` 非空（含原因）；(3) 全部尝试为 `infra_error`（见上条）。以下既有豁免路径 SHALL 封堵：

- **Semgrep 静态短路**（现 2271-2281 行）：hardcoded_secret/weak_crypto/deserialization/xxe 四类 SHALL 先执行对应确定性 PoC 再判 `static_confirmed`；无 attempt 时不得置 `is_verified=true`
- **软证据升级**（现 2296-2320 行）：前置条件 SHALL 增加 `len(attempts)>0`，不满足时降为 `needs_context`
- **R4 放行**（orchestrator.py 现 1137-1207 行）：连续拒绝达上限放行收尾时，所有未验证 finding SHALL 强制标记 `sandbox_skip_reason="gate_release_after_max_redispatch"` 且报告 MUST 呈现"未沙箱验证清单"
- **弹性退出**（现 1142-1148 行）：放行 finish 时剩余未验证 finding SHALL 标记 `needs_context(elastic_exit)`
- **预算耗尽/取消收口**（现 1329-1350 行）：SHALL 仿 cancel 路径先补跑剩余未执行的确定性 PoC 再收口
- **LLM 拒调兜底**（现 1404-1474 行）：0 次 sandbox_exec 时的程序化兜底 SHALL 遍历全部 `sandbox_commands`，不得只执行第一个

#### Scenario: Semgrep 短路类型也经过沙箱
- **WHEN** 一个 source=semgrep 的 hardcoded_secret finding 进入验证
- **THEN** 对应确定性 PoC 被执行并产生 attempt，终态基于 attempt + 静态证据共同推导

#### Scenario: 零执行 + 四件套齐备不再直接 static_confirmed
- **WHEN** 某 SSRF finding 四件套（dataflow_path/code_snippet/ai_confidence≥0.75/verification_method）齐备但 attempts 为空且无 skip_reason
- **THEN** 终态为 `needs_context`，不再是 `static_confirmed`

#### Scenario: 兜底覆盖全部待验证 finding
- **WHEN** LLM 循环 0 次 sandbox_exec 且存在 5 个待验证 finding
- **THEN** 程序化兜底对 5 个 finding 的确定性 PoC 全部执行

### Requirement: SSRF 确定性 PoC 网络参数 SHALL 传递到容器

`_run_deterministic_sandbox_commands`（现 2502-2507 行）调用 `execute_with_files` 时 SHALL 将模板产出的 `network_enabled` 参数映射为容器 `network_mode`（true → `bridge`/配置值，false → `none`），不再无条件落默认 `"none"`。SSRF 模板（现 2817-2864 行）的 metadata 探测在 `network_enabled=true` 时 SHALL 有机会真实执行。

#### Scenario: SSRF PoC 在允许联网时真实探测
- **WHEN** SSRF 模板生成 `network_enabled=true` 的命令且系统配置允许沙箱联网
- **THEN** 容器以非 none 网络模式创建，169.254.169.254 探测真实发出，成功时输出确认标记

#### Scenario: 联网被禁时行为不变
- **WHEN** `SANDBOX_NETWORK_ENABLED=false`
- **THEN** 容器仍为 none 模式，输出 degraded 提示（现状行为）

### Requirement: 三个无确认输出模板 SHALL 补全证据分支

path_traversal（现 2792-2816 行）、hardcoded_secret（3071-3098 行）、deserialization（3099-3124 行）三个确定性 PoC 模板 SHALL 补充明确的确认输出标记（与其它模板一致的 `VULNERABILITY_CONFIRMED(STATIC)` 或 `VULNERABILITY_STATIC_ONLY` 语义），使确定性执行对这三类漏洞能产出可推导的证据。

#### Scenario: path_traversal PoC 能产出证据
- **WHEN** 一个有真实 file_path 的 path_traversal finding 执行确定性 PoC 且源码确认存在路径拼接 sink 与 `os.path` 非法拼接
- **THEN** 输出含确认标记，`compute_verification_status` 可推导出 `static_confirmed`

### Requirement: 沙箱证据关键语义标记 MUST 前端可见

Finding 详情的沙箱证据区 SHALL 展示 attempt 的 `fabricated`（伪造降级）、`static_evidence`（演示性确认降档）、`poc_error`/`poc_error_type`（验证器崩溃）三个语义标记，使使用者能区分"真实动态确认"、"演示性静态确认"与"验证器故障"。`verification_status_breakdown` 的统计口径 SHALL 与上述新语义一致。

#### Scenario: 演示性确认被标注
- **WHEN** 某 finding 的 confirmed 证据来自 `VULNERABILITY_CONFIRMED(STATIC)` 模板输出
- **THEN** 前端该 attempt 显示"演示性静态确认"标记，用户不会误读为动态利用成功
