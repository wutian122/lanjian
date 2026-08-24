"""
Verification Agent (漏洞验证层) - LLM 驱动版

LLM 是验证的大脑！
- LLM 决定如何验证每个漏洞
- LLM 构造验证策略
- LLM 分析验证结果
- LLM 判断是否为真实漏洞

类型: ReAct (真正的!)
"""

import asyncio
import json
import logging
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

from .base import BaseAgent, AgentConfig, AgentResult, AgentType, AgentPattern, TaskHandoff
from ..json_parser import AgentJsonParser
from ..prompts import CORE_SECURITY_PRINCIPLES, VULNERABILITY_PRIORITIES, build_enhanced_prompt
from app.models.agent_task import VerificationStatus
from app.services.agent.strict_finding import is_strict_finding

logger = logging.getLogger(__name__)

SANDBOX_FAILURE_MARKERS = ("工具执行失败", "Traceback", "Error:", "Exception:", "\n失败:", "\n错误:")

# V6 B2（REQ-VE-2）：子串级失败标记仅在特定条件下生效，与工具级/行级标记区分
_SUB_FAILURE_MARKERS = ("Traceback", "Error:", "Exception:")


def _has_sandbox_failure_marker(observation: str, exit_code) -> bool:
    """V6 B2（REQ-VE-2）失败标记收窄判定。

    - "工具执行失败" 是工具级失败前缀，保留全文匹配；
    - "\\n失败:"/"\\n错误:" 是行级失败标记，保留全文匹配；
    - "Traceback"/"Error:"/"Exception:" 是子串级标记，仅在 exit_code!=0
      或位于 stderr/错误段（"标准错误:"```...``` 与 "错误:" 行）内时生效，
      防止 exit 0 且含铁证标记的成功输出被正文里 incidental 的 "Error:"
      子串误杀（生产任务 5a1f7ab6：21 次执行 0 证据的直接原因之一）。
    """
    obs = observation or ""
    if "工具执行失败" in obs or "\n失败:" in obs or "\n错误:" in obs:
        return True
    stderr_section = ""
    m = re.search(r"标准错误:\s*```[^\n]*\n(.*?)```", obs, re.DOTALL)
    if m:
        stderr_section = m.group(1)
    err_line = re.search(r"^错误:.*$", obs, re.MULTILINE)
    if err_line:
        stderr_section += "\n" + err_line.group(0)
    if any(mk in stderr_section for mk in _SUB_FAILURE_MARKERS):
        return True
    exit_bad = exit_code is not None and exit_code != 0
    return exit_bad and any(mk in obs for mk in _SUB_FAILURE_MARKERS)

# R3 反伪造：源码缺失/模拟输出却声称确认 → 该 attempt 不得作为有效证据
# LLM 在沙箱读不到源码或偷懒时会输出 "Simulated ..." + 假确认标记
FABRICATION_MARKERS = (
    "simulated", "simulation", "模拟", "模拟执行",
    "source file not found", "source not found", "文件未找到", "文件不存在",
)

# B3 严标准：真正的漏洞触发证据标记（confirmed 档要求含其中之一）
# 仅 "退出码:0"/"Verification Complete" 等"PoC 跑完"标记不算真证据
# 已去掉 "static_confirmed"（状态名，不应作为 sandbox 证据）和 "vulnerable"（太宽，"not vulnerable" 也匹配）
VULN_EVIDENCE_MARKERS = (
    "VULNERABILITY_CONFIRMED", "vulnerability confirmed",
    "exploit successful", "injection successful",
    "payload executed", "command executed", "code execution",
    "file created", "file read successfully", "data exfiltrated",
    "bypass successful", "authentication bypassed",
)

LANGUAGE_TEST_TOOL_NAMES = {"python_test", "php_test", "javascript_test", "java_test", "go_test", "ruby_test", "shell_test", "bash_test"}


# R1 确定性验证状态引擎：由运行时沙箱证据推导验证结论，不信任 LLM 自述 verdict
# 返回 (verification_status, is_verified, notes)
# 证据优先级：confirmed(动态铁证) > static_confirmed(代码推理) > not_reproducible(尝试未复现)
# false_positive 仅由 LLM 显式标注 + 无 confirmed 证据时生效
def compute_verification_status(
    finding: dict[str, Any],
    attempts: list[Any],
    attempt_has_vuln_evidence_fn=None,
    attempt_matches_finding_fn=None,
) -> tuple[str, bool, dict]:
    """由 sandbox_attempts 确定性推导验证状态。

    - attempts: finding 的 sandbox_attempts（已由 _attach_runtime_sandbox_attempts 绑定）
    - attempt_has_vuln_evidence_fn / attempt_matches_finding_fn: 可注入的判定函数
      （默认用模块级轻量实现，便于单测；运行时由 Verification 实例注入复用 B3 严标准）
    """
    # 过滤伪造/不可信证据
    real_attempts = [
        a for a in attempts if isinstance(a, dict) and not a.get("fabricated")
    ]
    evidence_has_vuln = (
        attempt_has_vuln_evidence_fn or _attempt_has_vuln_evidence_default
    )
    evidence_matches = (
        attempt_matches_finding_fn or _attempt_matches_finding_default
    )

    # 1) confirmed：成功执行 + 漏洞触发证据 + 匹配 finding
    for a in real_attempts:
        if a.get("success") is True and a.get("exit_code") == 0:
            if evidence_has_vuln(a) and evidence_matches(a, finding):
                return VerificationStatus.CONFIRMED, True, {}

    # 2) static_confirmed：成功执行但无动态铁证（weak_evidence 宽松兜底）
    for a in real_attempts:
        if a.get("success") is True and (a.get("weak_evidence") or a.get("static_evidence")):
            return VerificationStatus.STATIC_CONFIRMED, True, {}

    # 3) false_positive：LLM 显式标注且无 confirmed 证据
    llm_verdict = str(finding.get("verdict") or finding.get("verification_status") or "").lower()
    if llm_verdict == VerificationStatus.FALSE_POSITIVE:
        return VerificationStatus.FALSE_POSITIVE, False, {}

    # 3.5) REQ-VE-2：验证器（PoC）自身崩溃与"未复现"分档。
    # 全部 attempt 均为 poc_error（Traceback/SyntaxError/re.error 等）→ needs_context
    # 并注明验证器崩溃，不得冒充 not_reproducible（"没复现"语义只留给 PoC 正常执行）。
    if real_attempts and all(a.get("poc_error") for a in real_attempts):
        return VerificationStatus.NEEDS_CONTEXT, False, {
            "reason": "pre-generated PoC crashed",
            "poc_error": True,
        }

    # 4) not_reproducible：尝试过但未复现
    if real_attempts:
        return VerificationStatus.NOT_REPRODUCIBLE, False, {
            "reason": "sandbox attempts executed but no confirmation evidence"
        }

    # 5) needs_context：无证据（LLM 可带 sandbox_skip_reason 说明原因）
    notes = {}
    skip_reason = finding.get("sandbox_skip_reason")
    if skip_reason:
        notes["sandbox_skip_reason"] = str(skip_reason)[:500]
    return VerificationStatus.NEEDS_CONTEXT, False, notes


def _attempt_has_vuln_evidence_default(attempt: dict[str, Any]) -> bool:
    """模块级默认证据判定（与实例方法 _attempt_has_vuln_evidence 同标准）。"""
    evidence_summary = str(attempt.get("evidence_summary") or "")
    ev_lower = evidence_summary.lower()
    if "vulnerability_confirmed(static)" in ev_lower:
        return False
    if any(marker.lower() in ev_lower for marker in FABRICATION_MARKERS):
        return False
    return any(m.lower() in ev_lower for m in VULN_EVIDENCE_MARKERS)


def _attempt_matches_finding_default(attempt: dict[str, Any], finding: dict[str, Any]) -> bool:
    """模块级默认匹配（宽松：success + exit_code 0 即可，精确匹配由实例注入）。"""
    if attempt.get("success") is not True:
        return False
    if attempt.get("exit_code") != 0:
        return False
    return True



VERIFICATION_SYSTEM_PROMPT = """你是蓝鉴的漏洞验证 Agent，一个**自主**的安全验证专家。

## 你的角色
你是漏洞验证的**大脑**，不是机械验证器。你需要：
1. 理解每个漏洞的上下文
2. 设计合适的验证策略
3. **使用沙箱环境进行动态验证**
4. 判断漏洞是否真实存在
5. 评估实际影响并生成 PoC

## 🔴 强制规则：必须使用沙箱执行
**每个漏洞都必须通过 sandbox_exec 在 Docker 沙箱中执行验证。** 这是不可跳过的步骤。
你不应该仅通过代码阅读或 LLM 分析来判断漏洞——必须在隔离沙箱中实际运行测试代码。

## 🔴 反伪造规则（强制，违者判定为验证失败）
1. **禁止模拟/编造 PoC 输出**。不得输出 "Simulated ..."、伪造的确认标记或虚构的沙箱结果。
2. 若沙箱中**无法读取到目标源码**（如 Source file not found），必须在 Final Answer 中为该 finding 标注 `sandbox_skip_reason` 如实说明原因，**不得**声称漏洞已确认。
3. 所有 `VULNERABILITY_CONFIRMED` 结论必须来自真实沙箱命令的 stdout 输出。
4. 系统会独立执行预生成的 PoC 并据其实证据判定状态；你的自述 verdict 不作为最终依据。

## 你可以使用的工具

### 🔥 沙箱验证工具（优先使用，必须使用！）
- **sandbox_exec**: 在 Docker 沙箱中执行命令/PoC 脚本
  - 用于所有漏洞的动态验证
  - 沙箱已配置多语言运行时（Python/Node/Java/Go/Ruby/PHP）
  - 参数: command (str), language (str), timeout (int), files (dict)

- **sandbox_http**: 在沙箱中发送 HTTP 请求（用于 SSRF/XSS 验证）

- **sandbox_browser**: 在沙箱中用无头浏览器(chromium)验证需浏览器渲染的漏洞
  - **XSS（反射型/DOM型）**: `sandbox_browser(action="eval", url="http://target/?payload=<script>alert(1)</script>", script="document.body.innerHTML")`，检查 payload 是否原样进入 DOM
  - **开放重定向**: `sandbox_browser(action="navigate", url="http://target/redirect?url=//evil.com")`，检查返回的最终 URL 是否跳转到外部
  - **SSRF 可视化**: `sandbox_browser(action="navigate", url="http://target/fetch?url=http://169.254.169.254/")`，检查页面内容
  - 参数: action(navigate/screenshot/eval/click/get_text), url, selector, script, timeout
  - **优先用于 XSS/重定向类**，沙箱已装 chromium+playwright

### 🔥 辅助工具
- **extract_function**: 从源文件提取指定函数代码
  - 用于获取目标函数，构建测试代码
  - 参数: file_path (str), function_name (str), include_imports (bool)

- **read_file**: 读取代码文件获取上下文
  参数: file_path (str), start_line (int), end_line (int)

### 备用工具（仅当沙箱不可用时使用）
- **run_code**: 在当前环境执行代码（不如沙箱安全，仅做备用）

## 🔥 Sandbox 沙箱验证指南

### 对于网络相关漏洞（SSRF、URL重定向等）
1. 使用 `extract_function` 获取目标代码
2. 编写 PoC 攻击脚本
3. **使用 `sandbox_exec` 并设置 `network_enabled: true`**
   - 示例: sandbox_exec(command="curl -v http://target", network_enabled=true)
   - 沙箱将临时启用 bridge 网络模式
4. 分析沙箱输出，确认漏洞是否触发

### 对于可执行的漏洞（命令注入、代码注入等）
1. 使用 `extract_function` 获取目标代码
2. 编写 PoC 攻击脚本（Python/Shell/PHP 等）
3. **使用 `sandbox_exec` 在 Docker 沙箱中执行 PoC**
4. 分析沙箱输出，确认漏洞是否触发

### 对于数据泄露型漏洞（SQL注入、路径遍历等）
1. 获取目标代码和上下文
2. 编写针对性的测试脚本
3. **使用 `sandbox_exec` 在沙箱中执行测试**
4. 检查是否能构造恶意查询/路径

### 对于配置类漏洞（硬编码密钥等）
1. 使用 `read_file` 读取配置文件
2. 验证敏感信息是否存在
3. **使用 `sandbox_exec` 验证密钥是否有效**（如尝试 API 调用）

### 沙箱执行示例
```
# 命令注入验证
sandbox_exec(command="python3 -c \"import os; os.system('echo test; id')\"", language="python")

# 路径遍历验证  
sandbox_exec(command="python3 poc.py", language="python", files={"poc.py": "..."})

# SSRF 网络验证
sandbox_exec(command="curl -v http://169.254.169.254/latest/meta-data", network_enabled=true)
```

## 🔴 强制操作顺序（必须遵守）
对于每个漏洞发现，请严格按照以下顺序操作：

**第一步：读取目标文件（使用 read_file）**
```
Thought: 我需要先读取目标文件确认漏洞代码存在
Action: read_file
Action Input: {"file_path": "目标文件路径"}
```

**第二步：立即使用 sandbox_exec 验证（不要跳过）**
```
Thought: 现在我必须使用 sandbox_exec 在沙箱中验证这个漏洞
Action: sandbox_exec
Action Input: {"command": "python3 -c '你的验证代码'", "timeout": 30}
```

**第三步：分析沙箱输出并判定**
```
Thought: 根据沙箱输出判断漏洞是否存在
Action: sandbox_exec
Action Input: {"command": "进一步验证命令", "timeout": 30}
```

**第四步：输出 Final Answer（只有完成至少一次 sandbox_exec 后才允许）**

⚠️ **警告**: 如果你跳过第二步直接输出 Final Answer，系统会拒绝并强制要求你使用 sandbox_exec。

## 工作流程
对于每个漏洞发现：

```
Thought: [分析漏洞类型，设计沙箱验证策略]
Action: sandbox_exec
Action Input: {命令和测试代码}
```

验证完所有发现后：
```
Thought: [总结沙箱验证结果]
Final Answer: [JSON 格式的验证报告，包含每个漏洞的沙箱执行结果]
```

### SQL 注入 Fuzzing Harness 示例 (Python)
```python
# === Mock 数据库 ===
class MockCursor:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        print(f"[SQL] Query: {query}")
        print(f"[SQL] Params: {params}")
        self.queries.append((query, params))

        # 检测 SQL 注入特征
        if params is None and ("'" in query or "OR" in query.upper() or "--" in query):
            print("[VULN] Possible SQL injection - no parameterized query!")

class MockDB:
    def cursor(self):
        return MockCursor()

# === 目标函数 ===
def get_user(db, user_id):
    cursor = db.cursor()
    cursor.execute(f"SELECT * FROM users WHERE id = '{user_id}'")  # 漏洞！

# === Fuzzing ===
db = MockDB()
payloads = ["1", "1'", "1' OR '1'='1", "1'; DROP TABLE users--", "1 UNION SELECT * FROM admin"]

for p in payloads:
    print(f"\\n=== Testing: {p} ===")
    get_user(db, p)
```

### PHP 命令注入 Fuzzing Harness 示例
```php
// 注意：php -r 不需要 <?php 标签

// Mock $_GET
$_GET['cmd'] = '; id';
$_POST['cmd'] = '; id';
$_REQUEST['cmd'] = '; id';

// 目标代码（从项目复制）
$output = shell_exec($_GET['cmd']);
echo "Output: " . $output;

// 如果有输出，说明命令被执行
if ($output) {
    echo "\\n[VULN] Command executed!";
}
```

### XSS 检测 Harness 示例 (Python)
```python
def vulnerable_render(user_input):
    # 模拟模板渲染
    return f"<div>Hello, {user_input}!</div>"

payloads = [
    "test",
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "{{7*7}}",  # SSTI
]

for p in payloads:
    output = vulnerable_render(p)
    print(f"Input: {p}")
    print(f"Output: {output}")
    # 检测：payload 是否原样出现在输出中
    if p in output and ("<" in p or "{{" in p):
        print("[VULN] XSS - input not escaped!")
```

## Sandbox 沙箱验证指南（必须遵循！）

### 对于可执行的漏洞（命令注入、代码注入等）
1. 使用 `extract_function` 获取目标代码
2. 编写 PoC 攻击脚本
3. **使用 `sandbox_exec` 在 Docker 沙箱中执行**
4. 分析沙箱输出，确认漏洞触发

### 对于数据泄露型漏洞（SQL注入、路径遍历等）
1. 获取目标代码
2. **使用 `sandbox_exec` 在沙箱中执行测试**
3. 检查是否能构造恶意查询/路径

### 对于浏览器渲染类漏洞（XSS/开放重定向/SSRF）— 优先用 sandbox_browser
1. 构造含 payload 的 URL
2. **使用 `sandbox_browser(action="navigate"或"eval", url=..., script=...)`** 在无头浏览器中真实渲染
3. 检查 payload 是否进入 DOM / 是否发生重定向 / 页面是否含敏感内容
4. 若沙箱无目标服务运行，可用 `sandbox_browser(action="eval", url="about:blank", script="<渲染函数代码>")` 模拟渲染逻辑

### 对于配置类漏洞（硬编码密钥等）
1. 使用 `read_file` 读取配置文件
2. 验证敏感信息是否存在
3. **使用 `sandbox_exec` 验证密钥有效性**

## 工作流程
你将收到一批待验证的漏洞发现。对于每个发现：

```
Thought: [分析漏洞类型，设计验证策略]
Action: [工具名称]
Action Input: [参数]
```

验证完所有发现后，输出：

```
Thought: [总结验证结果]
Final Answer: [JSON 格式的验证报告]
```

## ⚠️ 输出格式要求（严格遵守）

**禁止使用 Markdown 格式标记！** 你的输出必须是纯文本格式：

✅ 正确格式：
```
Thought: 我需要读取 search.php 文件来验证 SQL 注入漏洞。
Action: read_file
Action Input: {"file_path": "search.php"}
```

❌ 错误格式（禁止使用）：
```
**Thought:** 我需要读取文件
**Action:** read_file
**Action Input:** {"file_path": "search.php"}
```

规则：
1. 不要在 Thought:、Action:、Action Input:、Final Answer: 前后添加 `**`
2. 不要使用其他 Markdown 格式（如 `###`、`*斜体*` 等）
3. Action Input 必须是完整的 JSON 对象，不能为空或截断

## Final Answer 格式
**每个 finding 必须包含 `file_path` 和 `line_start`（与输入中的发现完全一致），否则验证结果将被丢弃。**
```json
{
    "findings": [
        {
            "file_path": "与输入完全一致的文件路径",
            "line_start": 行号,
            ...原始发现字段...,
            "verdict": "confirmed/false_positive/not_reproducible/needs_context",
            "confidence": 0.0-1.0,
            "is_verified": true/false,
            "verification_method": "描述验证方法",
            "verification_details": "验证过程和结果详情",
            "poc": {
                "description": "PoC 描述",
                "steps": ["步骤1", "步骤2"],
                "payload": "完整可执行的 PoC 代码或命令",
                "harness_code": "Fuzzing Harness 代码（如果使用）"
            },
            "impact": "实际影响分析",
            "recommendation": "修复建议"
        }
    ],
    "summary": {
        "total": 数量,
        "confirmed": 数量,
        "likely": 数量,
        "false_positive": 数量
    }
}
```

## 🔴 强制规则：只验证输入的发现
**你必须只返回与输入发现数量完全一致的 findings。**
- 输入有 N 个发现，你的 Final Answer 必须恰好包含 N 个 findings
- 每个finding的 file_path 和 line_start 必须与输入中对应的发现**完全一致**（逐字匹配）
- **绝对不要**发明新的发现、猜测额外的漏洞、或添加输入中没有的发现
- 如果你认为发现了额外的漏洞，请在 verification_details 中说明，但不要作为单独的finding返回
- 违反此规则的 Final Answer 将被系统丢弃

## 验证判定标准
- **confirmed**: 漏洞确认存在且可利用，有明确证据（如 Harness 成功触发）
- **likely**: 高度可能存在漏洞，代码分析明确但无法动态验证
- **uncertain**: 需要更多信息才能判断
- **false_positive**: 确认是误报，有明确理由

### 🔥 verdict 判定示例（必须遵循，避免误标 not_reproducible）
- **SSTI/模板注入**: sandbox_exec 运行 Jinja2，`{{7*7}}` 渲染为 49 / `{{7*'7'}}` 渲染为 7777777 → **confirmed**（已成功复现模板注入）
- **XSS**: payload（如 `<script>alert(1)</script>`）原样出现在渲染输出或 DOM 中 → **confirmed**
- **命令注入**: sandbox_exec 执行含用户输入的命令，观察到命令执行结果（如 echo 输出、文件创建）→ **confirmed**
- **JWT/认证绕过**: sandbox_exec 验证 jwt.decode(options={'verify_signature': False}) 能解码伪造 token → **confirmed**
- **not_reproducible**: 仅当沙箱无法验证（如需运行中的外部服务/真实 Git 服务）且代码分析也无法确认可利用性时
- **needs_context**: 仅当信息不足无法判断（如找不到文件、代码不完整、关键参数未知）
- **false_positive**: 确认是误报（如文件不存在、代码与描述不符、测试文件误报）

⚠️ **不要把已成功复现的漏洞标为 not_reproducible**。只要沙箱 PoC 触发了漏洞行为（注入成功、payload 进入输出、命令执行），就判 confirmed。

## 🚨 强制证据标记（关键！系统据此自动判定，缺标记会被误判 not_reproducible）

**PoC 成功复现漏洞时，脚本必须 print 一行 `VULNERABILITY_CONFIRMED: <简述>`**。系统通过该标记自动识别动态复现成功。
- ✅ 正确：`print('VULNERABILITY_CONFIRMED: 路径遍历成功读取 root 外文件 TOP SECRET DATA')`
- ✅ 正确：`print('VULNERABILITY_CONFIRMED: IDOR 越权读取他人资源 sk-user-B')`
- ❌ 错误：只 print 自定义文字如 `TOP SECRET DATA` / `越权成功`（系统不认，会被误判 not_reproducible）
- ❌ 错误：只在 verification_details 文字里说"成功复现"但 PoC 输出没标记

**优先使用系统注入的 sandbox_exec 模板命令**（每个漏洞已附带含 VULNERABILITY_CONFIRMED 标记的 PoC），仅在模板不适用时自写 PoC，但自写 PoC 成功时也必须 print `VULNERABILITY_CONFIRMED: <简述>`。

## Final Answer 必须填充 sandbox_attempts 字段
每个 finding 除 verdict/verification_details 外，必须填 `sandbox_attempts` 数组（系统据此做证据匹配，空数组会导致复现成功的漏洞被误判 not_reproducible）：
```json
"sandbox_attempts": [
    {
        "success": true,
        "exit_code": 0,
        "command": "python3 -c \"print('VULNERABILITY_CONFIRMED: ...')\"",
        "evidence_summary": "VULNERABILITY_CONFIRMED: <PoC 输出的证据摘要>",
        "target_ref": "文件路径:行号"
    }
]
```

## 🚨 防止幻觉验证（关键！）

**Analysis Agent 可能报告不存在的文件！** 你必须验证：

1. **文件必须存在** - 使用 read_file 读取发现中指定的文件
   - 如果 read_file 返回"文件不存在"，该发现是 **false_positive**
   - 不要尝试"猜测"正确的文件路径

2. **代码必须匹配** - 发现中的 code_snippet 必须在文件中真实存在
   - 如果文件内容与描述不符，该发现是 **false_positive**

3. **不要"填补"缺失信息** - 如果发现缺少关键信息（如文件路径为空），标记为 uncertain

❌ 错误做法：
```
发现: "SQL注入在 api/database.py:45"
read_file 返回: "文件不存在"
判定: confirmed  <- 这是错误的！
```

✅ 正确做法：
```
发现: "SQL注入在 api/database.py:45"
read_file 返回: "文件不存在"
判定: false_positive，理由: "文件 api/database.py 不存在"
```

## ⚠️ 关键约束
1. **必须先调用工具验证** - 不允许仅凭已知信息直接判断
2. **优先使用 run_code** - 编写 Harness 进行动态验证
3. **PoC 必须完整可执行** - poc.payload 应该是可直接运行的代码
4. **不要假设环境** - 沙箱中没有运行的服务，需要 mock

## 重要原则
1. **你是验证的大脑** - 你决定如何测试，工具只提供执行能力
2. **动态验证优先** - 能运行代码验证的就不要仅靠静态分析
3. **质量优先** - 宁可漏报也不要误报太多
4. **证据支撑** - 每个判定都需要有依据

现在开始验证漏洞发现！"""


@dataclass
class VerificationStep:
    """验证步骤"""
    thought: str
    action: Optional[str] = None
    action_input: Optional[Dict] = None
    observation: Optional[str] = None
    is_final: bool = False
    final_answer: Optional[Dict] = None


def _normalize_tool_key(action: str, action_input: dict) -> str:
    """归一化工具调用 key，用于重复调用检测。

    sandbox_exec：对命令做空白归一化并截断，避免 LLM 微调输入
                  （空格/换行/变量名变化）绕过去重导致同一 PoC 被反复执行。
    其他工具：保持原 JSON 精确匹配（sort_keys 保证 key 顺序无关）。
    """
    if action == "sandbox_exec" and isinstance(action_input, dict):
        cmd = str(action_input.get("command") or "")
        # 归一化空白并截断到 500 字符（覆盖绝大多数 PoC，同时避免超长 payload key 膨胀）
        normalized_cmd = " ".join(cmd.split())[:500]
        return f"sandbox_exec:{normalized_cmd}"
    return f"{action}:{json.dumps(action_input or {}, sort_keys=True)}"


class VerificationAgent(BaseAgent):
    """
    漏洞验证 Agent - LLM 驱动版
    
    LLM 全程参与，自主决定：
    1. 如何验证每个漏洞
    2. 使用什么工具
    3. 判断真假
    """
    
    def __init__(
        self,
        llm_service,
        tools: Dict[str, Any],
        event_emitter=None,
        task_id: Optional[str] = None,
        llm_rate_per_minute: Optional[int] = None,
    ):
        # 组合增强的系统提示词
        # 🔥 v3.1: 使用 build_enhanced_prompt 注入防幻觉规则和文件验证
        full_system_prompt = build_enhanced_prompt(
            base_prompt=VERIFICATION_SYSTEM_PROMPT,
            include_principles=True,
            include_priorities=True,
            include_tools=False,        # Verification 主要使用 sandbox_exec
            include_validation=True,
            include_anti_hallucination=True,   # ✅ P0-1: 防幻觉规则
            include_coverage_matrix=False,     # Verification 不做覆盖率判断
            include_control_driven=False,
            include_contract=True,             # Agent 合约
        )

        config = AgentConfig(
            name="Verification",
            agent_type=AgentType.VERIFICATION,
            pattern=AgentPattern.REACT,
            max_iterations=100,
            system_prompt=full_system_prompt,
        )
        super().__init__(
            config,
            llm_service,
            tools,
            event_emitter,
            task_id=task_id,
            llm_rate_per_minute=llm_rate_per_minute,
        )
        
        self._conversation_history: List[Dict[str, str]] = []
        self._steps: List[VerificationStep] = []



    
    def _parse_llm_response(self, response: str) -> VerificationStep:
        """解析 LLM 响应 - 增强版，更健壮地提取思考内容"""
        step = VerificationStep(thought="")

        # 🔥 v2.1: 预处理 - 移除 Markdown 格式标记（LLM 有时会输出 **Action:** 而非 Action:）
        cleaned_response = response
        cleaned_response = re.sub(r'\*\*Action:\*\*', 'Action:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Action Input:\*\*', 'Action Input:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Thought:\*\*', 'Thought:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Final Answer:\*\*', 'Final Answer:', cleaned_response)
        cleaned_response = re.sub(r'\*\*Observation:\*\*', 'Observation:', cleaned_response)

        # 🔥 首先尝试提取明确的 Thought 标记
        thought_match = re.search(r'Thought:\s*(.*?)(?=Action:|Final Answer:|$)', cleaned_response, re.DOTALL)
        if thought_match:
            step.thought = thought_match.group(1).strip()

        # 🔥 检查是否是最终答案
        final_match = re.search(r'Final Answer:\s*(.*?)$', cleaned_response, re.DOTALL)
        if final_match:
            step.is_final = True
            answer_text = final_match.group(1).strip()
            answer_text = re.sub(r'```json\s*', '', answer_text)
            answer_text = re.sub(r'```\s*', '', answer_text)
            # 使用增强的 JSON 解析器
            step.final_answer = AgentJsonParser.parse(
                answer_text,
                default={"findings": [], "raw_answer": answer_text}
            )
            # 确保 findings 格式正确
            if "findings" in step.final_answer:
                step.final_answer["findings"] = [
                    f for f in step.final_answer["findings"]
                    if isinstance(f, dict)
                ]

            # 🔥 如果没有提取到 thought，使用 Final Answer 前的内容作为思考
            if not step.thought:
                before_final = cleaned_response[:cleaned_response.find('Final Answer:')].strip()
                if before_final:
                    before_final = re.sub(r'^Thought:\s*', '', before_final)
                    step.thought = before_final[:500] if len(before_final) > 500 else before_final

            return step

        # 🔥 提取 Action
        action_match = re.search(r'Action:\s*(\w+)', cleaned_response)
        if action_match:
            step.action = action_match.group(1).strip()

            # 🔥 如果没有提取到 thought，提取 Action 之前的内容作为思考
            if not step.thought:
                action_pos = cleaned_response.find('Action:')
                if action_pos > 0:
                    before_action = cleaned_response[:action_pos].strip()
                    before_action = re.sub(r'^Thought:\s*', '', before_action)
                    if before_action:
                        step.thought = before_action[:500] if len(before_action) > 500 else before_action

        # 🔥 提取 Action Input - 增强版，处理多种格式
        input_match = re.search(r'Action Input:\s*(.*?)(?=Thought:|Action:|Observation:|$)', cleaned_response, re.DOTALL)
        if input_match:
            input_text = input_match.group(1).strip()
            input_text = re.sub(r'```json\s*', '', input_text)
            input_text = re.sub(r'```\s*', '', input_text)

            # 🔥 v2.1: 如果 Action Input 为空或只有 **，记录警告
            if not input_text or input_text == '**' or input_text.strip() == '':
                logger.warning(f"[Verification] Action Input is empty or malformed: '{input_text}'")
                step.action_input = {}
            else:
                # 使用增强的 JSON 解析器
                step.action_input = AgentJsonParser.parse(
                    input_text,
                    default={"raw_input": input_text}
                )
        elif step.action:
            # 🔥 v2.1: 有 Action 但没有 Action Input，记录警告
            logger.warning(f"[Verification] Action '{step.action}' found but no Action Input")
            step.action_input = {}

        # 🔥 最后的 fallback：如果整个响应没有任何标记，整体作为思考
        if not step.thought and not step.action and not step.is_final:
            if response.strip():
                step.thought = response.strip()[:500]

        return step
    
    async def run(self, input_data: Dict[str, Any]) -> AgentResult:
        """
        执行漏洞验证 - LLM 全程参与！
        """
        import time
        self._cancelled = False
        start_time = time.time()
        
        previous_results = input_data.get("previous_results", {})
        config = input_data.get("config", {})
        task = input_data.get("task", "")
        task_context = input_data.get("task_context", "")

        # 提取跨轮传递上下文
        cross_round_context = previous_results.get("cross_round_context", "")

        # 🔥 处理交接信息
        handoff = input_data.get("handoff")
        if handoff:
            from .base import TaskHandoff
            if isinstance(handoff, dict):
                handoff = TaskHandoff.from_dict(handoff)
            self.receive_handoff(handoff)
        
        # 收集所有待验证的发现
        findings_to_verify = []

        # 🔥 优先从交接信息获取发现
        if self._incoming_handoff and self._incoming_handoff.key_findings:
            findings_to_verify = self._incoming_handoff.key_findings.copy()
            logger.info(f"[Verification] 从交接信息获取 {len(findings_to_verify)} 个发现")

        # 🔥 FIX: 无论 handoff 有没有数据，都必须检查 previous_results.findings
        # 因为 Orchestrator 的 _all_findings（通过 previous_results.findings 传递）可能包含比 handoff 更多的发现
        if isinstance(previous_results, dict) and "findings" in previous_results:
            direct_findings = previous_results.get("findings", [])
            before_count = len(findings_to_verify)
            if isinstance(direct_findings, list):
                for f in direct_findings:
                    if isinstance(f, dict):
                        severity = str(f.get("severity", "")).lower()
                        needs_verify = f.get("needs_verification", True)
                        if needs_verify or severity in ["critical", "high"]:
                            findings_to_verify.append(f)
            added = len(findings_to_verify) - before_count
            logger.info(f"[Verification] 从 previous_results.findings 补充了 {added} 个发现（共 {len(findings_to_verify)} 个）")

        # 格式2: 传统格式 {"phase_name": {"data": {"findings": [...]}}}
        if not findings_to_verify:
            for phase_name, result in previous_results.items():
                if phase_name == "findings":
                    continue

                if isinstance(result, dict):
                    data = result.get("data", {})
                else:
                    data = result.data if hasattr(result, 'data') else {}

                if isinstance(data, dict):
                    phase_findings = data.get("findings", [])
                    for f in phase_findings:
                        if isinstance(f, dict):
                            severity = str(f.get("severity", "")).lower()
                            needs_verify = f.get("needs_verification", True)
                            if needs_verify or severity in ["critical", "high"]:
                                findings_to_verify.append(f)

            if findings_to_verify:
                logger.info(f"[Verification] 从传统格式获取 {len(findings_to_verify)} 个发现")
        
        # 🔥 如果仍然没有发现，尝试从 input_data 的其他字段提取
        if not findings_to_verify:
            # 尝试从 task 或 task_context 中提取描述的漏洞
            if task and ("发现" in task or "漏洞" in task or "findings" in task.lower()):
                logger.warning(f"[Verification] 无法从结构化数据获取发现，任务描述: {task[:200]}")
                # 创建一个提示 LLM 从任务描述中理解漏洞的特殊处理
                await self.emit_event("warning", f"无法从结构化数据获取发现列表，将基于任务描述进行验证")
        
        # 🔥 从 handoff.key_findings 合并发现，确保 Recon/Analysis 的全部发现都被验证
        handoff_dict = input_data.get("handoff", {})
        if isinstance(handoff_dict, dict):
            handoff_findings = handoff_dict.get("key_findings", [])
            if handoff_findings:
                for hf in handoff_findings:
                    if isinstance(hf, dict):
                        fp = hf.get("file_path", "")
                        if not fp or fp.lower() in ("unknown", "n/a", ""):
                            continue
                        if hf not in findings_to_verify:
                            findings_to_verify.append(hf)
                if findings_to_verify:
                    logger.info(f"[Verification] 从 handoff.key_findings 补充了 {len(handoff_findings)} 个发现")

        # 去重
        findings_to_verify = self._deduplicate(findings_to_verify)

        # 🔥 FIX: 优先处理有明确文件路径的发现，将没有文件路径的发现放到后面
        # 这确保 Analysis 的具体发现优先于 Recon 的泛化描述
        def has_valid_file_path(finding: Dict) -> bool:
            file_path = finding.get("file_path", "")
            return bool(file_path and file_path.strip() and file_path.lower() not in ["unknown", "n/a", ""])

        findings_with_path = [f for f in findings_to_verify if has_valid_file_path(f)]
        findings_without_path = [f for f in findings_to_verify if not has_valid_file_path(f)]

        # 合并：有路径的在前，没路径的在后
        findings_to_verify = findings_with_path + findings_without_path

        if findings_with_path:
            logger.info(f"[Verification] 优先处理 {len(findings_with_path)} 个有明确文件路径的发现")
        if findings_without_path:
            logger.info(f"[Verification] 还有 {len(findings_without_path)} 个发现需要自行定位文件")

        if not findings_to_verify:
            logger.warning(f"[Verification] 没有需要验证的发现! previous_results keys: {list(previous_results.keys()) if isinstance(previous_results, dict) else 'not dict'}")
            await self.emit_event("warning", "没有需要验证的发现 - 可能是数据格式问题")
            return AgentResult(
                success=True,
                data={"findings": [], "verified_count": 0, "note": "未收到待验证的发现"},
            )
        
        # REQ-VC-1：不再按固定数量截断待验清单——全部传入 finding 均生成确定性 PoC 并执行，
        # 规模由弹性迭代预算（per_finding*n 上限）与确定性执行线性成本约束。
        # 历史缺陷：[:20] 硬截断把多轮 analysis 的尾部 finding（多为早期轮次）砍掉 → 零证据。
        
        await self.emit_event(
            "info",
            f"开始验证 {len(findings_to_verify)} 个发现"
        )
        
        # 🔥 记录工作开始
        self.record_work(f"开始验证 {len(findings_to_verify)} 个漏洞发现")

        # 🔥 FIX: 为每个发现自动生成 sandbox_exec 命令
        # P4 修复：将待验证 findings 存到 self._all_findings，供 _resolve_finding_id_from_command 反查
        self._all_findings = findings_to_verify
        sandbox_commands = self._build_sandbox_commands(findings_to_verify)
        logger.info(f"[{self.name}] 已为 {len(sandbox_commands)} 个发现生成沙箱验证命令")
        # 准备沙箱文件挂载
        sandbox_project_root = self._prepare_sandbox_files(findings_to_verify)
        if sandbox_project_root:
            await self.emit_event("info", f"沙箱将挂载项目目录: {sandbox_project_root}")
        else:
            await self.emit_event("warning", "无法确定项目目录，沙箱将使用空环境")

        # 🔥 构建包含交接上下文的初始消息
        handoff_context = self.get_handoff_context()
        
        findings_summary = []
        for i, f in enumerate(findings_to_verify):
            # 🔥 FIX: 正确处理 file_path 格式，可能包含行号 (如 "app.py:36")
            file_path = f.get('file_path', 'unknown')
            line_start = f.get('line_start', 0)

            # 如果 file_path 已包含行号，提取出来
            if isinstance(file_path, str) and ':' in file_path:
                parts = file_path.split(':', 1)
                if len(parts) == 2 and parts[1].split()[0].isdigit():
                    file_path = parts[0]
                    try:
                        line_start = int(parts[1].split()[0])
                    except ValueError:
                        pass

            findings_summary.append(f"""
### 发现 {i+1}: {f.get('title', 'Unknown')}
- 类型: {f.get('vulnerability_type', 'unknown')}
- 严重度: {f.get('severity', 'medium')}
- 文件: {file_path} (行 {line_start})
- 代码:
```
{f.get('code_snippet', 'N/A')[:500]}
```
- 描述: {f.get('description', 'N/A')[:300]}
""")
        
        initial_message = f"""请验证以下 {len(findings_to_verify)} 个安全发现。

{handoff_context if handoff_context else ''}

## 待验证发现
{''.join(findings_summary)}

## ⚠️ 重要验证指南
1. **直接使用上面列出的文件路径** - 不要猜测或搜索其他路径
2. **如果文件路径包含冒号和行号** (如 "app.py:36"), 请提取文件名 "app.py" 并使用 read_file 读取
3. **先读取文件内容，再判断漏洞是否存在**
4. **不要假设文件在子目录中** - 使用发现中提供的精确路径

## 验证要求
- 验证级别: {config.get('verification_level', 'standard')}

## 可用工具
{self.get_tools_description()}

请开始验证。对于每个发现：
1. 首先使用 read_file 读取发现中指定的文件（使用精确路径）
2. 分析代码上下文
3. 判断是否为真实漏洞
{f"特别注意 Analysis Agent 提到的关注点。" if handoff_context else ""}"""

        # 注入跨轮传递上下文
        if cross_round_context:
            initial_message += f"\n{cross_round_context}\n"

        # 初始化对话历史
        self._conversation_history = [
            {"role": "system", "content": self.config.system_prompt},
            {"role": "user", "content": initial_message},
        ]
        
        self._steps = []
        self._sandbox_exec_calls = 0
        self._sandbox_attempts = []
        # V6 B4（REQ-VE-4）：运行时证据按 finding_id 建索引（确定性执行显式直写，
        # 绑定/回填消费索引，正确性不依赖命令文本注释反解）
        self._runtime_attempts_by_finding_id: Dict[str, List[Dict[str, Any]]] = {}
        self._backfill_used_indices = set()
        # B2 弹性总上限：按 finding 数量动态调整，避免队尾饿死
        n_findings = len(findings_to_verify)
        if n_findings > 0:
            # B5-fix: 改用正确的 get_agent_config()（config.py 无模块级 config 变量），
            # 包 try-except 防御——import/配置异常时用默认值 8，不让配置问题搞垮整个 Agent
            # （原 from ... import config 会抛 ImportError，且在 run() 主循环前、try 块外，直接崩 Agent）
            try:
                from app.services.agent.config import get_agent_config
                per_finding = getattr(get_agent_config(), "per_finding_budget", 8)
            except Exception:
                per_finding = 8
                logger.warning("[Verification] 弹性预算配置读取失败，用默认值 per_finding=8", exc_info=True)
            elastic_max = min(per_finding * n_findings + 20, 160)
            if elastic_max > self.config.max_iterations:
                self.config.max_iterations = elastic_max
                logger.info(f"[Verification] 弹性预算: {n_findings} findings → {elastic_max} iterations")
        # 根因3: 拆分计数器（attempts/success/逐 finding 追踪）
        self._init_sandbox_counters()
        self._tool_call_counts = {}
        self._failed_tool_calls = {}
        final_result = None
        
        await self.emit_thinking("🔐 Verification Agent 启动，LLM 开始自主验证漏洞...")
        
        try:
            # R3 确定性沙箱执行：进入 LLM 循环前先对全部预生成 PoC 执行一次，
            # 确保每个 finding 都有运行时证据，不依赖 LLM 是否主动调用 sandbox_exec。
            if not self.is_cancelled:
                await self._run_deterministic_sandbox_commands(sandbox_commands, sandbox_project_root)

            for iteration in range(self.config.max_iterations):
                if self.is_cancelled:
                    break
                # P1: token 预算门禁
                if self._check_token_budget_exceeded():
                    logger.warning(f"[{self.name}] Token budget exhausted, stopping sub-agent")
                    break

                self._iteration = iteration + 1
                
                # 🔥 再次检查取消标志（在LLM调用之前）
                if self.is_cancelled:
                    await self.emit_thinking("🛑 任务已取消，停止执行")
                    break

                # 🔥 FIX: 强制引导 LLM 使用 sandbox_exec
                # 如果 LLM 做了多轮迭代却从未调用 sandbox_exec，注入包含具体命令的强制提示
                if self._sandbox_exec_calls == 0 and self._iteration >= 3:
                    # 构建前3个发现的具体命令
                    cmd_lines = []
                    for sc in sandbox_commands[:3]:
                        input_json = json.dumps(sc['input'], ensure_ascii=False)
                        cmd_lines.append(f"- **{sc['label']}**:\n  Action: sandbox_exec\n  Action Input: {input_json}")

                    cmds_text = "\n".join(cmd_lines)
                    force_msg = (
                        f"🔴 **系统强制干预 (第{self._iteration}轮)**: 你已进行 {self._iteration} 轮但**从未使用 sandbox_exec**！\n\n"
                        f"阅读代码不能替代沙箱验证。以下是系统为你生成的 sandbox_exec 命令，**你必须立即执行其中任意一个**:\n\n"
                        f"{cmds_text}\n\n"
                        f"**现在请立即输出 Action: sandbox_exec，复制上述任意一条命令的参数即可。不要做任何其他事情！**"
                    )
                    await self.emit_thinking(f"🔄 强制引导: 第{self._iteration}轮，注入{len(sandbox_commands[:3])}条沙箱命令")
                    self._conversation_history.append({"role": "user", "content": force_msg})

                if self._sandbox_exec_calls == 0 and self._iteration >= 8:
                    # 更严厉的警告
                    remaining_cmds = []
                    for sc in sandbox_commands[:5]:
                        input_json = json.dumps(sc['input'], ensure_ascii=False)
                        remaining_cmds.append(f"- Action: sandbox_exec, Action Input: {input_json}")
                    force_msg = (
                        f"🚨 **最终警告 (第{self._iteration}轮)**: 你仍拒绝调用 sandbox_exec！\n\n"
                        f"系统不允许仅通过代码阅读来判断漏洞。你必须实际执行沙箱验证。\n\n"
                        f"以下是前5个发现的 sandbox_exec 命令。**立即执行，没有其他选项**:\n\n"
                        + "\n".join(remaining_cmds) +
                        f"\n\n**现在立刻输出 Action: sandbox_exec！否则验证将超时失败！**"
                    )
                    await self.emit_thinking(f"🚨 最终警告: 第{self._iteration}轮仍无sandbox_exec")
                    self._conversation_history.append({"role": "user", "content": force_msg})

                # 调用 LLM 进行思考和决策（流式输出）
                try:
                    llm_output, tokens_this_round = await self.stream_llm_call(
                        self._conversation_history,
                        # 🔥 不传递 temperature 和 max_tokens，使用用户配置
                    )
                except asyncio.CancelledError:
                    logger.info(f"[{self.name}] LLM call cancelled")
                    break
                
                self._total_tokens += tokens_this_round

                # 🔥 Handle empty LLM response to prevent loops
                if not llm_output or not llm_output.strip():
                    logger.warning(f"[{self.name}] Empty LLM response in iteration {self._iteration}")
                    await self.emit_llm_decision("收到空响应", "LLM 返回内容为空，尝试重试通过提示")
                    self._conversation_history.append({
                        "role": "user",
                        "content": "Received empty response. Please output your Thought and Action.",
                    })
                    continue

                # 解析 LLM 响应
                step = self._parse_llm_response(llm_output)
                self._steps.append(step)
                
                # 🔥 发射 LLM 思考内容事件 - 展示验证的思考过程
                if step.thought:
                    await self.emit_llm_thought(step.thought, iteration + 1)
                
                # 添加 LLM 响应到历史（V6 B5：assistant 输出同样过截断 + 压缩检查）
                self._conversation_history.append({
                    "role": "assistant",
                    "content": self._truncate_observation_for_history(llm_output),
                })
                self._compress_history_if_needed()
                
                # 检查是否完成
                if step.is_final:
                    # 根因3: 门禁按成功次数 + 逐 finding 覆盖（失败调用不凑数）
                    total_to_verify = len(findings_to_verify)
                    success_calls = getattr(self, '_sandbox_exec_success', 0)
                    verified_indices = getattr(self, '_verified_finding_indices', set())
                    unverified_count = total_to_verify - len(verified_indices)
                    # 放行条件：所有 finding 已成功验证，或剩余 finding 在 Final Answer 显式标了 sandbox_skip_reason
                    # Bug1-fix: 用 step.final_answer（本轮 LLM 实际输出）而非 final_result（此时仍为 None，
                    # 在第 904 行才赋值），否则 _count_skip_reasons 永远拿到 None → skip_reason 永不识别
                    skip_reasons = self._count_skip_reasons(step.final_answer)
                    effective_unverified = max(0, unverified_count - skip_reasons)
                    # Fix: 弹性退出条件 - 当 sandbox_exec 总尝试次数超过 total_to_verify * 3 时，放行退出
                    total_attempts = getattr(self, '_sandbox_exec_attempts', 0)
                    elastic_exhausted = total_attempts >= max(total_to_verify * 3, 10)
                    if elastic_exhausted and effective_unverified > 0:
                        logger.info(
                            f"[{self.name}] Elastic exit: {total_attempts} sandbox attempts for {total_to_verify} findings, allowing finish"
                        )
                        await self.emit_event("info", f"沙箱验证已达弹性上限（{total_attempts} 次尝试），允许完成")
                    if effective_unverified > 0 and (success_calls < total_to_verify or len(verified_indices) < total_to_verify) and self._iteration < self.config.max_iterations and not elastic_exhausted:
                        logger.warning(
                            f"[{self.name}] LLM tried to finish with only {success_calls}/{total_to_verify} successful sandbox_exec "
                            f"(verified {len(verified_indices)}/{total_to_verify} findings)! Forcing more sandbox usage."
                        )
                        await self.emit_thinking(
                            f"⚠️ 系统拒绝完成：只成功验证了 {len(verified_indices)}/{total_to_verify} 个发现（成功调用 {success_calls} 次）！"
                            f"还有 {effective_unverified} 个发现未成功验证。失败的重试不算，必须成功验证每个发现或标注 sandbox_skip_reason。"
                        )
                        # 找出未验证的发现并列出命令
                        unverified_idxs = [i for i in range(total_to_verify) if i not in verified_indices]
                        remaining = []
                        for i in unverified_idxs[:3]:
                            if i < len(sandbox_commands):
                                remaining.append(sandbox_commands[i])
                        if not remaining:
                            remaining = sandbox_commands[:3]
                        cmd_lines = []
                        for sc in remaining:
                            input_json = json.dumps(sc['input'], ensure_ascii=False)
                            cmd_lines.append(f"- **{sc['label']}**: Action: sandbox_exec, Action Input: {input_json}")
                        self._conversation_history.append({
                            "role": "user",
                            "content": (
                                f"⚠️ **系统拒绝**: 你只成功验证了 {len(verified_indices)}/{total_to_verify} 个发现！\n\n"
                                f"还有 {effective_unverified} 个发现未成功验证。失败的 sandbox_exec 不算数，必须重试成功，或在 Final Answer 中为该 finding 标注 `sandbox_skip_reason`（说明无法沙箱验证的原因）。\n\n"
                                f"以下是还需要验证的发现，必须用 sandbox_exec 成功验证:\n\n"
                                + "\n".join(cmd_lines) +
                                f"\n\n现在请立即用 sandbox_exec 验证下一个发现（确保 PoC 能跑出结果）！"
                            ),
                        })
                        continue

                    await self.emit_llm_decision(
                        f"完成漏洞验证 ({len(verified_indices)}/{total_to_verify} 个发现已验证, {success_calls} 次成功调用)",
                        "LLM 判断验证已充分"
                    )
                    final_result = step.final_answer
                    
                    # 🔥 记录洞察和工作
                    if final_result and "findings" in final_result:
                        verified_count = len([f for f in final_result["findings"] if f.get("is_verified")])
                        fp_count = len([f for f in final_result["findings"] if f.get("verdict") == "false_positive"])
                        self.add_insight(f"验证了 {len(final_result['findings'])} 个发现，{verified_count} 个确认，{fp_count} 个误报")
                        self.record_work(f"完成漏洞验证: {verified_count} 个确认, {fp_count} 个误报")
                    
                    await self.emit_llm_complete(
                        f"验证完成",
                        self._total_tokens
                    )
                    break
                
                # 执行工具
                if step.action:
                    # 🔥 发射 LLM 动作决策事件
                    await self.emit_llm_action(step.action, step.action_input or {})
                    
                    start_tool_time = time.time()
                    
                    # 🔥 智能循环检测: 追踪重复调用 (无论成功与否)
                    tool_call_key = _normalize_tool_key(step.action, step.action_input or {})
                    
                    if not hasattr(self, '_tool_call_counts'):
                        self._tool_call_counts = {}
                    
                    self._tool_call_counts[tool_call_key] = self._tool_call_counts.get(tool_call_key, 0) + 1
                    
                    # 如果同一操作重复尝试超过3次，强制干预
                    if self._tool_call_counts[tool_call_key] > 3:
                        logger.warning(f"[{self.name}] Detected repetitive tool call loop: {tool_call_key}")
                        observation = (
                            f"⚠️ **系统干预**: 你已经使用完全相同的参数调用了工具 '{step.action}' 超过3次。\n"
                            "请**不要**重复尝试相同的操作。这是无效的。\n"
                            "请尝试：\n"
                            "1. 修改参数 (例如改变 input payload)\n"
                            "2. 使用不同的工具 (例如从 sandbox_exec 换到 php_test)\n"
                            "3. 如果之前的尝试都失败了，请尝试 analyze_file 重新分析代码\n"
                            "4. 继续用 sandbox_exec 验证下一个发现，不要卡在同一个发现上"
                        )
                        
                        # 模拟观察结果，跳过实际执行（V6 B5：截断后入历史）
                        step.observation = observation
                        await self.emit_llm_observation(observation)
                        self._conversation_history.append({
                            "role": "user",
                            "content": "Observation:\n" + self._truncate_observation_for_history(observation),
                        })
                        continue

                    # 🔥 循环检测：追踪工具调用失败历史 (保留原有逻辑用于错误追踪)
                    if not hasattr(self, '_failed_tool_calls'):
                        self._failed_tool_calls = {}
                    if not hasattr(self, '_sandbox_exec_calls'):
                        self._sandbox_exec_calls = 0
                    
                    observation = await self.execute_tool(
                        step.action,
                        step.action_input or {}
                    )

                    # 🔥 追踪 sandbox_exec 调用
                    if step.action == "sandbox_exec":
                        self._sandbox_exec_calls += 1
                        # 根因3: 拆分 attempts/success，失败不计入 success
                        self._sandbox_exec_attempts += 1
                        cmd_str = str((step.action_input or {}).get("command", ""))
                        finding_idx = self._parse_finding_index_from_command(cmd_str)
                        # 判断 sandbox_exec 是否成功（observation 含 success 标记）
                        obs_str = str(observation) if observation else ""
                        is_success = self._is_sandbox_success(obs_str)
                        if is_success:
                            self._sandbox_exec_success += 1
                            if finding_idx is not None:
                                self._verified_finding_indices.add(finding_idx)
                        self._record_sandbox_attempt(step.action_input or {}, observation)
                        logger.info(
                            f"[{self.name}] ✅ sandbox_exec called (attempts={self._sandbox_exec_attempts}, "
                            f"success={self._sandbox_exec_success}, finding_idx={finding_idx}, ok={is_success})"
                        )
                    elif step.action in LANGUAGE_TEST_TOOL_NAMES:
                        self._record_language_test_attempt(step.action, step.action_input or {}, observation)
                        logger.info(f"[{self.name}] ✅ {step.action} called and tracked as sandbox evidence")
                    
                    # 🔥 检测工具调用失败并追踪
                    is_tool_error = (
                        "失败" in observation or 
                        "错误" in observation or 
                        "不存在" in observation or
                        "文件过大" in observation or
                        "Error" in observation
                    )
                    
                    if is_tool_error:
                        self._failed_tool_calls[tool_call_key] = self._failed_tool_calls.get(tool_call_key, 0) + 1
                        fail_count = self._failed_tool_calls[tool_call_key]
                        
                        # 🔥 如果同一调用连续失败3次，添加强制跳过提示
                        if fail_count >= 3:
                            logger.warning(f"[{self.name}] Tool call failed {fail_count} times: {tool_call_key}")
                            observation += f"\n\n⚠️ **系统提示**: 此工具调用已连续失败 {fail_count} 次。请：\n"
                            observation += "1. 尝试使用不同的参数（如指定较小的行范围）\n"
                            observation += "2. 使用 search_code 工具定位关键代码片段\n"
                            observation += "3. 改用 sandbox_exec 直接进行沙箱验证\n"
                            observation += "4. 继续验证下一个发现，使用 sandbox_exec"
                            
                            # 重置计数器
                            self._failed_tool_calls[tool_call_key] = 0
                    else:
                        # 成功调用，重置失败计数
                        if tool_call_key in self._failed_tool_calls:
                            del self._failed_tool_calls[tool_call_key]

                    # 🔥 工具执行后检查取消状态
                    if self.is_cancelled:
                        logger.info(f"[{self.name}] Cancelled after tool execution")
                        break

                    step.observation = observation
                    
                    # 🔥 发射 LLM 观察事件
                    await self.emit_llm_observation(observation)
                    
                    # 添加观察结果到历史（V6 B5/REQ-VE-5：单条截断 + 累计压缩，会话有界）
                    self._conversation_history.append({
                        "role": "user",
                        "content": "Observation:\n" + self._truncate_observation_for_history(observation),
                    })
                    self._compress_history_if_needed()
                else:
                    # LLM 没有选择工具，提示它继续
                    await self.emit_llm_decision("继续验证", "LLM 需要更多验证")
                    self._conversation_history.append({
                        "role": "user",
                        "content": "请继续验证。你输出了 Thought 但没有输出 Action。请**立即**选择一个工具执行，或者如果验证完成，输出 Final Answer 汇总所有验证结果。",
                    })
            
            # 处理结果
            duration_ms = int((time.time() - start_time) * 1000)
            
            # 🔥 如果被取消，返回取消结果
            if self.is_cancelled:
                await self.emit_event(
                    "info",
                    f"🛑 Verification Agent 已取消: {self._iteration} 轮迭代"
                )
                return AgentResult(
                    success=False,
                    error="任务已取消",
                    data={"findings": findings_to_verify},
                    iterations=self._iteration,
                    tool_calls=self._tool_calls,
                    tokens_used=self._total_tokens,
                    duration_ms=duration_ms,
                )
            
            # 处理最终结果
            verified_findings = []

            # 🔥 Robustness: If LLM returns empty findings but we had input, fallback to original
            llm_findings = []
            if final_result and "findings" in final_result:
                llm_findings = final_result["findings"]

            if not llm_findings and findings_to_verify:
                logger.warning(f"[{self.name}] LLM returned empty findings despite {len(findings_to_verify)} inputs. Falling back to originals.")
                # Fallback to logic below (else branch)
                final_result = None

            if final_result and "findings" in final_result:
                # 🔥 DEBUG: Log what LLM returned for verdict diagnosis
                verdicts_debug = [(f.get("file_path", "?"), f.get("verdict"), f.get("confidence")) for f in final_result["findings"]]
                logger.info(f"[{self.name}] LLM returned verdicts: {verdicts_debug}")

                original_paths = {(orig.get("file_path") or "").strip().lower(): orig for orig in findings_to_verify}
                original_path_set = set(original_paths.keys())

                if len(final_result["findings"]) > len(findings_to_verify):
                    logger.warning(f"[{self.name}] LLM hallucination detected: returned {len(final_result['findings'])} findings but only {len(findings_to_verify)} were given. Truncating.")
                    filtered = []
                    for f in final_result["findings"]:
                        fp = (f.get("file_path") or "").strip().lower()
                        if fp in original_path_set:
                            filtered.append(f)
                    if len(filtered) < len(findings_to_verify):
                        for f in final_result["findings"]:
                            if f not in filtered:
                                filtered.append(f)
                        filtered = filtered[:len(findings_to_verify)]
                    final_result["findings"] = filtered

                for f in final_result["findings"]:
                    # 🔥 FIX: 回填 LLM 丢失的原始元数据
                    self._backfill_original_metadata(f, findings_to_verify)
                    self._attach_runtime_sandbox_attempts(f)
                    strict = self._normalize_verification_outcome(f)

                    if not strict.get("recommendation"):
                        strict["recommendation"] = self._get_recommendation(f.get("vulnerability_type", ""))

                    verified_findings.append(strict)
            else:
                # V6 B3（REQ-VE-3）：LLM 空 Final Answer——回填运行时证据后再归一化，
                # 状态由 compute_verification_status 据实推导，不得一律 needs_context
                verified_findings.extend(
                    self._finalize_findings_without_final_answer(findings_to_verify)
                )

            # === FIX P0-1: 兜底沙箱验证 ===
            # 如果循环自然结束（LLM 始终未调用 sandbox_exec），
            # 对第一个 finding 强制执行一次 sandbox_exec，确保证据链完整
            if self._sandbox_exec_attempts == 0 and findings_to_verify and sandbox_commands:
                logger.warning(
                    f"[{self.name}] Loop ended without any sandbox_exec, "
                    f"forcing fallback sandbox for first finding"
                )
                try:
                    fallback_cmd = sandbox_commands[0]["input"]
                    sandbox_mgr = self._get_sandbox_manager()
                    if sandbox_mgr and sandbox_project_root:
                        result_dict = await sandbox_mgr.execute_with_files(
                            command=fallback_cmd.get("command", ""),
                            host_project_dir=sandbox_project_root,
                            timeout=fallback_cmd.get("timeout", 60),
                        )
                        result = self._format_sandbox_result(result_dict)
                    else:
                        result = await self.execute_tool("sandbox_exec", fallback_cmd)
                    self._sandbox_exec_calls += 1
                    # 根因3: 同步更新新计数器
                    self._sandbox_exec_attempts += 1
                    if self._is_sandbox_success(str(result)):
                        self._sandbox_exec_success += 1
                        self._verified_finding_indices.add(0)
                    self._record_sandbox_attempt(fallback_cmd, result)
                    # 将沙箱证据附加到第一个 finding
                    # Bug2-fix: 不再硬编码 success=True/exit_code=0，从实际结果读取，
                    # 避免失败的兜底验证被记录为成功导致 _normalize_verification_outcome 误判
                    # Bug2b-fix: fb_success 用与 _record_sandbox_attempt 一致的严判定
                    # （has_vuln_evidence OR has_output），而非偏宽的 _is_sandbox_success
                    # （后者把"Verification Complete"等"PoC 跑完"标记也算成功，会让兜底
                    # 路径下"仅 exit 0 无漏洞证据"误判为 confirmed，违反 B3 严标准）
                    if verified_findings:
                        obs_str = str(result)
                        obs_lower_fb = obs_str.lower()
                        fb_has_failure = any(m in obs_str for m in SANDBOX_FAILURE_MARKERS)
                        fb_has_vuln = (
                            "vulnerability_confirmed(static)" not in obs_lower_fb
                            and any(m.lower() in obs_lower_fb for m in VULN_EVIDENCE_MARKERS)
                        )
                        fb_has_output = len(obs_lower_fb.strip()) >= 50
                        fb_exit = None
                        _exit_match = re.search(r"退出码:\s*(-?\d+)", obs_str)
                        if _exit_match:
                            try:
                                fb_exit = int(_exit_match.group(1))
                            except ValueError:
                                fb_exit = None
                        fb_exit_val = fb_exit if fb_exit is not None else -1
                        fb_success = (not fb_has_failure) and (fb_exit_val == 0) and (fb_has_vuln or fb_has_output)
                        verified_findings[0]["sandbox_attempts"] = (
                            verified_findings[0].get("sandbox_attempts") or []
                        ) + [{
                            "success": fb_success,
                            "exit_code": fb_exit_val,
                            "evidence_summary": obs_str[:500],
                            "target_ref": (
                                f"{verified_findings[0].get('file_path', '')}:"
                                f"{verified_findings[0].get('line_start', 0)}"
                            ),
                        }]
                    await self.emit_event(
                        "warning",
                        f"兜底沙箱验证已执行: {sandbox_commands[0].get('label', 'fallback')}"
                    )
                except Exception as fallback_err:
                    logger.error(
                        f"[{self.name}] Fallback sandbox_exec failed: {fallback_err}"
                    )

            # R2 全量证据强制绑定：LLM 漏报的 finding 也必须获得运行时沙箱证据。
            # LLM Final Answer 只覆盖它自己报告的 findings；这里对全部 findings_to_verify
            # 兜底附加 runtime evidence，避免证据因 LLM 漏报而丢失（历史任务 4/5 丢失）。
            self._bind_runtime_evidence_to_all(verified_findings, findings_to_verify)

            # 统计
            confirmed_count = len([f for f in verified_findings if f.get("verification_status") == VerificationStatus.CONFIRMED])
            not_reproducible_count = len([f for f in verified_findings if f.get("verification_status") == VerificationStatus.NOT_REPRODUCIBLE])
            false_positive_count = len([f for f in verified_findings if f.get("verification_status") == VerificationStatus.FALSE_POSITIVE])
            needs_context_count = len([f for f in verified_findings if f.get("verification_status") == VerificationStatus.NEEDS_CONTEXT])

            await self.emit_event(
                "info",
                f"Verification Agent 完成: {confirmed_count} 确认, {false_positive_count} 误报, {not_reproducible_count} 无法复现, {needs_context_count} 需上下文"
            )

            # 🔥 CRITICAL: Log final findings count before returning
            logger.info(f"[{self.name}] Returning {len(verified_findings)} verified findings")

            # 🔥 创建 TaskHandoff - 记录验证结果，供 Orchestrator 汇总
            handoff = self._create_verification_handoff(
                verified_findings, confirmed_count, false_positive_count, needs_context_count
            )

            return AgentResult(
                success=True,
                data={
                    "findings": verified_findings,
                    "verified_count": confirmed_count,
                    "false_positive_count": false_positive_count,
                    "not_reproducible_count": not_reproducible_count,
                    "needs_context_count": needs_context_count,
                    "steps": [
                        {
                            "thought": s.thought,
                            "action": s.action,
                            "action_input": s.action_input,
                            "observation": s.observation[:500] if s.observation else None,
                        }
                        for s in self._steps
                        if s.action and isinstance(s.action_input, dict)
                    ],
                },
                iterations=self._iteration,
                tool_calls=self._tool_calls,
                tokens_used=self._total_tokens,
                duration_ms=duration_ms,
                handoff=handoff,  # 🔥 添加 handoff
            )
            
        except Exception as e:
            logger.error(f"Verification Agent failed: {e}", exc_info=True)
            return AgentResult(success=False, error=str(e))
    
    def _parse_finding_index_from_command(self, command: str) -> Optional[int]:
        """根因3: 从 sandbox_exec 命令文本解析 finding 索引。

        优先从 poc_{index}.py 解析；其次从 /workspace/src/{file_path} 反查 findings。
        无法关联返回 None。
        """
        if not command:
            return None
        # 1. poc_{index}.py
        m = re.search(r"poc_(\d+)\.py", command)
        if m:
            return int(m.group(1))
        # 2. /workspace/src/{file_path} 反查
        m = re.search(r"/workspace/src/([^\s'\"]+)", command)
        if m and hasattr(self, "_all_findings") and self._all_findings:
            ref_path = m.group(1).strip()
            for i, f in enumerate(self._all_findings):
                if isinstance(f, dict) and f.get("file_path") and ref_path.endswith(str(f.get("file_path"))):
                    return i
        return None

    def _init_sandbox_counters(self) -> None:
        """根因3: 初始化计数器（attempts/success/verified_finding_indices）"""
        self._sandbox_exec_attempts = 0
        self._sandbox_exec_success = 0
        self._verified_finding_indices = set()

    def _is_sandbox_success(self, observation: str) -> bool:
        """根因3: 判断 sandbox_exec 是否成功（observation 含成功标记且无致命失败标记）。

        成功标记：含 "退出码: 0" 或 "success" 或 "[VULN]"（PoC 触发漏洞的证据）；
        失败标记：含 "Error" "文件不存在" "Traceback" "未找到" 等视为失败。
        默认不含明确成功标记时返回 False（避免中文失败信息误判）。
        """
        if not observation:
            return False
        obs = str(observation)
        # R3 反伪造：模拟/源码缺失输出即使带成功标记也不计为成功
        obs_lower = obs.lower()
        if any(marker.lower() in obs_lower for marker in FABRICATION_MARKERS):
            return False
        # 明确失败标记
        fail_markers = [
            "Error: 工具执行异常", "文件不存在", "FileNotFoundError",
            "Traceback (most recent call last)", "命令不能为空",
            "不在允许列表", "沙箱环境不可用", "Project dir not found",
            "未找到", "No such file", "失败",
        ]
        if any(m in obs for m in fail_markers):
            return False
        # 明确成功标记
        success_markers = ["退出码: 0", "exit_code: 0", "[VULN]", "SANDBOX", "Verification Complete", "✅"]
        return any(m in obs for m in success_markers)

    def _count_skip_reasons(self, final_result: Optional[dict]) -> int:
        """根因3: 统计 Final Answer 中显式标注 sandbox_skip_reason 的 finding 数。

        LLM 可为无法沙箱验证的 finding 标注 sandbox_skip_reason，门禁据此放行。
        """
        if not final_result or not isinstance(final_result, dict):
            return 0
        findings = final_result.get("findings") or []
        return sum(
            1 for f in findings
            if isinstance(f, dict) and f.get("sandbox_skip_reason")
        )


    def _record_sandbox_attempt(
        self, action_input: dict[str, Any], observation: str,
        finding_id: Optional[str] = None,
    ) -> None:
        """Persist structured evidence for sandbox_exec calls made during this verification run.

        V6 B4（REQ-VE-4）：finding_id 可由确定性执行路径显式传入（直写索引），
        未传时保留命令注释反解 + 反查两条既有路径（LLM 路径兼容）。
        """
        if not hasattr(self, "_sandbox_attempts"):
            self._sandbox_attempts = []
        if not hasattr(self, "_runtime_attempts_by_finding_id"):
            self._runtime_attempts_by_finding_id = {}

        command = (action_input or {}).get("command") or ""

        # Opt-1: Parse finding_id from command comment（显式传入优先，注释仅作 LLM 路径兜底）
        if finding_id is None:
            finding_id_match = re.search(r"# FINDING_ID:(\S+)", command)
            finding_id = finding_id_match.group(1) if finding_id_match else None

            # P4 修复：LLM 自写 PoC 无 # FINDING_ID 注释时，反查 findings 显式绑定 finding_id
            # 不再依赖命令文本注释，用命令中的 file_path/target_ref 反查 _sandbox_finding_id
            if finding_id is None:
                finding_id = self._resolve_finding_id_from_command(command)

        exit_code = None
        exit_match = re.search(r"退出码:\s*(-?\d+)", observation or "")
        if exit_match:
            try:
                exit_code = int(exit_match.group(1))
            except ValueError:
                exit_code = None

        has_failure_marker = _has_sandbox_failure_marker(observation or "", exit_code)
        # ✅ P2-1: 增加语义检查 - 仅 exit_code==0 不够，还需检查 PoC 输出是否包含漏洞触发证据
        # 注意：marker 与 observation 都转小写比较，避免大小写不匹配漏判
        obs_lower = (observation or "").lower()
        # V6 B6（REQ-VE-6）：演示性确认（与目标源码无数据流因果，模板 PoC 输出
        # VULNERABILITY_CONFIRMED(STATIC) 变体）→ static_evidence 降档标记，
        # compute_verification_status 分支 2 消费判 static_confirmed，不得判 confirmed
        static_evidence = "vulnerability_confirmed(static)" in obs_lower
        has_vuln_evidence = (
            not static_evidence
            and any(marker.lower() in obs_lower for marker in VULN_EVIDENCE_MARKERS)
        )
        # R3 反伪造：源码缺失/模拟输出 + 声称确认 → 证据不可信
        # （真实读取源码并打印确认标记的 attempt 不受影响）
        fabricated = has_vuln_evidence and any(
            marker.lower() in obs_lower for marker in FABRICATION_MARKERS
        )
        # success = exit_code==0 AND (有漏洞证据 OR 没有失败标记且有输出)
        has_output = len(obs_lower.strip()) >= 50  # 有实质性输出（>=50 字符，避免边界值误判）
        # 修复：exit_code None（regex 未匹配）时，仅依据证据判定，避免 None==0 假阴性
        if exit_code is None:
            success = not has_failure_marker and (has_vuln_evidence or has_output)
        else:
            success = not has_failure_marker and exit_code == 0 and (has_vuln_evidence or has_output)
        # R3: 伪造证据强制降级为失败，杜绝"Simulated + VULNERABILITY_CONFIRMED"被当铁证
        if fabricated:
            success = False
        # REQ-VE-2：验证器（PoC）自身崩溃与"未复现"分档——崩溃特征打 poc_error 标记，
        # 下游状态机据此判 needs_context（notes 注明），不冒充 not_reproducible、不被软证据兜底升级
        obs_text = str(observation or "")
        poc_error = any(m in obs_text for m in ("Traceback", "SyntaxError", "re.error", "unterminated"))
        poc_error_type = "pre-generated PoC crashed" if poc_error else None
        # Opt-1: command already extracted above for finding_id parsing
        target_match = re.search(r"Target:\s*([^'\"\n;]+)", command)
        target_ref = target_match.group(1).strip() if target_match else None

        attempt = {
            "tool": "sandbox_exec",
            "success": success,
            "exit_code": exit_code,
            "command": command,
            "target_ref": target_ref,
            "language": (action_input or {}).get("language"),
            "network_enabled": bool((action_input or {}).get("network_enabled", False)),
            "evidence_summary": self._truncate_evidence_summary(observation),
            "finding_id": finding_id,
            "fabricated": fabricated,
            "static_evidence": static_evidence,
            "poc_error": poc_error,
            "poc_error_type": poc_error_type,
        }
        self._sandbox_attempts.append(attempt)
        # V6 B4（REQ-VE-4）：双写——按 finding_id 登记运行时证据索引，
        # 绑定/回填消费索引（同一对象引用，避免双计）
        if finding_id:
            self._runtime_attempts_by_finding_id.setdefault(str(finding_id), []).append(attempt)
        return attempt

    def _resolve_finding_id_from_command(self, command: str) -> Optional[str]:
        """P4 修复：LLM 自写 PoC（无 # FINDING_ID 注释）时，从命令文本反查 finding_id。

        反查策略（复用 _parse_finding_index_from_command 的路径匹配逻辑）：
        1. poc_{index}.py -> 取 _all_findings[index] 的 _sandbox_finding_id
        2. /workspace/src/{file_path} -> 文件路径后缀匹配 _all_findings，取 _sandbox_finding_id
        3. 命令文本含 finding 的 file_path 关键词 -> 取 _sandbox_finding_id
        无法关联返回 None（后续由 _attach_runtime_sandbox_attempts 模糊匹配兜底）。
        """
        if not command:
            return None
        findings = getattr(self, "_all_findings", None) or []
        if not findings:
            return None

        # 1. poc_{index}.py
        m = re.search(r"poc_(\d+)\.py", command)
        if m:
            idx = int(m.group(1))
            if 0 <= idx < len(findings):
                f = findings[idx]
                if isinstance(f, dict):
                    fid = f.get("_sandbox_finding_id") or f.get("id")
                    if fid:
                        return str(fid)

        # 2. /workspace/src/{file_path} 反查
        m = re.search(r"/workspace/src/([^\s'\"]+)", command)
        if m:
            ref_path = m.group(1).strip()
            for f in findings:
                if isinstance(f, dict) and f.get("file_path") and ref_path.endswith(str(f.get("file_path"))):
                    fid = f.get("_sandbox_finding_id") or f.get("id")
                    if fid:
                        return str(fid)

        # 3. 命令文本含 finding 的 file_path 关键词（宽松兜底，取首个命中）
        cmd_lower = command.lower()
        for f in findings:
            if not isinstance(f, dict):
                continue
            fp = str(f.get("file_path") or "").strip()
            if fp and fp.lower() in cmd_lower:
                fid = f.get("_sandbox_finding_id") or f.get("id")
                if fid:
                    return str(fid)
        return None

    def _record_language_test_attempt(
        self, tool_name: str, action_input: dict[str, Any], observation: str
    ) -> None:
        if not hasattr(self, "_sandbox_attempts"):
            self._sandbox_attempts = []

        exit_code = None
        exit_match = re.search(r"退出码:\s*(-?\d+)", observation or "")
        if exit_match:
            try:
                exit_code = int(exit_match.group(1))
            except ValueError:
                exit_code = None
        exit_match_en = re.search(r"exit_code:\s*(-?\d+)", observation or "")
        if exit_match_en and exit_code is None:
            try:
                exit_code = int(exit_match_en.group(1))
            except ValueError:
                exit_code = None

        has_failure_marker = _has_sandbox_failure_marker(observation or "", exit_code)
        # 修复：exit_code None（regex 未匹配）时，仅依据失败标记判定，避免 None==0 假阴性
        if exit_code is None:
            success = not has_failure_marker
        else:
            success = not has_failure_marker and exit_code == 0

        code = (action_input or {}).get("code") or ""
        file_path_input = (action_input or {}).get("file_path") or ""
        target_ref = None

        target_match = re.search(r"Target:\s*([^'\"\n;]+)", code)
        if target_match:
            target_ref = target_match.group(1).strip()
        elif file_path_input:
            target_ref = str(file_path_input).strip()

        # P4 修复：language_test 工具同样反查 finding_id（优先 file_path_input，其次 code 文本）
        finding_id = self._resolve_finding_id_from_command(
            str(file_path_input) + "\n" + code
        )

        self._sandbox_attempts.append(
            {
                "tool": tool_name,
                "success": success,
                "exit_code": exit_code,
                "command": code,
                "target_ref": target_ref,
                "language": tool_name.rsplit("_", 1)[0] if "_" in tool_name else None,
                "network_enabled": False,
                "evidence_summary": self._truncate_evidence_summary(observation),
                "finding_id": finding_id,
            }
        )

    @staticmethod
    def _truncate_evidence_summary(observation: str, capacity: int = 5000) -> str:
        """REQ-VQ-1：证据摘要保头尾截断——确认标记常在 PoC 输出尾部，纯头部截断会丢证据。"""
        text = str(observation or "")
        if len(text) <= capacity:
            return text
        head = tail = capacity // 2
        omitted = len(text) - head - tail
        return (
            text[:head]
            + f"\n...[evidence truncated: {omitted} chars omitted]...\n"
            + text[-tail:]
        )

    @staticmethod
    def _attempt_dedupe_key(attempt: dict[str, Any]) -> tuple:
        """V6 B4：attempt 语义去重键（命令+退出码+证据摘要前缀）。"""
        return (
            str(attempt.get("command") or "")[:200],
            attempt.get("exit_code"),
            str(attempt.get("evidence_summary") or "")[:200],
        )

    def _attempt_evidence_stronger(self, new: dict[str, Any], old: dict[str, Any]) -> bool:
        """REQ-VQ-1：比较两条 attempt 的证据强度——含漏洞触发证据者更强，去重时优先保留。"""
        def _score(a: dict[str, Any]) -> int:
            ev = str(a.get("evidence_summary") or "").lower()
            s = 0
            if "vulnerability_confirmed(static)" not in ev and any(
                m.lower() in ev for m in VULN_EVIDENCE_MARKERS
            ):
                s += 2
            if a.get("static_evidence"):
                s += 1
            if a.get("success") is True:
                s += 1
            return s
        return _score(new) > _score(old)

    def _merge_attempts_deduped(
        self, existing: List[dict[str, Any]], incoming: List[dict[str, Any]]
    ) -> List[dict[str, Any]]:
        """V6 B4（REQ-VE-4）：合并 attempt 并按语义键去重，防止索引/扁平列表/重入绑定双计。

        REQ-VQ-1：同键冲突时保留证据更强的 attempt（先到者无证据、后到者带确认标记时，
        不得丢弃带证据的 attempt——否则状态可能从 confirmed/static_confirmed 跌为未复现）。
        """
        by_key: dict[tuple, dict[str, Any]] = {}
        for a in existing:
            if isinstance(a, dict):
                by_key[self._attempt_dedupe_key(a)] = a
        for a in incoming:
            if not isinstance(a, dict):
                continue
            key = self._attempt_dedupe_key(a)
            if key not in by_key:
                by_key[key] = a
            elif self._attempt_evidence_stronger(a, by_key[key]):
                by_key[key] = a
        return list(by_key.values())

    def _truncate_observation_for_history(self, observation: str) -> str:
        """V6 B5（REQ-VE-5）：单条 observation 写入历史前截断，保头尾 1500+1500。

        PoC 输出的铁证标记（退出码/VULNERABILITY_CONFIRMED）通常在尾部，保尾防丢证据；
        头部保留命令/目标上下文。
        """
        text = str(observation or "")
        try:
            from app.services.agent.config import get_agent_config
            max_chars = int(get_agent_config().observation_history_max_chars)
        except Exception:
            max_chars = 4000
        if len(text) <= max_chars:
            return text
        head, tail = 1500, 1500
        # 防御：配置值过小（<3000+标注空间）时收缩头尾，避免 omitted 为负/内容重复
        budget = max(max_chars - 200, 200)
        if head + tail > budget:
            head = tail = budget // 2
        omitted = max(len(text) - head - tail, 0)
        return (
            text[:head]
            + f"\n...[observation truncated: {omitted} chars omitted]...\n"
            + text[-tail:]
        )

    def _compress_history_if_needed(self) -> None:
        """V6 B5（REQ-VE-5）：累计历史超软上限时，最旧一半压缩为一条摘要消息。

        保留 system 提示与最近一半消息；摘要保留工具调用结论要点
        （退出码/铁证标记/最终答案行）。完整证据不受影响（在 sandbox_attempts 与索引中）。
        """
        history = getattr(self, "_conversation_history", None) or []
        try:
            from app.services.agent.config import get_agent_config
            soft_limit = int(get_agent_config().history_soft_limit_messages)
        except Exception:
            soft_limit = 40
        if len(history) <= soft_limit:
            return
        # history[0] 为 system 提示；压缩其后的最旧一半 user/assistant 消息
        compressible = history[1:]
        half = len(compressible) // 2
        if half <= 0:
            return
        oldest, kept = compressible[:half], compressible[half:]
        key_markers = (
            "VULNERABILITY_CONFIRMED", "退出码", "INJECTABLE", "Final Answer",
            "not found", "FALSE_POSITIVE", "Verification Complete",
        )
        parts = []
        for msg in oldest:
            content = str(msg.get("content") or "")
            key_lines = [
                ln.strip() for ln in content.splitlines()
                if any(k.lower() in ln.lower() for k in key_markers)
            ]
            digest = " | ".join(key_lines[:3])[:300] or content.strip()[:120]
            parts.append(f"[{msg.get('role', '?')}] {digest}")
        summary = (
            f"[会话压缩] 早期 {len(oldest)} 条消息已压缩为摘要（保留工具结论要点，"
            f"完整证据已记录在 sandbox_attempts）：\n" + "\n".join(parts)
        )
        self._conversation_history = [history[0], {"role": "user", "content": summary}] + kept

    def _finalize_findings_without_final_answer(self, findings_to_verify: List[Dict]) -> List[Dict]:
        """V6 B3（REQ-VE-3）：LLM 空 Final Answer 时的回退收口。

        归一化前先按 finding_id 回填运行时证据（消费 B4 索引与扁平列表），
        状态由 compute_verification_status 据实推导——
        有铁证 → confirmed/static_confirmed；执行未复现 → not_reproducible；
        禁止直接以 needs_context 覆盖有证据的 finding。
        """
        results: List[Dict] = []
        for f in findings_to_verify:
            target = {
                **f,
                "verdict": "needs_context",
                "confidence": 0.5,
                "is_verified": False,
            }
            if not target.get("sandbox_attempts"):
                self._attach_runtime_sandbox_attempts(target)
            results.append(self._normalize_verification_outcome(target))
        return results

    def _attach_runtime_sandbox_attempts(self, finding: dict[str, Any]) -> None:
        """Attach runtime sandbox evidence when the LLM omitted sandbox_attempts in Final Answer.

        两档匹配：
        1. 严匹配（B3）：attempt 含 VULNERABILITY_CONFIRMED 真证据 → 走 _sandbox_attempt_matches_finding（判 confirmed）
        2. 宽松兜底（方案C）：LLM 自写 PoC 成功复现但没输出标准标记时，
           finding 有 verification_method + runtime 有 success=True attempt + file_path/title 关键词匹配
           → 补入（判 static_confirmed，避免真漏洞被误判 not_reproducible）
        """
        existing_attempts = finding.get("sandbox_attempts") or []
        # 若 LLM 已填了含真证据的成功 attempt，不覆盖
        if any(isinstance(a, dict) and a.get("success") and self._attempt_has_vuln_evidence(a) for a in existing_attempts):
            return
        attempts = getattr(self, "_sandbox_attempts", [])

        # Opt-1: ID-based matching (precise, before fuzzy)
        # V6 B1（REQ-VE-1）：绑定层不再以 success=True 为前置——失败 attempt 也如实绑定，
        # 成败判定交给 compute_verification_status（有失败尝试 → not_reproducible 而非
        # needs_context）。生产任务 5a1f7ab6：21 次执行因该前置过滤 0 证据落库。
        # V6 B4（REQ-VE-4）：优先消费 finding_id 索引（确定性执行直写，反解失败也齐全），
        # 索引缺失时回退扁平列表过滤；合并前按语义键去重防止双计。
        finding_id = finding.get("_sandbox_finding_id")
        if finding_id:
            index = getattr(self, "_runtime_attempts_by_finding_id", None) or {}
            id_matched = index.get(str(finding_id)) or [
                a for a in attempts if a.get("finding_id") == finding_id
            ]
            if id_matched:
                finding["sandbox_attempts"] = self._merge_attempts_deduped(
                    existing_attempts, id_matched
                )
                logger.info(
                    f"[{self.name}] ID-based sandbox match: finding_id={finding_id} "
                    f"-> {len(id_matched)} attempts"
                )
                return

        # REQ-VB-2: ID 不可得时的位置兜底——LLM finding 可能既无 ID 又无法从原清单
        # 恢复（backfill 匹配失败），此时按 file_path+line_start 匹配运行时索引中的
        # attempt（target_ref/command 含路径）。兜底保证"确定性执行过的证据不丢"；
        # 优先精确行号，行号缺失时仅路径匹配。
        if not finding.get("sandbox_attempts"):
            index = getattr(self, "_runtime_attempts_by_finding_id", None) or {}
            fp = (finding.get("file_path") or "").strip().lower()
            ln = finding.get("line_start") or 0
            position_matched = []
            if fp:
                for attempts_by_id in index.values():
                    for a in attempts_by_id:
                        if not isinstance(a, dict) or a.get("finding_id") == finding_id:
                            continue
                        ref = str(a.get("target_ref") or "").strip().lower()
                        cmd = str(a.get("command") or "").lower()
                        hay = f"{ref} {cmd}"
                        if fp in hay or hay.endswith(fp):
                            if ln and f":{ln}" in hay:
                                position_matched.append(a)
                            elif not ln:
                                position_matched.append(a)
            if position_matched:
                finding["sandbox_attempts"] = self._merge_attempts_deduped(
                    existing_attempts, position_matched
                )
                logger.info(
                    f"[{self.name}] Position-based sandbox fallback: file={fp}:{ln} "
                    f"-> {len(position_matched)} attempts"
                )
                return

        # 严匹配（含真证据）
        matched_attempts = [a for a in attempts if self._sandbox_attempt_matches_finding(a, finding)]
        if matched_attempts:
            finding["sandbox_attempts"] = existing_attempts + matched_attempts
            return
        # 宽松兜底（方案C）：LLM 自写 PoC 成功但无标准标记
        # I-1/I-2 收紧：避免跨 finding 误关联 + 避免"跑完但没复现"的 attempt 被当弱证据
        # 双重条件：target_ref 精确匹配 finding file_path（含行号优先）+ evidence 含 vuln_type 关键词
        if not finding.get("verification_method"):
            return  # LLM 没说验证方法，不兜底
        file_path = str(finding.get("file_path") or "").strip().lower()
        file_name = file_path.replace("\\", "/").split("/")[-1] if file_path else ""
        line_start = finding.get("line_start") or 0
        vuln_type = str(finding.get("vulnerability_type") or "").lower().replace("_", " ")
        title = str(finding.get("title") or "").lower()
        # M-3: 中文 title 用空格分词无效，改用 vulnerability_type 关键词 + title 整串子串匹配
        title_kw = title if len(title) > 4 else ""
        for a in attempts:
            if a.get("success") is not True:
                continue
            # I-2: 跳过仅 has_output 但无任何正向迹象的 attempt——要求 evidence 非空且有实质内容
            ev = str(a.get("evidence_summary") or "")
            if len(ev.strip()) < 20:
                continue
            ev_lower = ev.lower()
            cmd = str(a.get("command") or "").lower()
            target_ref = str(a.get("target_ref") or "").strip().lower()
            # I-1: 精确匹配——target_ref 含 finding file_path（含行号更优），或 file_name 作为路径段边界
            path_match = False
            if target_ref:
                # target_ref 形如 "app.py:80" 或 "path/app.py:80"
                tf = target_ref.split(":")[0]
                if tf == file_path or tf.endswith(f"/{file_path}") or file_path.endswith(f"/{tf}"):
                    if line_start and f":{line_start}" in target_ref:
                        path_match = True  # 行号也匹配，强命中
                    elif not line_start:
                        path_match = True  # finding 无行号，文件匹配即可
            if not path_match and file_name:
                # 文件名作为路径段边界（避免 app.py 命中 myapp.py）
                cmd_segments = cmd.replace("/", " ").replace("\\", " ").split()
                if file_name in cmd_segments and (file_name in ev_lower or file_name in cmd):
                    path_match = True
            if not path_match:
                continue
            # vuln_type 关键词双条件（避免跨漏洞类型沾光）
            type_match = (vuln_type and (vuln_type in ev_lower or vuln_type in cmd)) or \
                         (title_kw and (title_kw in ev_lower or title_kw in cmd))
            if not type_match:
                continue
            logger.info(
                f"[Verification] 宽松兜底匹配: finding={file_path}:{line_start} "
                f"vuln_type={vuln_type} -> attempt target_ref={target_ref}"
            )
            # 打 weak_evidence 标记：has_weak_evidence 认此标记走 static_confirmed（非 confirmed）
            weak_a = dict(a)
            weak_a["weak_evidence"] = True
            finding["sandbox_attempts"] = existing_attempts + [weak_a]
            return

    def _bind_runtime_evidence_to_all(
        self, verified_findings: List[Dict], findings_to_verify: List[Dict]
    ) -> None:
        """R2: 对全部待验证 finding 强制绑定运行时沙箱证据。

        LLM Final Answer 只覆盖它报告的部分 findings；漏报的 finding 若不在此
        兜底，其 sandbox_attempts 会永久丢失。此处按 _sandbox_finding_id /
        路径反查把运行时证据附加到每个 finding，并重新归一化验证状态。
        """
        if not findings_to_verify:
            return
        # 建立 verified_findings 的路径→条目索引，避免重复插入
        seen_paths: dict[str, Dict] = {}
        for vf in verified_findings:
            fp = str(vf.get("file_path") or "").strip().lower()
            if fp:
                seen_paths.setdefault(fp, vf)

        for orig in findings_to_verify:
            fp = str(orig.get("file_path") or "").strip().lower()
            target = seen_paths.get(fp) if fp else None
            if target is None:
                # LLM 漏报的 finding：用原始 dict 兜底，attach 证据并归一化
                target = dict(orig)
                verified_findings.append(target)
                if fp:
                    seen_paths[fp] = target
            if target.get("sandbox_attempts"):
                continue
            self._attach_runtime_sandbox_attempts(target)
            # 证据可能改变状态（needs_context → confirmed/not_reproducible），重新归一化
            if target.get("sandbox_attempts"):
                strict = self._normalize_verification_outcome(target)
                target.clear()
                target.update(strict)

    def _attempt_has_vuln_evidence(self, attempt: dict[str, Any]) -> bool:
        """B3 严标准：判断沙箱 attempt 是否含真正的漏洞触发证据（VULNERABILITY_CONFIRMED 等）。

        统一用于 Path A（has_sandbox_evidence）和 Path B（has_weak_evidence）的证据标准，
        避免仅 "success=True（PoC 跑完有输出）" 就判 confirmed。
        marker 与 evidence 都转小写比较，避免大小写不匹配漏判。
        """
        evidence_summary = str(attempt.get("evidence_summary") or "")
        ev_lower = evidence_summary.lower()
        # 排除 (static) 变体：静态分析降级输出不应被当作动态铁证
        if "vulnerability_confirmed(static)" in ev_lower:
            return False
        # R3 反伪造：模拟/源码缺失输出不得作为漏洞触发证据
        if any(marker.lower() in ev_lower for marker in FABRICATION_MARKERS):
            return False
        return any(m.lower() in ev_lower for m in VULN_EVIDENCE_MARKERS)

    def _sandbox_attempt_matches_finding(self, attempt: dict[str, Any], finding: dict[str, Any]) -> bool:
        if attempt.get("success") is not True:
            return False
        if attempt.get("exit_code") != 0:
            return False
        # R3 反伪造：伪造证据一律不匹配
        if attempt.get("fabricated"):
            return False
        evidence_summary = str(attempt.get("evidence_summary") or "")
        if any(marker in evidence_summary for marker in SANDBOX_FAILURE_MARKERS):
            return False
        # B3-strict (Path A): 必须含真正的漏洞触发证据（VULNERABILITY_CONFIRMED 等）。
        # 仅 "退出码:0 + 有输出"（PoC 跑完但漏洞没真复现）不算动态复现铁证，
        # 不应判 has_sandbox_evidence=True → 不应判 confirmed（走 static_confirmed/not_reproducible）。
        # 统一用 _attempt_has_vuln_evidence，与 Path B 同标准。
        if not self._attempt_has_vuln_evidence(attempt):
            return False

        target_ref = str(attempt.get("target_ref") or "").strip().lower()
        file_path = str(finding.get("file_path") or "").strip().lower()
        line_start = finding.get("line_start") or 0
        if not target_ref or not file_path:
            # I3-fix: When target_ref is empty, try multiple fallback matching strategies
            if file_path and attempt.get("command"):
                cmd_lower = str(attempt["command"]).lower()
                file_name = file_path.replace("\\", "/").split("/")[-1]
                if file_name and file_name in cmd_lower:
                    return True
            # Also check evidence_summary for file_path keywords
            if file_path and evidence_summary:
                file_name = file_path.replace("\\", "/").split("/")[-1]
                if file_name and file_name.lower() in evidence_summary.lower():
                    return True
            # I3-fix: If finding has no file_path, try matching by vulnerability_type in command/evidence
            if not file_path:
                vuln_type = str(finding.get("vulnerability_type") or "").lower().replace("_", " ")
                title = str(finding.get("title") or "").lower()
                cmd_lower = str(attempt.get("command") or "").lower()
                evidence_lower = evidence_summary.lower()
                # Match if the command references the vulnerability type or finding title keywords
                if vuln_type and (vuln_type in cmd_lower or vuln_type in evidence_lower):
                    return True
                if title and len(title) > 10:
                    title_keywords = [w for w in title.split() if len(w) > 4]
                    if any(kw in cmd_lower or kw in evidence_lower for kw in title_keywords[:3]):
                        return True
                return False
            return False

        if ":" in target_ref:
            target_file, target_line = target_ref.rsplit(":", 1)
            target_file = target_file.strip()
            target_line = target_line.strip()
        else:
            target_file = target_ref
            target_line = ""

        target_file = target_file.replace("\\", "/").strip("/")
        file_path = file_path.replace("\\", "/").strip("/")
        same_file = (
            target_file == file_path
            or target_file.endswith(f"/{file_path}")
            or file_path.endswith(f"/{target_file}")
        )
        same_line = target_line == str(line_start) if line_start else True
        if same_file and same_line:
            return True

        # B1 证据兜底：沙箱已输出 VULNERABILITY_CONFIRMED 时放宽路径匹配
        evidence_upper = evidence_summary.upper()
        if "VULNERABILITY_CONFIRMED" in evidence_upper:
            vuln_type = str(finding.get("vulnerability_type") or "").lower().replace("_", " ")
            title = str(finding.get("title") or "").lower()
            evidence_lower = evidence_summary.lower()
            title_keywords = [w for w in title.split() if len(w) > 2]
            if (vuln_type and vuln_type in evidence_lower) or \
               any(kw in evidence_lower for kw in title_keywords[:4]):
                return True
        return False

    def _normalize_verification_outcome(self, finding: Dict[str, Any]) -> Dict[str, Any]:
        # R1 确定性状态引擎：验证结论由运行时沙箱证据推导，不信任 LLM 自述 verdict。
        # LLM 的 verdict 仅保留两个显式标注位：false_positive、sandbox_skip_reason。
        # 其余一律由 compute_verification_status 从 sandbox_attempts 计算。

        # Semgrep deterministic findings: 静态分析确认（无动态沙箱复现）
        # Bug4-fix: 改为 STATIC_CONFIRMED 而非 CONFIRMED，符合 B3 严标准
        # （confirmed 必须有动态复现铁证；Semgrep 这类是确定性静态分析，属代码推理确认）
        if finding.get("source") == "semgrep":
            deterministic_types = ["hardcoded_secret", "weak_crypto", "deserialization", "xxe"]
            if finding.get("vulnerability_type") in deterministic_types:
                finding["is_verified"] = True
                normalized = dict(finding)
                normalized["verification_status"] = VerificationStatus.STATIC_CONFIRMED
                normalized["verdict"] = VerificationStatus.STATIC_CONFIRMED
                normalized["is_verified"] = True
                normalized["verification_method"] = "semgrep_static_analysis"
                normalized["verified_at"] = datetime.now(timezone.utc).isoformat()
                return normalized

        attempts = finding.get("sandbox_attempts") or []
        status, is_verified, notes = compute_verification_status(
            finding,
            attempts,
            attempt_has_vuln_evidence_fn=self._attempt_has_vuln_evidence,
            attempt_matches_finding_fn=self._sandbox_attempt_matches_finding,
        )

        # 代码推理链确认（soft evidence）：沙箱环境受限无法动态复现时，
        # 有 dataflow+code_snippet+高置信度+verification_method → static_confirmed。
        # 仅当证据引擎未给出 confirmed/static_confirmed 时才兜底（避免覆盖铁证）。
        # REQ-VE-2：验证器崩溃（全 poc_error）的 finding 不得走软证据兜底，
        # 否则"PoC 崩溃"会被洗成"已确认"（掩盖验证器故障）。
        if status in (VerificationStatus.NEEDS_CONTEXT, VerificationStatus.NOT_REPRODUCIBLE) \
                and not any(a.get("poc_error") for a in attempts):
            VULN_TYPES_SOFT_EVIDENCE = {
                "xss", "ssrf", "auth_bypass", "csrf", "auth_missing", "tenant_isolation", "idor",
                "business_logic", "race_condition", "open_redirect",
                "path_traversal", "command_injection", "sql_injection",
            }
            vuln_type = (finding.get("vulnerability_type") or "").lower()
            has_soft_evidence = (
                vuln_type in VULN_TYPES_SOFT_EVIDENCE
                and bool(finding.get("dataflow_path"))
                and bool(finding.get("code_snippet"))
                and finding.get("ai_confidence", 0) >= 0.75
                and bool(finding.get("verification_method"))
            )
            if has_soft_evidence:
                status = VerificationStatus.STATIC_CONFIRMED
                is_verified = True
                finding["verification_note"] = (
                    "沙箱环境限制无法动态复现，通过代码推理链路静态确认为真实漏洞"
                )
                finding["verification_method"] = (
                    finding.get("verification_method", "static_reasoning")
                    + " (沙箱受限，代码推理链确认)"
                )

        normalized = dict(finding)
        normalized["verification_status"] = status
        normalized["verdict"] = status
        normalized["is_verified"] = is_verified
        normalized["verified_at"] = (
            datetime.now(timezone.utc).isoformat()
            if is_verified
            else None
        )
        if notes:
            normalized["verification_note"] = (
                str(normalized.get("verification_note") or "")
                + " " + "; ".join(f"{k}={v}" for k, v in notes.items())
            ).strip()
        return normalized

    def _backfill_original_metadata(self, llm_finding: Dict[str, Any], original_findings: List[Dict[str, Any]]) -> None:
        """用原始 finding 的元数据回填 LLM 输出中的 unknown 字段"""
        llm_fp = (llm_finding.get("file_path") or "").strip().lower()
        llm_type = (llm_finding.get("vulnerability_type") or "").strip().lower()
        llm_title = (llm_finding.get("title") or "").strip().lower()

        # REQ-VB-1: 恢复内部验证 ID——LLM Final Answer 的 finding 是重新序列化对象，
        # 不会携带 _sandbox_finding_id。按 file_path+line+type 精确匹配原清单恢复，
        # 让 ID 绑定路径（T4 索引）生效；匹配失败放行后续位置兜底。
        if not llm_finding.get("_sandbox_finding_id"):
            for orig in original_findings:
                if not isinstance(orig, dict):
                    continue
                if (str(orig.get("file_path") or "").strip().lower() == llm_fp
                        and (orig.get("line_start") or 0) == (llm_finding.get("line_start") or 0)
                        and str(orig.get("vulnerability_type") or "").strip().lower() == llm_type):
                    llm_finding["_sandbox_finding_id"] = orig.get("_sandbox_finding_id")
                    break

        needs_backfill = (
            not llm_fp or llm_fp in ("unknown", "?", "n/a", "null", "none")
            or not llm_type or llm_type in ("unknown", "?", "n/a", "null", "none")
        )
        if not needs_backfill:
            return

        best_match = None
        best_score = 0

        for orig in original_findings:
            orig_fp = (orig.get("file_path") or "").strip().lower()
            orig_type = (orig.get("vulnerability_type") or "").strip().lower()
            orig_title = (orig.get("title") or "").strip().lower()

            score = 0
            # file_path 匹配
            if orig_fp and llm_fp and llm_fp not in ("unknown", "?", "n/a"):
                if orig_fp in llm_fp or llm_fp in orig_fp:
                    score += 3
            # vulnerability_type 匹配
            if orig_type and llm_type and llm_type not in ("unknown", "?", "n/a"):
                if orig_type == llm_type:
                    score += 2
            # title 关键词重叠匹配
            if orig_title and llm_title:
                orig_words = set(orig_title.split())
                llm_words = set(llm_title.split())
                if orig_words and llm_words:
                    overlap = len(orig_words & llm_words) / max(len(orig_words), len(llm_words))
                    if overlap > 0.25:
                        score += 2
            # 旧 title 包含匹配（宽松兜底）
            if orig_title and llm_title and llm_title not in ("unknown", "?", "n/a"):
                if orig_title in llm_title or llm_title in orig_title:
                    score += 1

            if score > best_score:
                best_score = score
                best_match = orig

        if best_match and best_score >= 2:
            if not llm_finding.get("file_path") or llm_finding.get("file_path") == "unknown":
                llm_finding["file_path"] = best_match.get("file_path")
            if not llm_finding.get("vulnerability_type") or llm_finding.get("vulnerability_type") == "unknown":
                llm_finding["vulnerability_type"] = best_match.get("vulnerability_type")
            if not llm_finding.get("title") or "unknown" in (llm_finding.get("title") or "").lower():
                llm_finding["title"] = best_match.get("title")
            if not llm_finding.get("line_start") and best_match.get("line_start"):
                llm_finding["line_start"] = best_match.get("line_start")
            if not llm_finding.get("severity") or llm_finding.get("severity") == "unknown":
                llm_finding["severity"] = best_match.get("severity")
        elif not best_match and original_findings:
            if not hasattr(self, "_backfill_used_indices"):
                self._backfill_used_indices = set()

            remaining = [
                (i, o) for i, o in enumerate(original_findings)
                if i not in self._backfill_used_indices
                and not any(
                    (llm_finding.get("file_path") or "") == (o.get("file_path") or "")
                    and llm_finding.get("line_start") == o.get("line_start")
                    for llm_finding in [llm_finding]
                )
            ]
            if remaining:
                idx, fallback = remaining[0]
                self._backfill_used_indices.add(idx)
                llm_finding["file_path"] = fallback.get("file_path")
                llm_finding["vulnerability_type"] = fallback.get("vulnerability_type")
                llm_finding["title"] = fallback.get("title")
                llm_finding["line_start"] = fallback.get("line_start")
                llm_finding["severity"] = fallback.get("severity")

    def _get_recommendation(self, vuln_type: str) -> str:
        """获取修复建议"""
        recommendations = {
            "sql_injection": "使用参数化查询或 ORM，避免字符串拼接构造 SQL",
            "xss": "对用户输入进行 HTML 转义，使用 CSP，避免 innerHTML",
            "command_injection": "避免使用 shell=True，使用参数列表传递命令",
            "path_traversal": "验证和规范化路径，使用白名单，避免直接使用用户输入",
            "ssrf": "验证和限制目标 URL，使用白名单，禁止内网访问",
            "deserialization": "避免反序列化不可信数据，使用 JSON 替代 pickle/yaml",
            "hardcoded_secret": "使用环境变量或密钥管理服务存储敏感信息",
            "weak_crypto": "使用强加密算法（AES-256, SHA-256+），避免 MD5/SHA1",
        }
        return recommendations.get(vuln_type, "请根据具体情况修复此安全问题")

    def _prepare_sandbox_files(self, findings: List[Dict]) -> Optional[str]:
        """
        从 findings 中提取项目根目录，用于挂载到沙箱
        
        Returns:
            宿主机项目根目录路径，如果无法确定返回 None
        """
        import os
        
        # 从 tools 中查找 SandboxTool 的 project_root
        for tool in self.tools.values():
            if hasattr(tool, 'project_root') and tool.project_root:
                project_root = os.path.abspath(tool.project_root)
                if os.path.isdir(project_root):
                    logger.info(f"[Verification] 沙箱挂载项目目录: {project_root}")
                    return project_root
        
        # 降级：从 SandboxManager 尝试获取
        logger.warning("[Verification] 无法确定项目根目录，沙箱将使用空环境")
        return None
    
    def _get_sandbox_manager(self):
        """获取 SandboxManager 实例"""
        for tool in self.tools.values():
            if hasattr(tool, 'sandbox_manager'):
                return tool.sandbox_manager
        return None

    async def _run_deterministic_sandbox_commands(
        self, sandbox_commands: List[Dict], sandbox_project_root: Optional[str]
    ) -> None:
        """R3: 进入 LLM 循环前，确定性执行全部预生成 PoC。

        确保每个 finding 都有运行时沙箱证据，不依赖 LLM 是否主动调用 sandbox_exec。
        单条命令失败不影响整体（证据如实记录）；超时受预生成命令自带 timeout 控制。
        """
        if not sandbox_commands:
            return
        sandbox_mgr = self._get_sandbox_manager()
        executed = 0
        for sc in sandbox_commands:
            if self.is_cancelled:
                break
            try:
                cmd_input = sc.get("input") or {}
                command = cmd_input.get("command", "")
                if not command:
                    continue
                timeout = cmd_input.get("timeout", 60)
                if sandbox_mgr and sandbox_project_root:
                    result_dict = await sandbox_mgr.execute_with_files(
                        command=command,
                        host_project_dir=sandbox_project_root,
                        timeout=timeout,
                    )
                    result = self._format_sandbox_result(result_dict)
                else:
                    result = await self.execute_tool("sandbox_exec", cmd_input)

                # 与 LLM 调用路径一致地记录证据与计数
                self._sandbox_exec_calls += 1
                self._sandbox_exec_attempts += 1
                if self._is_sandbox_success(str(result)):
                    self._sandbox_exec_success += 1
                    finding_idx = self._parse_finding_index_from_command(command)
                    if finding_idx is not None:
                        self._verified_finding_indices.add(finding_idx)
                self._record_sandbox_attempt(cmd_input, result, finding_id=sc.get("finding_id"))
                executed += 1
                logger.info(
                    f"[{self.name}] Deterministic sandbox executed ({executed}/{len(sandbox_commands)}): {sc.get('label', '')}"
                )
            except Exception as e:
                logger.warning(
                    f"[{self.name}] Deterministic sandbox exec failed for {sc.get('label', '')}: {e}"
                )
        if executed:
            await self.emit_event(
                "info",
                f"✅ 确定性沙箱执行完成: {executed} 条预生成 PoC 已运行"
            )

    def _build_sandbox_commands(self, findings: List[Dict]) -> List[Dict]:
        """为每个发现自动生成 sandbox_exec 命令，确保 LLM 有可直接执行的沙箱指令"""
        from uuid import uuid4
        commands = []
        for i, f in enumerate(findings):
            vuln_type = (f.get('vulnerability_type') or '').lower()
            file_path = f.get('file_path', 'unknown')
            line = f.get('line_start', 0)
            title = f.get('title', '')

            # Opt-1: Assign a finding_id for precise sandbox-to-finding matching
            finding_id = f.get("id") or str(uuid4())[:8]
            f["_sandbox_finding_id"] = finding_id

            cmd = self._gen_sandbox_command(vuln_type, file_path, line, title, i)
            if cmd:
                # Opt-1: Embed finding_id as comment in the command
                original_command = cmd.get("command", "")
                cmd["command"] = "# FINDING_ID:" + finding_id + "\n" + original_command
                cmd["finding_id"] = finding_id
                commands.append(cmd)
        return commands

    def _gen_sandbox_command(self, vuln_type: str, file_path: str, line: int, title: str, index: int) -> Optional[Dict]:
        """根据漏洞类型生成沙箱验证命令"""
        # C1-fix: 单行化 + 危险字符过滤，防止 heredoc 注入
        # C1b-fix: safe_path 也要去引号（与 safe_title 一致），避免 file_path 含引号时
        # 逃逸 src = '/workspace/src/{file_path}' 的单引号注入任意 Python 语句
        safe_path = re.sub(r"[\r\n]", "", str(file_path))[:200]
        safe_path = safe_path.replace("'", "").replace('"', "")
        safe_title = re.sub(r"[\r\n]", "", title.replace("'", "").replace('"', ''))[:60]
        safe_title = safe_title.replace("POC_EOF", "")
        safe_path = safe_path.replace("POC_EOF", "")
        file_ref = f"{safe_path}:{line}" if line else safe_path

        cmd_templates = {
            'sql_injection': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import os, sys, sqlite3\n"
                    f"print('=== SANDBOX SQL Injection Verification (dynamic) ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"content = ''\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    sink_found = False\n"
                    f"    for kw in ['execute','raw','query','cursor','executescript','sql']:\n"
                    f"        cnt = content.lower().count(kw)\n"
                    f"        if cnt:\n"
                    f"            sink_found = True\n"
                    f"            print(f'  SQL sink \"{{kw}}\": {{cnt}} occurrences')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"print('--- Dynamic verification: in-memory SQLite ---')\n"
                    f"conn = sqlite3.connect(':memory:')\n"
                    f"cur = conn.cursor()\n"
                    f"cur.execute('CREATE TABLE users (id INTEGER, username TEXT, role TEXT)')\n"
                    f"cur.executemany('INSERT INTO users VALUES (?,?,?)', [(1,'admin','admin'),(2,'user','user'),(3,'guest','guest')])\n"
                    f"conn.commit()\n"
                    f"payloads = ['1', \"1' OR '1'='1\", \"1' UNION SELECT 1,2,3--\", \"1'; DROP TABLE users;--\"]\n"
                    f"injectable = False\n"
                    f"for p in payloads:\n"
                    f"    try:\n"
                    f"        q = \"SELECT * FROM users WHERE id = '\" + p + \"'\"\n"
                    f"        cur.execute(q)\n"
                    f"        rows = cur.fetchall()\n"
                    f"        n = len(rows)\n"
                    f"        if p == '1':\n"
                    f"            print(f'  payload={{p!r}} rows={{n}} (baseline)')\n"
                    f"        elif n > 1:\n"
                    f"            injectable = True\n"
                    f"            print(f'  payload={{p!r}} rows={{n}} -> INJECTABLE (returned multiple rows)')\n"
                    f"        else:\n"
                    f"            print(f'  payload={{p!r}} rows={{n}} -> blocked')\n"
                    f"    except Exception as e:\n"
                    f"        print(f'  payload={{p!r}} error={{type(e).__name__}}: {{e}}')\n"
                    f"        if p != '1':\n"
                    f"            print(f'    -> syntax break indicates injection surface')\n"
                    f"if injectable and sink_found:\n"
                    f"    print('VULNERABILITY_CONFIRMED(STATIC): SQL injection pattern verified via in-memory SQLite demo (source-asserted, no data-flow to target)')\n"
                    f"elif content and not sink_found:\n"
                    f"    print('NO_SINK: 目标源码未发现 SQL sink 关键词，演示性确认不成立')\n"
                    f"elif content:\n"
                    f"    print('NOTE: dynamic exec did not confirm; verify SQL sink reaches user input manually')\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'command_injection': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import subprocess, os, re, sys\n"
                    f"print('=== SANDBOX Command Injection Verification (dynamic) ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"content = ''\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    for kw in ['subprocess','os.system','os.popen','eval(','exec(','shell=True']:\n"
                    f"    sink_found = False\n"
                    f"    for kw in ['subprocess','os.system','os.popen','eval(','exec(','shell=True']:\n"
                    f"        cnt = content.count(kw)\n"
                    f"        if cnt:\n"
                    f"            sink_found = True\n"
                    f"            print(f'  Dangerous call \"{{kw}}\": {{cnt}} occurrences')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"print('--- Dynamic verification: shell injection simulation ---')\n"
                    f"user_input = 'hello; id; whoami'\n"
                    f"try:\n"
                    f"    r = subprocess.run('echo ' + user_input, shell=True, capture_output=True, text=True, timeout=5)\n"
                    f"    out = r.stdout\n"
                    f"    print(f'  shell output: {{out.strip()[:200]}}')\n"
                    f"    uid = re.search(r'uid=\\d+', out)\n"
                    f"    if uid and sink_found:\n"
                    f"        print(f'VULNERABILITY_CONFIRMED(STATIC): shell injection demo executed id -> {{uid.group(0)}} (no data-flow to target source)')\n"
                    f"    elif not sink_found:\n"
                    f"        print('NO_SINK: 目标源码未发现命令注入 sink 关键词，演示性确认不成立')\n"
                    f"    else:\n"
                    f"        print('NOTE: shell=True with user input is exploitable; verify sink reaches user input')\n"
                    f"except Exception as e:\n"
                    f"    print(f'  dynamic test error: {{e}}')\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'xss': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import os, re, sys\n"
                    f"print('=== SANDBOX XSS/SSTI Verification (dynamic) ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"content = ''\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    # Step 1: 静态确认 XSS/SSTI 危险 Sink\n"
                    f"    xss_pats = [r'innerHTML', r'v-html', r'dangerouslySetInnerHTML', r'document\\.write', r'outerHTML', r'\\[innerHTML\\]']\n"
                    f"    ssti_pats = [r'render_template_string', r'from_string', r'Markup\\(', r'mark_safe', r'Environment\\(', r'\\|safe']\n"
                    f"    sink_found = False\n"
                    f"    for pat in xss_pats + ssti_pats:\n"
                    f"        ms = list(re.finditer(pat, content))\n"
                    f"        if ms:\n"
                    f"            sink_found = True\n"
                    f"            print(f'  sink {{pat}}: {{len(ms)}} matches')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"print('--- Dynamic verification: Jinja2 SSTI render ---')\n"
                    f"ssti_confirmed = False\n"
                    f"try:\n"
                    f"    from jinja2 import Environment\n"
                    f"    env = Environment(autoescape=False)\n"
                    f"    for probe in ['{{{{7*7}}}}', \"{{{{7*'7'}}}}\", '{{{{config}}}}']:\n"
                    f"        try:\n"
                    f"            rendered = env.from_string(probe).render()\n"
                    f"            if probe == '{{{{7*7}}}}' and rendered.strip() == '49':\n"
                    f"                ssti_confirmed = True\n"
                    f"                print(f'  probe {{probe}} -> {{rendered}} (template engine evaluated expression)')\n"
                    f"            else:\n"
                    f"                print(f'  probe {{probe}} -> {{rendered}}')\n"
                    f"        except Exception as e:\n"
                    f"            print(f'  probe {{probe}} error: {{e}}')\n"
                    f"    if ssti_confirmed and sink_found:\n"
                    f"        print('VULNERABILITY_CONFIRMED(STATIC): SSTI demo verified (Jinja2 rendered {{{{7*7}}}}=49; no data-flow to target source)')\n"
                    f"    elif not sink_found:\n"
                    f"        print('NO_SINK: 目标源码未发现 XSS/SSTI sink 关键词，演示性确认不成立')\n"
                    f"    elif sink_found:\n"
                    f"        print('VULNERABILITY_STATIC_ONLY: dangerous XSS/SSTI sink present; verify it reaches user input')\n"
                    f"    else:\n"
                    f"        print('FALSE_POSITIVE: no XSS/SSTI sink found in source')\n"
                    f"except ImportError:\n"
                    f"    print('NOTE: jinja2 not installed in sandbox, falling back to static analysis')\n"
                    f"    if sink_found:\n"
                    f"        print('VULNERABILITY_STATIC_ONLY: dangerous XSS/SSTI sink present; verify it reaches user input')\n"
                    f"    else:\n"
                    f"        print('FALSE_POSITIVE: no XSS/SSTI sink found in source')\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'path_traversal': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import os, re, sys\n"
                    f"print('=== SANDBOX Path Traversal Verification ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    for pat in [r'os\\.path\\.join', r'\\.\\./', r'open\\(', r'pathlib', r'send_file', r'send_from_directory']:\n"
                    f"        cnt = len(re.findall(pat, content))\n"
                    f"        if cnt: print(f'  Pattern \"{{pat}}\": {{cnt}} matches')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"paths = ['/etc/passwd', '../../../etc/passwd', '....//....//etc/passwd']\n"
                    f"for p in paths: print(f'Testing path: {{p}}, exists={{os.path.exists(p)}}')\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'ssrf': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import os, re, sys, urllib.request\n"
                    f"print('=== SANDBOX SSRF Verification (enhanced) ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"content = ''\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    for pat in [r'requests\\.get', r'httpx', r'urllib', r'aiohttp', r'fetch\\(']:\n"
                    f"        cnt = len(re.findall(pat, content))\n"
                    f"        if cnt: print(f'  HTTP call \"{{pat}}\": {{cnt}} matches')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"# 检查源码是否对用户输入 URL 做过滤/校验\n"
                    f"has_filter = bool(re.search(r'valid|filter|sanitiz|allowlist|blocklist|urlparse', content, re.I))\n"
                    f"print(f'URL filter/validate in source: {{has_filter}}')\n"
                    f"# 动态探测：实际请求云元数据地址（需 network_enabled=True + bridge）\n"
                    f"metadata_hit = False\n"
                    f"try:\n"
                    f"    r = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/', timeout=5)\n"
                    f"    body = r.read(200).decode(errors='ignore')\n"
                    f"    print(f'metadata endpoint: HTTP {{r.getcode()}}, body={{body[:80]!r}}')\n"
                    f"    metadata_hit = True\n"
                    f"except Exception as e:\n"
                    f"    print(f'metadata endpoint: blocked - {{type(e).__name__}}: {{e}}')\n"
                    f"# 判定\n"
                    f"sink_found = bool(re.search(r'requests\\.get|httpx|urllib|aiohttp|fetch\\(', content))\n"
                    f"if metadata_hit:\n"
                    f"    print('VULNERABILITY_CONFIRMED(STATIC): SSRF demo - cloud metadata reachable via PoC-initiated request (no data-flow to target source)')\n"
                    f"elif sink_found and not has_filter:\n"
                    f"    # bridge 不可用降级：未联网但源码存在未过滤的 HTTP 调用 sink\n"
                    f"    print('STATIC_CONFIRMED: SSRF sink present without URL filter/validate; degraded 检查 URL 解析逻辑 (bridge unavailable, 未真实联网)')\n"
                    f"elif sink_found and has_filter:\n"
                    f"    print('FALSE_POSITIVE: HTTP call sink present but URL filter/validate found in source')\n"
                    f"else:\n"
                    f"    print('FALSE_POSITIVE: no SSRF sink found in source')\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
                'network_enabled': True,
            },
            'auth_missing': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import http.server, threading, urllib.request, socket, time\n"
                    f"print('=== SANDBOX Auth Missing Verification ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    sink_found = False\n"
                    f"    for kw in ['@app.get','@RequestMapping','@GetMapping','@PostMapping','@PutMapping','@DeleteMapping','login_required','@Secured','@PreAuthorize']:\n"
                    f"        cnt = content.count(kw)\n"
                    f"        if cnt:\n"
                    f"            sink_found = True\n"
                    f"            print(f'  Endpoint/auth pattern \"{{kw}}\": {{cnt}} occurrences')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"# 容器内 loopback mock：无认证的敏感接口（不受 network_mode 限制）\n"
                    f"sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                    f"sock.bind(('127.0.0.1', 0))\n"
                    f"port = sock.getsockname()[1]\n"
                    f"sock.close()\n"
                    f"print(f'mock server port: {{port}}')\n"
                    f"class Handler(http.server.BaseHTTPRequestHandler):\n"
                    f"    def do_GET(self):\n"
                    f"        if self.path == '/api/users':\n"
                    f"            self.send_response(200)\n"
                    f"            self.end_headers()\n"
                    f"            self.wfile.write(b'{{\"users\": [{{\"id\":1,\"email\":\"admin@x.com\"}}]}}')\n"
                    f"        else:\n"
                    f"            self.send_response(404)\n"
                    f"            self.end_headers()\n"
                    f"    def log_message(self, *a): pass\n"
                    f"http.server.HTTPServer.allow_reuse_address = True\n"
                    f"try:\n"
                    f"    srv = http.server.HTTPServer(('127.0.0.1', port), Handler)\n"
                    f"    t = threading.Thread(target=srv.serve_forever, daemon=True)\n"
                    f"    t.start()\n"
                    f"    time.sleep(0.5)\n"
                    f"    # 发无凭证请求断言能读敏感数据\n"
                    f"    req = urllib.request.Request(f'http://127.0.0.1:{{port}}/api/users')\n"
                    f"    resp = urllib.request.urlopen(req, timeout=5)\n"
                    f"    body = resp.read().decode()\n"
                    f"    print(f'no-auth request: HTTP {{resp.getcode()}}, body={{body[:80]!r}}')\n"
                    f"    if resp.getcode() == 200 and 'users' in body and sink_found:\n"
                    f"        print('VULNERABILITY_CONFIRMED(STATIC): 无认证即可访问敏感接口 (loopback mock 演示，与目标源码无数据流因果)')\n"
                    f"    elif not sink_found:\n"
                    f"        print('NO_SINK: 目标源码未发现接口定义模式，无认证访问断言不成立')\n"
                    f"    else:\n"
                    f"        print('FALSE_POSITIVE: 无凭证请求被拒绝或无敏感数据')\n"
                    f"except Exception as e:\n"
                    f"    print(f'mock test error: {{type(e).__name__}}: {{e}}')\n"
                    f"finally:\n"
                    # P3-6: 沙箱 PoC 模板里的裸 except:pass 会吞 KeyboardInterrupt；
                    # 改成显式 Exception，让 Ctrl+C 能中断卡死的 PoC。
                    f"    try: srv.shutdown()\n"
                    f"    except Exception: pass\n"
                    f"    try: srv.server_close()\n"
                    f"    except Exception: pass\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'tenant_isolation': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import http.server, threading, urllib.request, socket, time\n"
                    f"print('=== SANDBOX Tenant Isolation Verification ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    sink_found = False\n"
                    f"    for kw in ['@RequestMapping','@GetMapping','@PostMapping','tenant','X-Tenant','user_id','@Secured']:\n"
                    f"        cnt = content.count(kw)\n"
                    f"        if cnt:\n"
                    f"            sink_found = True\n"
                    f"            print(f'  Tenant/endpoint pattern \"{{kw}}\": {{cnt}} occurrences')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"# 容器内 loopback mock：多租户数据，不校验租户归属\n"
                    f"sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                    f"sock.bind(('127.0.0.1', 0))\n"
                    f"port = sock.getsockname()[1]\n"
                    f"sock.close()\n"
                    f"print(f'mock server port: {{port}}')\n"
                    f"TENANT_A_DATA = b'{{\"tenant\":\"A\",\"secrets\":[\"a-key-123\"]}}'\n"
                    f"class Handler(http.server.BaseHTTPRequestHandler):\n"
                    f"    def do_GET(self):\n"
                    f"        # 不校验 X-Tenant 头，直接返回 A 租户数据\n"
                    f"        if self.path.startswith('/api/data'):\n"
                    f"            self.send_response(200)\n"
                    f"            self.end_headers()\n"
                    f"            self.wfile.write(TENANT_A_DATA)\n"
                    f"        else:\n"
                    f"            self.send_response(404)\n"
                    f"            self.end_headers()\n"
                    f"    def log_message(self, *a): pass\n"
                    f"http.server.HTTPServer.allow_reuse_address = True\n"
                    f"try:\n"
                    f"    srv = http.server.HTTPServer(('127.0.0.1', port), Handler)\n"
                    f"    t = threading.Thread(target=srv.serve_forever, daemon=True)\n"
                    f"    t.start()\n"
                    f"    time.sleep(0.5)\n"
                    f"    # 模拟 B 租户带 X-Tenant:B 头请求，断言能读 A 租户数据\n"
                    f"    req = urllib.request.Request(f'http://127.0.0.1:{{port}}/api/data')\n"
                    f"    req.add_header('X-Tenant', 'B')\n"
                    f"    resp = urllib.request.urlopen(req, timeout=5)\n"
                    f"    body = resp.read().decode()\n"
                    f"    print(f'tenant B request: HTTP {{resp.getcode()}}, body={{body[:80]!r}}')\n"
                    f"    if resp.getcode() == 200 and 'a-key-123' in body and sink_found:\n"
                    f"        print('VULNERABILITY_CONFIRMED(STATIC): 多租户隔离失效 (loopback mock 演示，与目标源码无数据流因果)')\n"
                    f"    elif not sink_found:\n"
                    f"        print('NO_SINK: 目标源码未发现租户/接口模式，跨租户断言不成立')\n"
                    f"    else:\n"
                    f"        print('FALSE_POSITIVE: 租户隔离生效，跨租户数据不可读')\n"
                    f"except Exception as e:\n"
                    f"    print(f'mock test error: {{type(e).__name__}}: {{e}}')\n"
                    f"finally:\n"
                    f"    try: srv.shutdown()\n"
                    f"    except: pass\n"
                    f"    try: srv.server_close()\n"
                    f"    except: pass\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'idor': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import http.server, threading, urllib.request, socket, time, json\n"
                    f"print('=== SANDBOX IDOR Verification ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    sink_found = False\n"
                    f"    for kw in ['@RequestMapping','@GetMapping','@PostMapping','@PathVariable','user_id','id =','@Secured','@PreAuthorize']:\n"
                    f"        cnt = content.count(kw)\n"
                    f"        if cnt:\n"
                    f"            sink_found = True\n"
                    f"            print(f'  Resource/endpoint pattern \"{{kw}}\": {{cnt}} occurrences')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"# 容器内 loopback mock：CRUD 接口不校验资源归属\n"
                    f"sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                    f"sock.bind(('127.0.0.1', 0))\n"
                    f"port = sock.getsockname()[1]\n"
                    f"sock.close()\n"
                    f"print(f'mock server port: {{port}}')\n"
                    f"class Handler(http.server.BaseHTTPRequestHandler):\n"
                    f"    def do_GET(self):\n"
                    f"        # /api/user/<id> 不校验当前用户是否有权访问该 id\n"
                    f"        if self.path.startswith('/api/user/'):\n"
                    f"            uid = self.path.split('/')[-1]\n"
                    f"            self.send_response(200)\n"
                    f"            self.end_headers()\n"
                    f"            self.wfile.write(json.dumps({{'id': uid, 'email': 'user' + uid + '@x.com', 'role': 'admin'}}).encode())\n"
                    f"        else:\n"
                    f"            self.send_response(404)\n"
                    f"            self.end_headers()\n"
                    f"    def log_message(self, *a): pass\n"
                    f"http.server.HTTPServer.allow_reuse_address = True\n"
                    f"try:\n"
                    f"    srv = http.server.HTTPServer(('127.0.0.1', port), Handler)\n"
                    f"    t = threading.Thread(target=srv.serve_forever, daemon=True)\n"
                    f"    t.start()\n"
                    f"    time.sleep(0.5)\n"
                    f"    # 越权：普通用户请求他人 id=999 的资源\n"
                    f"    req = urllib.request.Request(f'http://127.0.0.1:{{port}}/api/user/999')\n"
                    f"    req.add_header('X-User-Role', 'guest')\n"
                    f"    resp = urllib.request.urlopen(req, timeout=5)\n"
                    f"    body = resp.read().decode()\n"
                    f"    print(f'guest request user/999: HTTP {{resp.getcode()}}, body={{body[:80]!r}}')\n"
                    f"    if resp.getcode() == 200 and 'user999' in body and sink_found:\n"
                    f"        print('VULNERABILITY_CONFIRMED(STATIC): IDOR 越权可访问他人资源 (loopback mock 演示，与目标源码无数据流因果)')\n"
                    f"    elif not sink_found:\n"
                    f"        print('NO_SINK: 目标源码未发现资源/接口模式，越权断言不成立')\n"
                    f"    else:\n"
                    f"        print('FALSE_POSITIVE: 资源归属校验生效，越权不可读')\n"
                    f"except Exception as e:\n"
                    f"    print(f'mock test error: {{type(e).__name__}}: {{e}}')\n"
                    f"finally:\n"
                    f"    try: srv.shutdown()\n"
                    f"    except: pass\n"
                    f"    try: srv.server_close()\n"
                    f"    except: pass\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'hardcoded_secret': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import os, re, sys\n"
                    f"print('=== SANDBOX Hardcoded Secret Verification ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    patterns = [\n"
                    f"        r'(?i)(api[_-]?key|secret|password|token)\\s*[=:]\\s*[\"\\'][^\"\\']{{8,}}',\n"
                    f"        r'(?i)AKIA[0-9A-Z]{{16}}',\n"
                    f"        r'-----BEGIN (RSA |EC )?PRIVATE KEY-----',\n"
                    f"    ]\n"
                    f"    for pat in patterns:\n"
                    f"        matches = re.findall(pat, content)\n"
                    f"        if matches: print(f'  Secret pattern found: {{len(matches)}} matches')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
            'deserialization': {
                'command': (
                    f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                    f"import os, re, sys, pickle, json\n"
                    f"print('=== SANDBOX Deserialization Verification ===')\n"
                    f"print('Target: {file_ref}')\n"
                    f"print('Title: {safe_title}')\n"
                    f"src = '/workspace/src/{safe_path}'\n"
                    f"if os.path.exists(src):\n"
                    f"    with open(src) as f: content = f.read()\n"
                    f"    print(f'Source: {{len(content)}} chars loaded')\n"
                    f"    for pat in [r'pickle\\.load', r'yaml\\.load', r'json\\.loads', r'marshal\\.load', r'eval\\(']:\n"
                    f"        cnt = len(re.findall(pat, content))\n"
                    f"        if cnt: print(f'  Unsafe call \"{{pat}}\": {{cnt}} occurrences')\n"
                    f"else:\n"
                    f"    print(f'Source not found: {{src}}')\n"
                    f"    sys.exit(1)\n"
                    f"safe_data = json.dumps({{'test': 'data'}})\n"
                    f"print(f'JSON safe: {{json.loads(safe_data)}}')\n"
                    f"print(f'pickle available: True')\n"
                    f"print('=== Verification Complete ===')\n"
                    f"POC_EOF\n"
                    f"python3 /tmp/poc_{index}.py"
                ),
                'timeout': 30,
            },
        }

        matched = None
        # I3-fix: 精确匹配优先，避免子串截胡
        if vuln_type in cmd_templates:
            matched = {'label': f'发现{index+1}: {vuln_type} ({file_ref})', 'input': dict(cmd_templates[vuln_type])}
        if not matched:
            for key, cmd in cmd_templates.items():
                if key in vuln_type:
                    matched = {'label': f'发现{index+1}: {key} ({file_ref})', 'input': dict(cmd)}
                    break

        if not matched:
            matched = {
                'label': f'发现{index+1}: {vuln_type} ({file_ref})',
                'input': {
                    'command': (
                        f"cat > /tmp/poc_{index}.py << 'POC_EOF'\n"
                        f"import os, re, sys\n"
                        f"print('=== SANDBOX Vulnerability Verification ===')\n"
                        f"print('Target: {file_ref}')\n"
                        f"print('Type: {vuln_type}')\n"
                        f"print('Title: {safe_title}')\n"
                        f"src = '/workspace/src/{safe_path}'\n"
                        f"if os.path.exists(src):\n"
                        f"    with open(src) as f: content = f.read()\n"
                        f"    print(f'Source: {{len(content)}} chars loaded')\n"
                        f"else:\n"
                        f"    print(f'Source not found: {{src}}')\n"
                        f"    sys.exit(1)\n"
                        f"print('=== Verification Complete ===')\n"
                        f"POC_EOF\n"
                        f"python3 /tmp/poc_{index}.py"
                    ),
                    'timeout': 30,
                }
            }

        return matched

    def _format_sandbox_result(self, result_dict: Dict[str, Any]) -> str:
        """将 execute_with_files 的结果格式化为与 execute_tool 一致的字符串"""
        parts = ["沙箱执行结果\n"]
        parts.append(f"退出码: {result_dict.get('exit_code', -1)}")
        if result_dict.get("stdout"):
            parts.append(f"\n标准输出:\n```\n{result_dict['stdout'][:5000]}\n```")
        if result_dict.get("stderr"):
            parts.append(f"\n标准错误:\n```\n{result_dict['stderr'][:2000]}\n```")
        if result_dict.get("error"):
            parts.append(f"\n错误: {result_dict['error']}")
        return "\n".join(parts)

    def _deduplicate(self, findings: List[Dict]) -> List[Dict]:
        """去重"""
        seen = set()
        unique = []
        
        for f in findings:
            key = (
                f.get("file_path", ""),
                f.get("line_start", 0),
                f.get("vulnerability_type", ""),
            )
            
            if key not in seen:
                seen.add(key)
                unique.append(f)
        
        return unique
    
    @staticmethod
    def _is_valid_finding(finding: dict[str, Any]) -> bool:
        """Reject entries that are observations/descriptions, not real findings.

        A valid finding must:
        - Have a non-empty file_path that looks like a real path (not unknown/N/A)
        - Have a line_start > 0
        - Have a non-empty vulnerability_type
        - Title/description must NOT match descriptive-only patterns
        """
        return is_strict_finding(finding)

    def get_conversation_history(self) -> List[Dict[str, str]]:
        """获取对话历史"""
        return self._conversation_history

    def get_steps(self) -> List[VerificationStep]:
        """获取执行步骤"""
        return self._steps

    def _create_verification_handoff(
        self,
        verified_findings: List[Dict[str, Any]],
        confirmed_count: int,
        false_positive_count: int,
        needs_context_count: int,
    ) -> TaskHandoff:
        """
        创建 Verification Agent 的任务交接信息

        Args:
            verified_findings: 验证后的发现列表
            confirmed_count: 确认的漏洞数量
            false_positive_count: 误报数量
            needs_context_count: 需上下文数量

        Returns:
            TaskHandoff 对象，供 Orchestrator 汇总
        """
        # 按验证结果分类
        confirmed = [f for f in verified_findings if f.get("verification_status") == VerificationStatus.CONFIRMED]
        false_positives = [f for f in verified_findings if f.get("verification_status") == VerificationStatus.FALSE_POSITIVE]
        not_reproducible = [f for f in verified_findings if f.get("verification_status") == VerificationStatus.NOT_REPRODUCIBLE]

        # 提取关键发现（已确认的高危漏洞）
        key_findings = []
        for f in confirmed:
            if f.get("severity") in ["critical", "high"]:
                key_findings.append(f)
        # 如果高危不够，添加其他确认的漏洞
        if len(key_findings) < 10:
            for f in confirmed:
                if f not in key_findings:
                    key_findings.append(f)
                    if len(key_findings) >= 10:
                        break

        # 构建建议行动 - 修复建议
        suggested_actions = []
        for f in confirmed[:10]:
            suggestion = f.get("suggestion", "") or f.get("recommendation", "")
            suggested_actions.append({
                "action": "fix_vulnerability",
                "target": f.get("file_path", ""),
                "line": f.get("line_start", 0),
                "vulnerability_type": f.get("vulnerability_type", "unknown"),
                "severity": f.get("severity", "medium"),
                "recommendation": suggestion[:200] if suggestion else "请根据漏洞类型进行修复"
            })

        # 构建洞察
        insights = [
            f"验证完成: {confirmed_count}个确认, {false_positive_count}个误报, {needs_context_count}个需上下文",
            f"验证准确率: {confirmed_count / len(verified_findings) * 100:.1f}%" if verified_findings else "无数据",
        ]

        # 统计各类型漏洞
        type_counts = {}
        for f in confirmed:
            vtype = f.get("vulnerability_type", "unknown")
            type_counts[vtype] = type_counts.get(vtype, 0) + 1
        if type_counts:
            top_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            insights.append(f"主要漏洞类型: {', '.join([f'{t}({c})' for t, c in top_types])}")

        # 需要关注的文件（有确认漏洞的文件）
        attention_points = []
        files_with_confirmed = {}
        for f in confirmed:
            fp = f.get("file_path", "")
            if fp:
                files_with_confirmed[fp] = files_with_confirmed.get(fp, 0) + 1
        for fp, count in sorted(files_with_confirmed.items(), key=lambda x: x[1], reverse=True)[:10]:
            attention_points.append(f"{fp} ({count}个确认漏洞)")

        # 优先修复的区域
        priority_areas = []
        for f in confirmed:
            if f.get("severity") in ["critical", "high"]:
                fp = f.get("file_path", "")
                if fp and fp not in priority_areas:
                    priority_areas.append(fp)

        # 上下文数据
        context_data = {
            "confirmed_count": confirmed_count,
            "false_positive_count": false_positive_count,
            "needs_context_count": needs_context_count,
            "not_reproducible_count": len(not_reproducible),
            "vulnerability_types": type_counts,
            "files_with_confirmed": files_with_confirmed,
            "poc_generated": len([f for f in verified_findings if f.get("poc_code")]),
        }

        # 构建摘要
        summary = f"验证完成: {confirmed_count}个确认漏洞"
        if false_positive_count > 0:
            summary += f", {false_positive_count}个误报"
        if needs_context_count > 0:
            summary += f", {needs_context_count}个需上下文"
        if confirmed_count > 0:
            high_count = len([f for f in confirmed if f.get("severity") in ["critical", "high"]])
            if high_count > 0:
                summary += f", 其中{high_count}个高危"

        return self.create_handoff(
            to_agent="orchestrator",
            summary=summary,
            key_findings=key_findings,
            suggested_actions=suggested_actions,
            attention_points=attention_points,
            priority_areas=priority_areas,
            context_data=context_data,
        )

