/**
 * Agent Audit Utilities
 * Helper functions for the Agent Audit page
 */

import type { AgentFinding, AgentTask, AgentTreeResponse, AgentTreeNode, AiContextSummary, LogItem, PocSummary, ToolCallSummary } from "./types";

/**
 * Build tree structure from flat node list
 */
export function buildAgentTree(flatNodes: AgentTreeNode[]): AgentTreeNode[] {
  if (!flatNodes || flatNodes.length === 0) return [];

  // Create node map
  const nodeMap = new Map<string, AgentTreeNode>();
  flatNodes.forEach(node => {
    nodeMap.set(node.agent_id, { ...node, children: [] });
  });

  // Build tree structure
  const rootNodes: AgentTreeNode[] = [];

  flatNodes.forEach(node => {
    const currentNode = nodeMap.get(node.agent_id)!;

    if (node.parent_agent_id && nodeMap.has(node.parent_agent_id)) {
      const parentNode = nodeMap.get(node.parent_agent_id)!;
      parentNode.children.push(currentNode);
    } else {
      rootNodes.push(currentNode);
    }
  });

  return rootNodes;
}

/**
 * Find agent by ID in tree
 */
export function findAgentInTree(nodes: AgentTreeNode[], id: string): AgentTreeNode | null {
  for (const node of nodes) {
    if (node.agent_id === id) return node;
    const found = findAgentInTree(node.children, id);
    if (found) return found;
  }
  return null;
}

/**
 * Find agent name by ID in tree
 */
export function findAgentName(nodes: AgentTreeNode[], id: string): string | null {
  const agent = findAgentInTree(nodes, id);
  return agent?.agent_name || null;
}

/**
 * Generate unique log ID
 */
let logIdCounter = 0;
export function generateLogId(): string {
  return `log-${++logIdCounter}`;
}

/**
 * Reset log ID counter (for testing)
 */
export function resetLogIdCounter(): void {
  logIdCounter = 0;
}

/**
 * Get current time string for logs
 */
export function getTimeString(): string {
  return new Date().toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
}

/**
 * Format a backend event ISO timestamp into HH:mm:ss.
 * Falls back to current time only when the input is missing/invalid,
 * so historical replay shows the real event time (not render time).
 */
export function formatEventTime(iso?: string | null): string {
  if (!iso) return getTimeString();
  const d = new Date(iso);
  if (isNaN(d.getTime())) return getTimeString();
  return d.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
}

/**
 * Create a log item.
 * If `time` is provided (e.g. from a historical event's timestamp), it is used;
 * otherwise falls back to the current time (live SSE events).
 */
export function createLogItem(item: Omit<LogItem, 'id' | 'time'> & { time?: string }): LogItem {
  const { time, ...rest } = item;
  return {
    ...rest,
    id: generateLogId(),
    time: time ?? getTimeString(),
  };
}

/**
 * Clean thinking content (extract only the Thought part, remove Action/Action Input)
 */
export function cleanThinkingContent(content: string): string {
  if (!content) return "";

  let cleaned = content;

  // 1. 尝试提取 Thought: 后面的内容
  const thoughtMatch = cleaned.match(/Thought:\s*([\s\S]*?)(?=\n\s*Action\s*:|$)/i);
  if (thoughtMatch && thoughtMatch[1]) {
    cleaned = thoughtMatch[1].trim();
  } else {
    // 2. 如果没有 Thought: 前缀，尝试移除 Action 部分
    // 匹配 Action: 及其后面的所有内容（包括开头的 Action）
    cleaned = cleaned.replace(/^Action\s*:[\s\S]*$/i, "");
    cleaned = cleaned.replace(/\n\s*Action\s*:[\s\S]*$/i, "");
  }

  // 3. 移除可能残留的 Action Input 部分
  cleaned = cleaned.replace(/Action\s*Input\s*:[\s\S]*$/i, "");

  // 4. 清理空白和特殊字符
  cleaned = cleaned.trim();

  // 5. 如果清理后只剩下 "Action" 或类似的碎片，返回空
  if (/^Action\s*$/i.test(cleaned) || cleaned.length < 5) {
    return "";
  }

  return cleaned;
}

/**
 * Truncate output string
 */
export function truncateOutput(output: string, maxLength: number = 1000): string {
  if (output.length <= maxLength) return output;
  return output.slice(0, maxLength) + '\n... (truncated)';
}

/**
 * Calculate severity counts from findings
 */
export function calculateSeverityCounts(findings: { severity: string }[]): Record<string, number> {
  return {
    critical: findings.filter(f => f.severity === 'critical').length,
    high: findings.filter(f => f.severity === 'high').length,
    medium: findings.filter(f => f.severity === 'medium').length,
    low: findings.filter(f => f.severity === 'low').length,
  };
}

/**
 * Check if task is in running state
 */
export function isTaskRunning(status: string | undefined): boolean {
  return status === 'running' || status === 'pending';
}

/**
 * Check if task is complete
 */
export function isTaskComplete(status: string | undefined): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled';
}

/**
 * Format token count
 */
export function formatTokens(tokens: number): string {
  return (tokens / 1000).toFixed(1) + 'k';
}

/**
 * Filter logs by agent
 */
export function filterLogsByAgent(
  logs: LogItem[],
  selectedAgentId: string | null,
  treeNodes: AgentTreeNode[],
  showAllLogs: boolean
): LogItem[] {
  if (showAllLogs || !selectedAgentId) {
    return logs;
  }

  const selectedAgentName = findAgentName(treeNodes, selectedAgentId);
  if (!selectedAgentName) return logs;

  return logs.filter(log =>
    log.agentName?.toLowerCase() === selectedAgentName.toLowerCase() ||
    log.agentName?.toLowerCase().includes(selectedAgentName.toLowerCase().split('_')[0])
  );
}

/**
 * Debounce function
 */
export function debounce<T extends (...args: unknown[]) => unknown>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout> | null = null;

  return (...args: Parameters<T>) => {
    if (timeout) clearTimeout(timeout);
    timeout = setTimeout(() => func(...args), wait);
  };
}

export function buildAiContextSummary(params: {
  task: AgentTask | null;
  agentTree: AgentTreeResponse | null;
  findings: AgentFinding[];
  logs: LogItem[];
  selectedAgentId: string | null;
}): AiContextSummary {
  const { task, findings, logs, selectedAgentId } = params;
  const recentLogs = logs.slice(-20);
  const toolCalls = summarizeToolCalls(logs);
  const pocResults = summarizePocResults(findings);

  return {
    taskId: task?.id ?? null,
    taskName: task?.name || "未命名审计任务",
    taskStatus: task?.status || "unknown",
    currentPhase: task?.current_phase || "unknown",
    progressPercentage: task?.progress_percentage || 0,
    selectedAgentId,
    findingsTotal: findings.length,
    criticalFindings: findings.filter(f => f.severity === "critical").length,
    highFindings: findings.filter(f => f.severity === "high").length,
    verifiedFindings: findings.filter(f => f.is_verified).length,
    recentLogs,
    toolCalls,
    pocResults,
  };
}

function summarizeToolCalls(logs: LogItem[]): ToolCallSummary[] {
  return logs
    .filter(log => log.type === "tool" && log.tool?.name)
    .slice(-10)
    .map(log => ({
      id: log.id,
      toolName: log.tool?.name || "unknown_tool",
      status: log.tool?.status || "completed",
      agentName: log.agentName,
      durationMs: log.tool?.duration,
      outputPreview: log.content ? truncateOutput(log.content, 240) : undefined,
    }));
}

function summarizePocResults(findings: AgentFinding[]): PocSummary[] {
  return findings
    .filter(finding => finding.has_poc || finding.poc_code || finding.is_verified)
    .slice(0, 8)
    .map(finding => ({
      findingId: finding.id,
      title: finding.title,
      status: finding.is_verified ? "verified" : finding.has_poc || finding.poc_code ? "unknown" : "failed",
      summary: finding.is_verified
        ? "漏洞已通过验证"
        : finding.poc_code
          ? "已生成 PoC，等待复核或再次验证"
          : "暂无可用 PoC 结果",
    }));
}
