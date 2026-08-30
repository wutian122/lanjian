"""
智能上下文管理器

功能：
1. 上下文压缩：将长内容压缩为精简摘要
2. 滑动窗口：保留最近 N 轮对话
3. 重要性评分：保留关键信息，删除冗余内容
4. 完整归档：压缩前的完整内容保存到追踪文件
"""

import asyncio
import logging
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ContextWindow:
    """上下文窗口配置"""
    max_messages: int = 30  # 最多保留消息数
    max_total_length: int = 100_000  # 最大总字符数
    compression_threshold: int = 50_000  # 超过此长度触发压缩
    keep_recent: int = 5  # 始终保留最近 N 条消息


class ContextManager:
    """智能上下文管理器"""

    def __init__(
        self,
        llm_service,
        trace_manager=None,
        window_config: Optional[ContextWindow] = None
    ):
        self.llm_service = llm_service
        self.trace_manager = trace_manager
        self.config = window_config or ContextWindow()

    async def compress_if_needed(
        self,
        conversation_history: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """
        根据需要压缩上下文

        策略：
        1. 计算总长度
        2. 如果超过阈值，执行压缩
        3. 保留最近的消息 + 系统消息
        4. 中间部分压缩为摘要
        """
        if not conversation_history:
            return conversation_history

        total_length = sum(len(msg.get('content', '')) for msg in conversation_history)

        if total_length < self.config.compression_threshold:
            return conversation_history

        logger.info(f"[ContextManager] 触发上下文压缩: {total_length} 字符")

        # 分离系统消息、最近消息、中间消息
        system_messages = []
        recent_messages = []
        middle_messages = []

        for i, msg in enumerate(conversation_history):
            if msg.get('role') == 'system':
                system_messages.append(msg)
            elif i >= len(conversation_history) - self.config.keep_recent:
                recent_messages.append(msg)
            else:
                middle_messages.append(msg)

        # 压缩中间部分
        if middle_messages:
            compressed_summary = await self._compress_messages(middle_messages)

            # 记录压缩
            if self.trace_manager:
                original_content = "\n\n".join([
                    f"[{msg['role']}] {msg['content']}" for msg in middle_messages
                ])
                self.trace_manager.add_context_compression(
                    original_length=len(original_content),
                    compressed_length=len(compressed_summary),
                    compression_method="llm_summarization",
                    summary=compressed_summary,
                    full_content_ref=original_content
                )

            # 重构对话历史
            compressed_history = system_messages + [
                {
                    "role": "user",
                    "content": f"[历史上下文摘要]\n{compressed_summary}\n[以下是最近的对话]"
                }
            ] + recent_messages

            logger.info(
                f"[ContextManager] 压缩完成: {len(conversation_history)} 条 → "
                f"{len(compressed_history)} 条, {total_length} → "
                f"{sum(len(m.get('content', '')) for m in compressed_history)} 字符"
            )

            return compressed_history

        return conversation_history

    async def _compress_messages(self, messages: List[Dict[str, str]]) -> str:
        """
        使用 LLM 压缩消息列表为摘要

        提示 LLM 提取：
        - 关键决策
        - 重要发现
        - 工具调用结果
        - 错误和警告
        """
        content_to_compress = []

        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')

            # 截取过长内容
            if len(content) > 2000:
                content = content[:2000] + f"...[截断，原长度 {len(content)}]"

            content_to_compress.append(f"**{role.upper()}**: {content}")

        combined = "\n\n".join(content_to_compress)

        prompt = f"""请将以下对话历史压缩为简洁的摘要，保留所有关键信息：

{combined}

要求：
1. 提取所有重要的决策和发现
2. 记录所有工具调用及其结果
3. 保留所有错误和警告信息
4. 删除冗余和重复内容
5. 使用列表格式，每项一行

摘要："""

        try:
            response = await self.llm_service.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
            )

            summary = response.get('content', '压缩失败')
            return summary

        except Exception as e:
            logger.error(f"[ContextManager] 压缩失败: {e}")
            # 降级：简单截断
            return self._simple_truncate(messages)

    def _simple_truncate(self, messages: List[Dict[str, str]]) -> str:
        """简单截断策略（降级方案）"""
        summary_parts = []

        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')

            # 提取第一句话或前100字符
            first_sentence = content.split('\n')[0][:100]
            summary_parts.append(f"- [{role}] {first_sentence}")

        return "\n".join(summary_parts)

    def apply_sliding_window(
        self,
        conversation_history: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """
        应用滑动窗口策略

        保留：
        - 所有系统消息
        - 最近 N 条消息
        """
        if len(conversation_history) <= self.config.max_messages:
            return conversation_history

        logger.info(
            f"[ContextManager] 应用滑动窗口: {len(conversation_history)} 条 → "
            f"{self.config.max_messages} 条"
        )

        system_messages = [msg for msg in conversation_history if msg.get('role') == 'system']
        recent_messages = [
            msg for msg in conversation_history[-self.config.max_messages:]
            if msg.get('role') != 'system'
        ]

        return system_messages + recent_messages

    def extract_key_findings(self, conversation_history: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        从对话历史中提取关键发现

        用于生成审计报告或供其他 Agent 查阅
        """
        findings = []

        for msg in conversation_history:
            content = msg.get('content', '')

            # 匹配漏洞发现模式
            if '漏洞' in content or 'vulnerability' in content.lower():
                # 简单提取（实际项目可以用 NLP 或正则）
                findings.append({
                    "role": msg.get('role'),
                    "snippet": content[:200],
                    "full_content": content
                })

        return findings

    async def compress_agent_output(
        self,
        agent_name: str,
        output: str,
        max_length: int = 10_000
    ) -> str:
        """
        压缩子 Agent 输出

        当子 Agent（如 Analysis）返回大量内容时，
        压缩为摘要后再传递给 Orchestrator
        """
        if len(output) <= max_length:
            return output

        logger.info(
            f"[ContextManager] 压缩 {agent_name} 输出: "
            f"{len(output)} → 目标 {max_length} 字符"
        )

        # 提取结构化部分（如果存在）
        findings = self._extract_findings_from_output(output)
        recommendations = self._extract_recommendations_from_output(output)

        # 使用 LLM 压缩剩余内容
        prompt = f"""请将以下 {agent_name} Agent 的输出压缩为不超过 {max_length // 10} 字的摘要：

{output[:5000]}...

要求：
1. 保留所有发现的漏洞（类型、位置、严重程度）
2. 保留关键的分析结论
3. 删除详细的代码片段和日志
4. 使用列表格式

摘要："""

        try:
            response = await self.llm_service.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2000,
            )

            compressed = response.get('content', '')

            # 重新组装
            result = f"""## {agent_name} Agent 输出摘要

{compressed}

### 发现的漏洞（{len(findings)}个）
{self._format_findings(findings)}

### 建议
{recommendations}

---
*原始输出长度: {len(output)} 字符，已压缩为 {len(result)} 字符*
*完整输出已归档到追踪文件*
"""

            # 记录到追踪文件
            if self.trace_manager:
                self.trace_manager.add_context_compression(
                    original_length=len(output),
                    compressed_length=len(result),
                    compression_method=f"{agent_name}_output_compression",
                    summary=result,
                    full_content_ref=output
                )

            return result

        except Exception as e:
            logger.error(f"[ContextManager] 压缩 {agent_name} 输出失败: {e}")
            # 降级：简单截断
            return output[:max_length] + f"\n\n[截断，原长度 {len(output)}]"

    def _extract_findings_from_output(self, output: str) -> List[Dict[str, Any]]:
        """从输出中提取漏洞发现"""
        findings = []

        # 简单模式匹配（实际项目应该更精确）
        lines = output.split('\n')
        for i, line in enumerate(lines):
            if any(keyword in line for keyword in ['发现', '漏洞', 'vulnerability', 'finding']):
                findings.append({
                    "line": i,
                    "content": line,
                    "context": '\n'.join(lines[max(0, i-2):min(len(lines), i+3)])
                })

        return findings

    def _extract_recommendations_from_output(self, output: str) -> str:
        """从输出中提取建议"""
        # 简单提取包含"建议"的段落
        lines = output.split('\n')
        recommendations = []

        for line in lines:
            if '建议' in line or 'recommend' in line.lower():
                recommendations.append(line)

        return '\n'.join(recommendations[:5]) if recommendations else "无"

    def _format_findings(self, findings: List[Dict[str, Any]]) -> str:
        """格式化漏洞列表"""
        if not findings:
            return "无"

        formatted = []
        for i, f in enumerate(findings[:10], 1):
            formatted.append(f"{i}. {f.get('content', '')[:100]}")

        if len(findings) > 10:
            formatted.append(f"... 还有 {len(findings) - 10} 个发现")

        return '\n'.join(formatted)
