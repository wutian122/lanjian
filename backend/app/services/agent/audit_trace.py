"""
审计追踪文件系统

为每个审计任务生成独立的 Markdown 追踪文件，记录：
- 任务执行关键节点（Agent 调度、工具调用、发现漏洞）
- 嵌入模型索引记录
- 上下文压缩历史（保留完整原文引用）
- LLM 调用统计
- 时间线与决策链路

用途：
1. AI 在执行任务时查阅历史决策
2. 长上下文场景下的知识检索（替代完整上下文）
3. 人工审查与调试
4. 任务恢复时的状态重建
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    """追踪条目"""
    timestamp: str
    type: str  # agent_dispatch, tool_call, finding, embedding, context_compression, llm_call
    title: str
    content: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


class AuditTraceManager:
    """审计追踪管理器"""

    def __init__(self, task_id: str, project_name: str, base_dir: str = "./audit_traces"):
        self.task_id = task_id
        self.project_name = project_name
        self.base_dir = Path(base_dir)
        self.task_dir = self.base_dir / task_id[:8]
        self.task_dir.mkdir(parents=True, exist_ok=True)

        # 追踪文件路径
        self.trace_md = self.task_dir / "audit_trace.md"
        self.trace_json = self.task_dir / "audit_trace.json"
        self.context_archive = self.task_dir / "context_archive.json"

        # 内存缓存
        self.entries: List[TraceEntry] = []
        self.context_snapshots: List[Dict[str, Any]] = []

        # 统计信息
        self.stats = {
            "agents_dispatched": 0,
            "tools_called": 0,
            "findings_discovered": 0,
            "embeddings_created": 0,
            "contexts_compressed": 0,
            "llm_calls": 0,
            "total_tokens": 0,
        }

        # 初始化追踪文件
        self._initialize_trace_file()

    def _initialize_trace_file(self):
        """初始化追踪文件"""
        if not self.trace_md.exists():
            header = f"""# 审计追踪报告

**任务ID**: `{self.task_id}`
**项目**: {self.project_name}
**创建时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}

---

## 📋 任务概览

| 指标 | 数值 |
|------|------|
| Agent 调度次数 | 0 |
| 工具调用次数 | 0 |
| 发现漏洞数 | 0 |
| 嵌入向量数 | 0 |
| 上下文压缩次数 | 0 |
| LLM 调用次数 | 0 |
| Token 消耗 | 0 |

---

## 🕐 执行时间线

"""
            self.trace_md.write_text(header, encoding="utf-8")

    def add_agent_dispatch(
        self,
        agent_name: str,
        task: str,
        context: str = "",
        parent_agent: str = "Orchestrator"
    ):
        """记录 Agent 调度"""
        self.stats["agents_dispatched"] += 1

        entry = TraceEntry(
            timestamp=self._now(),
            type="agent_dispatch",
            title=f"调度 {agent_name} Agent",
            content={
                "agent": agent_name,
                "task": task,
                "context": context[:200] + "..." if len(context) > 200 else context,
                "parent": parent_agent,
            },
            metadata={"full_context_length": len(context)}
        )

        self.entries.append(entry)
        self._append_to_markdown(f"""
### 🤖 [{self._now()}] 调度 {agent_name} Agent

**任务**: {task}
**父 Agent**: {parent_agent}
**上下文长度**: {len(context)} 字符

""")

    def add_tool_call(
        self,
        tool_name: str,
        input_params: Dict[str, Any],
        output: Any,
        duration_ms: int,
        success: bool = True
    ):
        """记录工具调用"""
        self.stats["tools_called"] += 1

        entry = TraceEntry(
            timestamp=self._now(),
            type="tool_call",
            title=f"调用工具: {tool_name}",
            content={
                "tool": tool_name,
                "input": input_params,
                "output": str(output)[:500] + "..." if len(str(output)) > 500 else str(output),
                "duration_ms": duration_ms,
                "success": success,
            }
        )

        self.entries.append(entry)

        status_emoji = "✅" if success else "❌"
        self._append_to_markdown(f"""
### 🔧 [{self._now()}] {status_emoji} 工具调用: {tool_name}

**耗时**: {duration_ms}ms
**输入**: `{json.dumps(input_params, ensure_ascii=False, indent=2)[:200]}`
**输出**: {str(output)[:300]}{'...' if len(str(output)) > 300 else ''}

""")

    def add_finding(
        self,
        finding_type: str,
        severity: str,
        title: str,
        description: str,
        file_path: str,
        line_number: Optional[int] = None,
        code_snippet: Optional[str] = None,
        poc: Optional[str] = None,
        agent_source: str = "analysis"
    ):
        """记录漏洞发现"""
        self.stats["findings_discovered"] += 1

        entry = TraceEntry(
            timestamp=self._now(),
            type="finding",
            title=f"{severity.upper()} - {title}",
            content={
                "type": finding_type,
                "severity": severity,
                "title": title,
                "description": description,
                "location": {
                    "file": file_path,
                    "line": line_number,
                },
                "code_snippet": code_snippet,
                "poc": poc,
                "agent_source": agent_source,
            }
        )

        self.entries.append(entry)

        severity_emoji = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🟢",
        }.get(severity.lower(), "⚪")

        location_str = f"`{file_path}:{line_number}`" if line_number else f"`{file_path}`"

        self._append_to_markdown(f"""
### 🐛 [{self._now()}] {severity_emoji} 发现漏洞: {title}

**类型**: {finding_type}
**严重程度**: {severity.upper()}
**位置**: {location_str}
**发现者**: {agent_source} Agent

**描述**:
{description}

{f'**代码片段**:\n```\n{code_snippet[:300]}\n```\n' if code_snippet else ''}
{f'**PoC**:\n```\n{poc[:300]}\n```\n' if poc else ''}

""")

    def add_embedding_record(
        self,
        content_type: str,
        content_summary: str,
        vector_count: int,
        model: str = "unknown",
        metadata: Optional[Dict] = None
    ):
        """记录嵌入向量生成"""
        self.stats["embeddings_created"] += vector_count

        entry = TraceEntry(
            timestamp=self._now(),
            type="embedding",
            title=f"生成 {vector_count} 个嵌入向量",
            content={
                "content_type": content_type,
                "summary": content_summary,
                "vector_count": vector_count,
                "model": model,
            },
            metadata=metadata or {}
        )

        self.entries.append(entry)

        self._append_to_markdown(f"""
### 🧬 [{self._now()}] 生成嵌入向量

**内容类型**: {content_type}
**向量数量**: {vector_count}
**模型**: {model}
**摘要**: {content_summary[:200]}

""")

    def add_context_compression(
        self,
        original_length: int,
        compressed_length: int,
        compression_method: str,
        summary: str,
        full_content_ref: Optional[str] = None
    ):
        """记录上下文压缩"""
        self.stats["contexts_compressed"] += 1

        # 保存完整内容到归档
        snapshot_id = f"ctx_{len(self.context_snapshots)}"
        if full_content_ref:
            self.context_snapshots.append({
                "id": snapshot_id,
                "timestamp": self._now(),
                "original_length": original_length,
                "full_content": full_content_ref,
            })
            self._save_context_archive()

        entry = TraceEntry(
            timestamp=self._now(),
            type="context_compression",
            title=f"压缩上下文: {original_length} → {compressed_length} 字符",
            content={
                "original_length": original_length,
                "compressed_length": compressed_length,
                "compression_ratio": f"{compressed_length / original_length * 100:.1f}%",
                "method": compression_method,
                "summary": summary,
                "full_content_ref": snapshot_id,
            }
        )

        self.entries.append(entry)

        self._append_to_markdown(f"""
### 📦 [{self._now()}] 上下文压缩

**原始长度**: {original_length} 字符
**压缩后**: {compressed_length} 字符
**压缩率**: {compressed_length / original_length * 100:.1f}%
**方法**: {compression_method}
**完整内容引用**: `{snapshot_id}`

**摘要**:
{summary[:500]}

""")

    def add_llm_call(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: int,
        purpose: str = "decision"
    ):
        """记录 LLM 调用"""
        self.stats["llm_calls"] += 1
        self.stats["total_tokens"] += (prompt_tokens + completion_tokens)

        entry = TraceEntry(
            timestamp=self._now(),
            type="llm_call",
            title=f"LLM 调用: {model}",
            content={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "duration_ms": duration_ms,
                "purpose": purpose,
            }
        )

        self.entries.append(entry)

    def add_verification_result(
        self,
        finding_id: str,
        finding_title: str,
        verified: bool,
        evidence: str,
        sandbox_output: Optional[str] = None
    ):
        """记录验证结果"""
        status = "✅ 验证通过" if verified else "❌ 验证失败"

        self._append_to_markdown(f"""
### 🔬 [{self._now()}] {status}: {finding_title}

**漏洞ID**: `{finding_id}`
**验证结果**: {'通过' if verified else '失败'}

**证据**:
{evidence[:500]}

{f'**沙箱输出**:\n```\n{sandbox_output[:300]}\n```\n' if sandbox_output else ''}

""")

    def get_summary_for_agent(self) -> str:
        """生成供 Agent 查阅的精简摘要"""
        summary = f"""# 审计任务 {self.task_id[:8]} 摘要

## 统计
- Agent 调度: {self.stats['agents_dispatched']} 次
- 工具调用: {self.stats['tools_called']} 次
- 发现漏洞: {self.stats['findings_discovered']} 个
- LLM 调用: {self.stats['llm_calls']} 次
- Token 消耗: {self.stats['total_tokens']}

## 最近 10 条关键事件
"""
        for entry in self.entries[-10:]:
            summary += f"- [{entry.timestamp}] {entry.title}\n"

        summary += f"\n完整追踪文件: {self.trace_md}\n"
        return summary

    def finalize(self):
        """任务完成时写入最终统计"""
        self._update_stats_table()
        self._save_json_trace()
        logger.info(f"[AuditTrace] Finalized trace for task {self.task_id[:8]}")

    def _now(self) -> str:
        """当前时间戳"""
        return datetime.now(timezone.utc).strftime('%H:%M:%S')

    def _append_to_markdown(self, content: str):
        """追加内容到 Markdown 文件"""
        with open(self.trace_md, 'a', encoding='utf-8') as f:
            f.write(content)

    def _update_stats_table(self):
        """更新统计表格"""
        content = self.trace_md.read_text(encoding='utf-8')

        # 替换统计表格
        new_table = f"""| 指标 | 数值 |
|------|------|
| Agent 调度次数 | {self.stats['agents_dispatched']} |
| 工具调用次数 | {self.stats['tools_called']} |
| 发现漏洞数 | {self.stats['findings_discovered']} |
| 嵌入向量数 | {self.stats['embeddings_created']} |
| 上下文压缩次数 | {self.stats['contexts_compressed']} |
| LLM 调用次数 | {self.stats['llm_calls']} |
| Token 消耗 | {self.stats['total_tokens']} |"""

        # 简单替换（假设表格在固定位置）
        lines = content.split('\n')
        new_lines = []
        in_table = False
        table_replaced = False

        for line in lines:
            if '| 指标 | 数值 |' in line and not table_replaced:
                in_table = True
                new_lines.append(new_table)
                table_replaced = True
            elif in_table and line.startswith('|'):
                continue  # 跳过旧表格行
            elif in_table and not line.startswith('|'):
                in_table = False
                new_lines.append(line)
            else:
                new_lines.append(line)

        self.trace_md.write_text('\n'.join(new_lines), encoding='utf-8')

    def _save_json_trace(self):
        """保存 JSON 格式的追踪数据"""
        data = {
            "task_id": self.task_id,
            "project_name": self.project_name,
            "stats": self.stats,
            "entries": [asdict(e) for e in self.entries],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.trace_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def _save_context_archive(self):
        """保存上下文归档"""
        self.context_archive.write_text(
            json.dumps(self.context_snapshots, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
