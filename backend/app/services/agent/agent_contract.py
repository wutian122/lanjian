"""Agent 合约提示词与输出校验工具。"""

from __future__ import annotations

from dataclasses import dataclass


AGENT_HEADER_START = "<!-- AGENT_HEADER_START -->"
AGENT_OUTPUT_END = "<!-- AGENT_OUTPUT_END -->"


@dataclass(frozen=True)
class AgentContract:
    max_iterations: int
    turn_reserve: int = 3
    max_sink_categories: int = 8
    max_instances_per_sink_category: int = 3
    min_call_chain_depth: int = 3

    @property
    def reserve_threshold(self) -> int:
        return max(1, self.max_iterations - self.turn_reserve)

    _ENHANCED_CONTRACT = """

## 增强合约（自动注入）
1. ★ Turn 预留: 当你已使用的轮次接近上限时（剩余 ≤3 轮），必须立即停止探索，产出结构化 Final Answer。
2. 搜索策略: 先用 search_code 定位行号，再用 read_file 的 offset/limit 读上下文（±20行）。禁止一次读取整个大文件。
3. 同类漏洞 ≥5 个时合并报告，列出文件清单而非逐个深挖。
4. Sink 类别上界: 每个漏洞维度最多追踪 8 个 Sink 类别，每类最多深追 3 个实例。
5. ★ 截断防御: Final Answer 的 JSON 必须以 {"findings": [...]} 开头，确保即使输出被截断，已有 findings 不会丢失。
"""

    def to_prompt(self, *, include_enhanced: bool = True) -> str:
        base = f"""
<agent_contract>
## Agent 合约（强制）

### Turn 预留
- 当前最大迭代数：{self.max_iterations}
- 当已用迭代数 >= {self.reserve_threshold} 时，必须停止新的搜索方向，立即汇总已有证据并输出结构化结果。
- 最后 {self.turn_reserve} 轮只允许整理、验证和输出，不允许开启新的泛搜索。

### 截断防御
- 最终输出必须以 `{AGENT_HEADER_START}` 开头。
- 最终输出必须以 `{AGENT_OUTPUT_END}` 结尾。
- HEADER 中必须包含：覆盖维度、已读文件、已执行搜索、发现数量、仍需补漏方向。

### 数据转换管道追踪
- 高危和严重漏洞必须追踪 Source → Transform → Sink。
- 每条关键链路至少追踪 {self.min_call_chain_depth} 层调用或数据转换；无法追满时必须降级置信度并说明缺口。

### 探索上界
- 每个安全维度最多分析 {self.max_sink_categories} 类 Sink。
- 每类 Sink 最多深追 {self.max_instances_per_sink_category} 个实例。
- 同 pattern 多文件出现时应合并报告，不要逐个展开。
</agent_contract>
""".strip()
        if include_enhanced:
            base = base.replace(
                "</agent_contract>",
                f"{self._ENHANCED_CONTRACT}\n</agent_contract>",
            )
        return base


def inject_agent_contract(system_prompt: str | None, max_iterations: int) -> str:
    base_prompt = system_prompt or ""
    if "<agent_contract>" in base_prompt:
        return base_prompt
    return f"{base_prompt}\n\n{AgentContract(max_iterations=max_iterations).to_prompt()}".strip()


def has_output_sentinel(output: str | None) -> bool:
    return bool(output and AGENT_HEADER_START in output and AGENT_OUTPUT_END in output)
