# Tasks: sandbox-verification-hard-gate

## Phase 0: LLM 调用韧性（2026-09-04 服务端排查结论吸收）

> 排查结论：①R1 空响应=reasoning 吃光 max_tokens 预算（已由 32768 修复，护栏保留）；②R2 残余空响应=模型 reasoning 后自然停止零正文（finish_reason=stop、无截断、无服务端错误，纯模型行为）；③Orchestrator 空 name/空参数 tool_calls（模型退化，Task 7 自愈路径喂回后仍可能连续退化）；④10.129.2.101 服务端 mm 崩溃（32 Traceback）对应老板下午任务时段，触发器精确复现未完成、**服务端零操作**（老板指令），mm 触发器二分诊断挂起。

### Task 20: 空响应强化重试（reasoning-后-无正文 nudge）
- [x] **Task 20 完成**（commit 30e06af，review CLEAN——tool_calls 轮豁免双重保险、判定链顺序正确（tool_calls→truncated→reasoning_only→other）、四接入点无漏接、上限轮不追加锁定、与 Task 2B 截断机制/Task 8 submit_findings 协同不冲突；3 Minor 记账：空列表归一注释、getattr 冗余防御、run 循环 mock 桩强度）
- Files: `backend/app/services/agent/agents/base.py`（stream_llm_call 空响应判定处 :1330 附近）、`backend/app/services/agent/agents/{analysis,recon,verification}.py`（空响应重试提示词 :706-730/:955-959 等各处）
- Interfaces: 空响应重试提示词升级——区分两种形态并分别 nudge：①finish_reason=stop 且正文空（"你上一轮只输出了思考没有给出行动，请直接输出 Action 或调用工具，不要重复思考"）；②finish_reason=length 且正文空（提示由 B 变更截断机制已有，此处补充"输出预算被思考耗尽，请精简思考"）。连续空响应达上限后的收口行为保持现状
- TDD: 失败测试（mock 两种空响应形态 → 断言重试提示包含对应 nudge 文案且连续计数正确）→ 实现 → 通过 → commit

### Task 21: 空/无效 tool_calls 强 nudge 自愈
- [x] **Task 21 完成**（实施 e31de50 + 回归测试补丁 759f11f，review 初审 NEEDS_FIX（Important：顺带修复的 else 分支回写无测试锁定——变异实证删除该行 580 测试全绿）→ 重审 CLEAN（新测试为回写行唯一精确守卫，变异独立复核）；**顺带修复实锤：旧 else 分支 observation 从未回写 step.observation，"Observation:\nNone" 喂回模型——旧泛化自愈从未生效，R2 退化持续的真根因之一**；Minor 记账：连续无效无上限暂停（max_iterations 兜底）、分类器与映射的解析重复）
- Files: `backend/app/services/agent/agents/orchestrator.py`（_step_from_tool_calls :2108-2205 与未知操作分支 :1532-1534）
- Interfaces: 空 name/坏 JSON/空参数 tool_calls 的自愈 observation 强化——现状喂"未知操作: "泛化提示，改为：①空 name → 喂"工具调用缺少函数名，请重新输出，可用操作与参数 schema 如下：[完整三函数定义]"; ②dispatch_agent 空参数 → 喂"agent 参数缺失，必须为 recon/analysis/verification 之一，task 必须非空"; ③连续 2 次无效 tool_calls → 追加"请改用文本格式 Thought:/Action:/Action Input: 输出"（协议降级 nudge）
- TDD: 失败测试（三种无效形态 → 断言 observation 含 schema 重喂与协议降级 nudge）→ 实现 → 通过 → commit

## Phase 1: 基础设施语义修复

### Task 1: infra_error 标记与状态机分离
- [x] **Task 1 完成**（commit 6a699a6 + 0686dd9 缺口修复，review CLEAN——两 Scenario 8 探针实证、签名 8 项锁定、attempt 合并链 infra_error 保留性核对通过；实施者主动核验修复 language_test/fallback 两处缺口 + connection 守卫防误判（SSRF PoC 容器内拒绝是真实执行）；2 Important 交接：①**确定性路径 exit_code=-1 合成致 connection 签名抑制**（daemon 中断窄时序漏判）→ Task 2/3 处理 _format_sandbox_result 退出码语义；②**软证据升级不排除 infra_error**（needs_context 被洗白 static_confirmed）→ Task 6 验收强制核对；4 Minor 记账）
- Files: `backend/app/services/agent/agents/verification.py`（_record_sandbox_attempt :1606-1696、compute_verification_status :148-207）
- Interfaces: Produces attempt 新字段 `infra_error: bool`；compute 新分支 `("needs_context", "infra_error")`
- TDD: 写失败测试（attempt 全部含 "ImageNotFound"/"Docker not available" 签名 → 终态 needs_context 而非 not_reproducible；混合真实失败 → 仍 not_reproducible；成功铁证 → confirmed 不受影响）→ 跑失败 → 实现签名识别与分支前置 → 通过 → commit

### Task 2: execute_tool_command 异常返回补 stdout 键
- [x] **Task 2 完成**（实施 045388e + 修复 3839b8a，review 初审 NEEDS_FIX（Important：sandbox_language.py 六处渲染残留——"退出码: None"泄漏 + error 不渲染 + success 翻 True，infra 伪装在语言工具路径仍可达）→ 重审 CLEAN（六处守卫 + error 行闭环 _has_sandbox_failure_marker/_is_infra_error 双消费者、两个独立变异实证、fallback 五分支推演）；**_sandbox_failure 统一失败工厂 12 处收口 + exit_code 语义修正（None=未进容器/-1=超时）完整落地 Task 1 review Important-1**；4 Minor 记账：事件文案 exit=None、手动重跑端点不写 infra_error（Task 6 顺带）、sandbox_vuln.py 五处同款渲染（Minor）、fallback 持久化已修）
- Files: `backend/app/services/agent/tools/sandbox_tool.py`（:378-385）
- Interfaces: 异常返回 dict 与 :220-228 结构对齐（含 stdout/stderr 空串）
- TDD: 失败测试（mock containers.run 抛异常 → 返回 dict 含 "stdout" 键，SandboxTool._execute :860 不 KeyError）→ 实现 → 通过 → commit

### Task 3: SSRF 确定性 PoC network_mode 传递
- [x] **Task 3 完成**（commit 95e951e，review CLEAN——两 Scenario 达成（bridge 断言 + none 行为不变）、None 传参裁决复核正确（execute_with_files 裸透传 SDK，None=默认 bridge 反而开网）、两个调用点传递、RED 亲验（旧代码缺陷真实性确认）；**双门禁 sandbox_tool.py 超出声明范围但同 kill-switch 安全语义（R17 记账），修复了 LLM 自授网络的反向缺陷**；F1 Important 转后续任务：**sandbox_http/VulnerabilityVerifyTool 经 execute_http_request 无条件 bridge 且变异共享配置——kill-switch 全入口绕过未收口**；F4 建议后续对齐 AND 判定；F5 死配置核实为真（前端 sandboxNetworkEnabled 开关无后端消费者，仅 env 生效——后续接线或隐藏））
- Files: `backend/app/services/agent/agents/verification.py`（:2502-2507）
- Interfaces: Consumes 模板 `network_enabled` 与 `settings.SANDBOX_NETWORK_ENABLED`；Produces `execute_with_files(..., network_mode=...)` 实参
- TDD: 失败测试（network_enabled=True 且开关开 → execute_with_files 收到 bridge；开关关 → 收到 None/none）→ 实现 → 通过 → commit

### Task 4: 三个无证据模板补确认输出
- [x] **Task 4 完成**（commit 977f5a2，review CLEAN——双 Scenario 达成（三模板有 sink → 确认标记 → static_confirmed / 无 sink → not_reproducible 不回归）、真实执行 heredoc PoC 测试（非字符串断言）、两轮变异有效；**行外裁决复核属实：VULNERABILITY_STATIC_ONLY 此前零消费点（xss 输出一直空转 not_reproducible），扩展识别后 xss 首次生效且终态上限 static_confirmed 方向合理、提示词零暴露反伪造面不扩大**；2 Minor 记账：演示 base 目录不存在无影响（字符串运算）、pickle demo 无数据流因果（诚实措辞 STATIC_ONLY））
- Files: `backend/app/services/agent/agents/verification.py`（path_traversal :2792-2816、hardcoded_secret :3071-3098、deserialization :3099-3124）
- Interfaces: 输出标记 `VULNERABILITY_CONFIRMED(STATIC)` / `VULNERABILITY_STATIC_ONLY`（与 2773/2777 既有语义一致）
- TDD: 失败测试（构造三类 finding 的命令执行 → 输出含对应标记；无 sink → 无标记）→ 实现三分支 → 通过 → commit

## Phase 2: 沙箱硬门禁（豁免封堵）

### Task 5: Semgrep 静态短路先执行确定性 PoC
- [x] **Task 5 完成**（commit d06c472，review CLEAN——a 条五项核实（attempt 推导/证伪 not_reproducible/infra needs_context/零 attempt 不得 is_verified/非四类不变）+ weak_crypto/xxe skip_reason 豁免 + CONCERN 裁决（infra 优先于豁免）确认正确；**架构发现复核属实**：短路四类本来就执行确定性 PoC，缺陷在归一化无视 attempt，改造点收窄到归一化分流执行流零改动；三轮独立变异均被抓；3 Minor 记账：M1 infra 判定内联重复建议抽 helper、M2 零 attempt 补轻量单测、M3 ruff 存量）
- Files: `backend/app/services/agent/agents/verification.py`（:2271-2281）
- Interfaces: 短路类型 finding 先产生 attempt；无模板类型写 `sandbox_skip_reason="no_poc_template"`
- TDD: 失败测试（mock hardcoded_secret finding → 存在 attempt 且状态由 attempt+静态证据推导）→ 实现 → 通过 → commit

### Task 6: 软证据升级前置 attempts 非空
- [x] **Task 6 完成**（commit cddae98，review CLEAN——b 条四项+附加两项（fabricated 不得凑数/xss STATIC_ONLY 不受影响）全 ✅，四件套齐备的 infra finding 不再被洗白（note 矛盾消除）、真实执行过升级保持；**_attempt_is_infra 提为模块级语义零漂移（状态引擎与软证据共用同一判定）**；既有测试改写是 spec 授权非弱化；三轮变异精准；Task 1 交接两项验收完成；2 Minor 记账：Task 5 短路块内联 infra 判定重复（后续收口为 _attempt_is_infra）、勾选本条即 R1 落账）
- Files: `backend/app/services/agent/agents/verification.py`（:2296-2320）
- Interfaces: 升级前置条件 `len(非 infra attempts)>0`
- TDD: 失败测试（四件套齐备+0 attempts → needs_context；四件套+1 attempt → static_confirmed）→ 实现 → 通过 → commit

### Task 7: 弹性退出/预算耗尽/兜底遍历
- [x] **Task 7 完成**（commit c13a1d0，review CLEAN——f/g/h 三条全符合 + 5 组变异实证守卫；skip_reason 消费裁决实现一致（elastic_exit 硬门禁算豁免/orchestrator 仍算未验证可重派，UNVERIFIED_TERMINAL 不含 needs_context 核实）；幂等台账 _deterministic_done_finding_ids 生命周期正确（单次 run、异常不登记可重试）；**顺带修复实锤：零证据漏报 finding verification_status=None 出 Agent 的旧缺陷（变异守卫）；3 Minor 记账：台账登记无成功返回值校验（防御性）、g/h 失败重试语义日志连发、补跑成功后 infra_error 断言缺）
- Files: `backend/app/services/agent/agents/verification.py`（弹性退出 :1142-1148、预算耗尽 :1046-1048 与 :1329-1350、兜底 :1404-1474）
- Interfaces: 弹性退出写 `sandbox_skip_reason="elastic_exit"`；预算耗尽收口前补跑剩余确定性 PoC；兜底遍历全部 sandbox_commands
- TDD: 失败测试×3（对应三场景）→ 实现 → 通过 → commit

### Task 8: R4 放行的未验证清单强制标记与报告呈现
- [x] **Task 8 完成**（commit 0e32223，review CLEAN——d 条两项全满足 + 变异实证守卫；标记收口点五项过滤逐一核验（补验 confirmed 不标记/既有豁免不覆盖/四终态不标记/attempts 非空不标记）；**两路径扩展裁决接受**（轮次耗尽路径 orchestrator_max_iterations_exhausted——T6 注释 ec0985ad 生产回归背景，零证据收尾高发路径）；skip_reason 并入 verification_result JSON 持久化（AgentFinding 无独立列的合理方案）；3 Minor 记账：M1 deadline 路径豁免缺口转后续、M2 **前端展示承接归 Task 17**、M3 观测性）
- Files: `backend/app/services/agent/agents/orchestrator.py`（:1137-1207）、`backend/app/api/v1/endpoints/agent_tasks.py`（报告生成段）
- Interfaces: 放行时逐 finding 写 `sandbox_skip_reason="gate_release_after_max_redispatch"`；报告含"未沙箱验证清单"段落
- TDD: 失败测试（3 次拒绝后放行 → findings 带标记；报告文本含清单标题与 finding 标题）→ 实现 → 通过 → commit

## Phase 3: 产出下限

### Task 9: Analysis 分层候选提示词改造
- [x] **Task 9 完成**（commit 8354b1a，review CLEAN——spec 两 Scenario ✅（低置信候选产出/幻觉防护保留）、三处"宁可漏报"改写 + SUBMIT_FINDINGS 衔接无矛盾、归一化豁免（Task 8 未做本任务收口：双段重复 0.7 硬闸合并 + needs_verification≥0.1 豁免 + strict 同口径）；**Important 交接确认转 Task 11：recon 侦察线索（0.5/0.6+needs_verification）经豁免流入 _all_findings，违背 :617"高风险区不作 finding"既有裁决——Task 11 必须承接 recon 来源候选处置（排除或仅作上下文）**；2 Minor 记账：缺省 needs_verification=True 的放宽边界、丢弃日志文案 <0.7 未随阈值改）
- Files: `backend/app/services/agent/agents/analysis.py`（:29-260 系统提示词）、`backend/app/services/agent/prompts/system_prompts.py`（:402、:89）
- Interfaces: Final Answer findings 支持 `needs_verification`；提示词分级产出策略
- TDD: 失败测试（mock LLM 返回 confidence=0.4 + needs_verification=true 的 finding → 通过归一化不被 0.7 阈值丢弃且 is_strict_finding 放行；confidence=0.05 → 仍丢弃）→ 改提示词与 `_normalize_finding`/strict_finding 豁免逻辑 → 通过 → commit

### Task 10: 强制总结维度级下限
- [x] **Task 10 完成**（实施 fbf884b + 修复 9717963，review 初审 NEEDS_FIX（Important：重试异常吞首轮 violated 信号）→ 重审 CLEAN（四路径推演 + 变异独立复核）；豁免解析三级容错（词边界防 auth 误归）、tuple 化全调用方适配、主循环路径裁决合理（violated 恒 False）；flaky 三证确认（test_allows_public_hostname 公网 DNS 依赖，10 连过 + 代码实证）；4 Minor 记账转账本）
- Files: `backend/app/services/agent/agents/analysis.py`（:419-467）
- Interfaces: data 新增 `dimension_gaps_reported`、`output_floor_violated`
- TDD: 失败测试（0 候选 0 豁免 → violated=true 且有一次重试提示；有豁免 → violated=false）→ 实现 → 通过 → commit

### Task 11: orchestrator 产出下限门禁与 Semgrep 兜底
- [x] **Task 11 完成**（实施 3e4365d + 修复 e138424，review 初审 FAIL（3 Important：I1 violated 粘滞信号矛盾文案实证/I2 轮次耗尽缺兜底收口/I3 recon 落库无标注以高危漏洞身份呈现）→ 重审 CLEAN（三修复核验 + 双变异自证 + 回归 1073/4 一致）；I3 裁决：recon 线索不落库（排除验证队列+报告呈现双重达成）；**残留注释类 Minor 并入 Task 12 顺手修正**（:639/:1984 两处注释依据不实、M4 门禁计数含 recon、M8 finish 兜底缺集成测试、M9 陈旧注释）；Task 12 边界交接：候选口径统一、轮次耗尽第三路径 observation）
- Files: `backend/app/services/agent/agents/orchestrator.py`（max_dispatch 自动放行 :2091-2107、主循环收尾）、`backend/app/services/agent/agents/orchestrator.py`（归一化豁免联动 Task 9）
- Interfaces: 兜底候选 `{source:"semgrep_fallback", confidence:0.5, needs_verification:true}`；observations 记 `{gate:"output_floor"}`
- TDD: 失败测试（0 findings + 假 semgrep_findings 3 条 → 落库候选带标记进验证队列；output_floor_violated → 收口记 observations）→ 实现 → 通过 → commit

### Task 12: 门禁候选口径统一
- [x] **Task 12 完成**（commit f1b46cc，review CLEAN——spec 三条款逐条核验（候选纳入门禁/findings_to_verify 四入口/仅 3 候选派发 3 个）、helper 合并到 strict_finding 三副本收口（逐行等价 + is 身份断言防漂移）、**快照滞留隐患发现并修复**（R4 merge 以新 dict 替换索引位置，全量门禁改现取值）、CONCERN 控制流核实（finish 段快照消费全在 await 前成立）、Task 11 交接五项落地；2 Minor 记账：非门禁提示计数口径（:1970 用户可见）、verification.py 导入行超长格式化）
- Files: `backend/app/services/agent/agents/orchestrator.py`（has_findings :1133-1145、UNVERIFIED_TERMINAL :1262-1267）、`backend/app/services/agent/agents/verification.py`（findings_to_verify :832-837）
- Interfaces: `needs_verification=true` 候选纳入门禁与验证队列口径
- TDD: 失败测试（仅 3 候选 → verification 派发且门禁按候选计算）→ 实现 → 通过 → commit

## Phase 4: audit_trace 闭环

### Task 13: trace 持久化与路径配置
- [x] **Task 13 完成**（实施 df8f6c6 + 闭合 1a96942，review CLEAN——spec 三项亲验（env 链路/默认不变/四份 compose YAML 独立解析）、计划外闭合变异实证（orchestrator 显式传参屏蔽新 env 的缺口，AgentConfig env 前缀 AGENT_ 死配置实锤）；3 Minor 记账：AgentConfig.audit_trace_dir 死字段（后续清理）、**v6.4.1 镜像不含新 env 字段——挂载持久化现网已生效但 env 覆盖能力要等 Phase 6 重建**（时序知悉）、部署机容器未 recreate（Phase 6 统一））
- Files: `docker-compose.yml`（backend volumes）、`backend/app/services/agent/audit_trace.py`（:50-52 路径 env 化）
- Interfaces: env `AUDIT_TRACE_DIR` 覆盖默认 `./audit_traces`
- TDD: 失败测试（env 设置后目录切换）→ compose 修改（本地验证 volume 生效）→ commit

### Task 14: 写点补全（工具/LLM/验证）
- Files: `backend/app/services/agent/agents/base.py`（execute_tool 收尾、stream_llm_call done 后）、`backend/app/services/agent/agents/verification.py`（验证收尾）
- Interfaces: 调用 `add_tool_call`/`add_llm_call`/`add_verification_result`，全部 try/except 非致命
- TDD: 失败测试（mock 执行一轮 → trace md 工具/Token 栏目非 0）→ 实现 → 通过 → commit
- [x] **Task 14 完成**（commit 397086d + 守卫测试 2c714ff，review 初审 NEEDS_FIX（Important：验证收尾挂点零测试守卫——删挂点行 11 测试全绿）→ 重审 CLEAN（三守卫测试独立变异全红，I1/M1/M2 全闭合）；**R25 违规记录：实施者越权勾选本行（08e22e6），主控裁决勾选内容经审查属实确认有效，下不为例**；Minor 记账：M3 add_verification_result 不入 entries/json——**Task 15 读侧注意**、M4 purpose 标签、M5 双重截断无害；访问路径选方案 b 显式注入（多任务隔离）；全量 1101 passed/4 failed）

### Task 15: 读侧接线与 API 字段
- [x] **Task 15 完成**（实施 3a9e92c + 修复 448b61d，review 初审 NEEDS_FIX（2 Important：I1 摘要缺"关键门禁裁决"——spec SHALL 且无弃权记录；I2 子 Agent 消费侧零守卫——删注入块 13 测试全绿）→ 重审 CLEAN（I1 gate 段一处修复两处生效 + I2 三守卫隔离性成立 + 技术方案合理）；注入频率裁决认可（替换式至多一条最新，消除历史内自相矛盾）；M3 确认：验证结论不入摘要（audit_trace_path 为人工复盘入口）；4 Minor 记账：M4 gate 段截断优先级（实测不成立）、M1 API 守卫、M2 压缩转述残留、M3 base 同名假设）
- Files: `backend/app/services/agent/agents/orchestrator.py`（主循环开头注入）、`backend/app/api/v1/endpoints/agent_tasks.py`（AgentTaskResponse + audit_trace_path）
- Interfaces: 子 agent 经 `sub_input["trace_summary"]`；响应新字段
- TDD: 失败测试（有 trace → 对话历史含摘要段；trace 异常 → warning 跳过不中断；API 含路径）→ 实现 → 通过 → commit

## Phase 5: 前端

### Task 16: 创建表单预算入口
- [x] **Task 16 完成**（实施 e1b84cb + 文案修复 ac11644，review 初审 NEEDS_FIX（Important：默认值文案与后端事实不符——"默认 7200"是 spec 笔误，实际回退链默认 1800s）→ 重审 CLEAN（7 处文案逐一对齐 + 契约 53/53 + 零逻辑变更）；R21 spec 笔误纠正已由主控修正 tasks.md:121；留空语义裁决：不传字段走全局配置（Task 1 NULL 语义保留）；Task 8 M2 承接（skip_reason 前端展示）随本任务交付；2 Minor 记账：audit 端校验时序（finally 复位无害）、台账 M2 归属措辞统一
- Files: `frontend/src/components/agent/CreateAgentTaskDialog.tsx`、`frontend/src/components/audit/CreateTaskDialog.tsx`、`frontend/src/shared/api/agentTasks.ts`
- Interfaces: 提交体新增 `timeout_seconds`（分钟输入×60；留空不传走全局 agentTimeout 配置，默认 1800s——Task 1 的 NULL 语义）、`max_iterations`（留空后端默认 50）
- TDD: 组件测试（渲染两输入项、提交体含字段、范围校验）→ 实现 → 通过 → commit

### Task 17: 详情页预算/剩余时间与三标记
- [x] **Task 17 完成**（三标记半 f8cf910 review CLEAN + StatsPanel 半 39ecf3f review CLEAN；**后端最小增量偏离主控裁决：接受**（AgentTaskResponse 本无 timeout_seconds，前端剩余时间无数据源，19 行复用 resolve_task_timeout_seconds 回退链——ledger 落账）；StatsPanel 时间格（运行态剩余 1s tick/终态耗时/超时红字）；三标记+infra_error 徽章（伪造红/静态蓝/崩溃琥珀/infra 灰）；2 Minor 记账：三端点 timeout_seconds 语义不一（detail 有效值/list+create 显式值，后续统一）、测试路径笔误）
- Files: `frontend/src/pages/AgentAudit/components/StatsPanel.tsx`、`frontend/src/pages/AgentAudit/components/FindingSandboxEvidence.tsx`、`frontend/src/shared/api/agentTasks.ts`（类型）
- Interfaces: StatsPanel 时间格（运行中=剩余、完成=耗时）；attempt 三徽章（fabricated/static_evidence/poc_error）
- TDD: 组件测试 → 实现 → 通过 → commit

## Phase 6.5: Task 19 期间 follow-up 修复（2026-09-06/07 生产实证吸收）

### F1: Semgrep 兜底过滤可验证类型（Task 11 follow-up）
- [x] **F1 完成**（04d4189 + 二次分流修复 1d190dc，review 初审 NEEDS_FIX（Important：injection 别名一刀切误标命令注入候选走错 SQL 模板致 NO_SINK 假阴）→ 重审 CLEAN（9 组边界实测 + 模板分派独立验证）；VERIFIABLE_SEMGREP_TYPES 十类过滤（other/配置类/低严重度不送沙箱防预算拖垮）+ severity≥medium；生产实证：v4 任务 40 配置类 → v6.4.6 重验只剩代码类）

### A1: Semgrep 兜底 EL/SSTI 非可验证类型排除（F1 follow-up）
- [x] **A1 完成**（commit 94e8b4f，review CLEAN——EL/SSTI 非可验证类型排除（unverifiable 拦截在 canonicalize 之前），词边界 11 反例实证、拦截时序、三档 observation 分档、F1 并存零改动；生产根因（ELProcessor EL 类落 sql_injection 白跑）拦截链闭合；3 Minor 记账：expression 泛词 ReDoS 误归口径（无功能损失，backlog）、融合词缝隙（理论）、测试冗余）

### F2: watchdog 收口失效（事件循环同步阻塞冻结——独立修复待审）
- [x] **F2 完成**（三 commit 6c7fc6a/576254d/c491a95，review CLEAN——根因代码行亲验（litellm streaming_handler sync next 分支）+ 线程桥绕行根治（事件循环线程零 LLM 网络读、150s 兜底链逐环确认、取消语义协作 Event+daemon 线程）+ watchdog 补丁（同时 cancel/shield/3s 二次时限/栈落盘/语义边界三判别）+ per-chunk shield 防堵（Py3.12 吞取消实锤堵漏/_safe_aclose/_hard_interrupt 链贯通）；20 例新测试亲跑全过、全量 1218 passed/4 failed、A1 并行零冲突；4 Minor 记账：冗余 except/双 close 注释/测试数对账/放弃等待兜底）+ watchdog 强杀补丁（mark_deadline_hit 同时 cancel/shield/3s 二次时限/never-retrieved 防护）+ per-chunk 取消坑防堵（ensure_future+shield+aclose+_hard_interrupt）；回归 1181 passed/4 failed）

## Phase 6: 台账治理与端到端

### Task 18: OpenSpec 台账治理
- [x] **Task 18 完成**（commit 05fd5a1，review CLEAN——归档 fix-verification-evidence-root（18/18 复核五项全过 + 主 spec sync +7 无操作头残留）、核实 fix-sandbox-evidence-and-recovery 27/33（抽查 8 项 file:line 坐实 + 6 项弃权理由真实：T15/T20/T22 测试缺口、T31-33 E2E 无 harness）、**structured-output-protocol 9/9 归档候选经确认另行归档**；零代码改动）

### Task 18: OpenSpec 台账治理
- Files: `openspec/changes/fix-sandbox-evidence-and-recovery/tasks.md`（核实补勾）、归档 `fix-verification-evidence-root`
- Interfaces: openspec status/archive 命令
- 验收：status 与实际实施一致；archive 复核四项+Purpose 全过（R18/R24）

### Task 19: 端到端对照验证
- Files: 无代码（验证任务）
- 验收：同一项目跑新代码任务——findings>0（含候选）、每 finding 终态可解释（无 infra 伪装）、报告含未验证清单、trace 文件宿主机可读、前端三处可见；结果记入 observations 并汇报老板
