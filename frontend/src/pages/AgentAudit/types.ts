/**
 * Agent Audit Types
 * Type definitions for the Agent Audit page
 */

import type { AgentTask, AgentFinding, AgentTreeNode } from "@/shared/api/agentTasks";

// ============ Log Types ============

export type LogType =
  | 'thinking'
  | 'tool'
  | 'phase'
  | 'finding'
  | 'info'
  | 'error'
  | 'user'
  | 'dispatch'
  | 'progress';

export type ToolStatus = 'running' | 'completed' | 'failed';

export interface LogItem {
  id: string;
  time: string;
  type: LogType;
  title: string;
  content?: string;
  isStreaming?: boolean;
  tool?: {
    name: string;
    duration?: number;
    status?: ToolStatus;
  };
  severity?: string;
  agentName?: string;
  progressKey?: string; // 用于标识进度日志的唯一键，�?"index_progress"
}

export interface ToolCallSummary {
  id: string;
  toolName: string;
  status: 'running' | 'completed' | 'failed';
  agentName?: string;
  durationMs?: number;
  outputPreview?: string;
}

export interface PocSummary {
  findingId: string;
  title: string;
  status: 'verified' | 'failed' | 'unknown';
  summary: string;
}

export interface AiContextSummary {
  taskId: string | null;
  taskName: string;
  taskStatus: string;
  currentPhase: string;
  progressPercentage: number;
  selectedAgentId: string | null;
  findingsTotal: number;
  criticalFindings: number;
  highFindings: number;
  verifiedFindings: number;
  recentLogs: LogItem[];
  toolCalls: ToolCallSummary[];
  pocResults: PocSummary[];
}

// ============ Connection Types ============

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'failed';

// ============ State Types ============

export interface InitStep {
  name: string;
  status: 'start' | 'done';
}

interface AgentAuditState {
  task: AgentTask | null;
  findings: AgentFinding[];
  agentTree: AgentTreeResponse | null;
  logs: LogItem[];
  initSteps: InitStep[];
  selectedAgentId: string | null;
  showAllLogs: boolean;
  isLoading: boolean;
  error: string | null;
  connectionStatus: ConnectionStatus;
  isAutoScroll: boolean;
  expandedLogIds: Set<string>;
  // Wave 2 §3.5: stale running 检测与重连状态追踪
  reconnectAttempt: number;
  reconnectReason?: string;
  streamDied: boolean;
  streamDiedReason?: string;
}

export interface AgentTreeResponse {
  task_id: string;
  root_agent_id: string | null;
  total_agents: number;
  running_agents: number;
  completed_agents: number;
  failed_agents: number;
  total_findings: number;
  nodes: AgentTreeNode[];
}

// ============ Action Types ============

export type AgentAuditAction =
  | { type: 'SET_TASK'; payload: AgentTask }
  | { type: 'SET_FINDINGS'; payload: AgentFinding[] }
  | { type: 'ADD_FINDING'; payload: Partial<AgentFinding> & { id: string } }
  | { type: 'SET_AGENT_TREE'; payload: AgentTreeResponse }
  | { type: 'SET_LOGS'; payload: LogItem[] }
  | { type: 'ADD_LOG'; payload: Omit<LogItem, 'id' | 'time'> & { id?: string } }
  | { type: 'UPDATE_LOG'; payload: { id: string; updates: Partial<LogItem> } }
  | { type: 'UPDATE_OR_ADD_PROGRESS_LOG'; payload: { progressKey: string; title: string; agentName?: string } }
  | { type: 'COMPLETE_PROGRESS_LOG'; payload: { progressKey: string } }
  | { type: 'COMPLETE_TOOL_LOG'; payload: { toolName: string; output: string; duration: number } }
  | { type: 'REMOVE_LOG'; payload: string }
  | { type: 'SELECT_AGENT'; payload: string | null }
  | { type: 'TOGGLE_SHOW_ALL_LOGS' }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'SET_CONNECTION_STATUS'; payload: ConnectionStatus }
  | { type: 'SET_AUTO_SCROLL'; payload: boolean }
  | { type: 'TOGGLE_LOG_EXPANDED'; payload: string }
  | { type: 'COMPLETE_ALL_RUNNING_TOOLS' }
  | { type: 'RESET' }
  | { type: 'ADD_INIT_STEP'; payload: InitStep }
  // Wave 2 §3.5: 重连状态与断流通知
  | { type: 'RECONNECT_ATTEMPT'; payload: { attempt: number; reason?: string } }
  | { type: 'SSE_STREAM_DIED'; payload: { reason: string } };

// ============ Component Props ============

export interface AgentTreeNodeItemProps {
  node: AgentTreeNode;
  depth?: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export interface LogEntryProps {
  item: LogItem;
  isExpanded: boolean;
  onToggle: () => void;
}

export interface AgentDetailPanelProps {
  agentId: string;
  treeNodes: AgentTreeNode[];
  onClose: () => void;
}

export interface StatsPanelProps {
  task: AgentTask | null;
  findings: AgentFinding[];
  compact?: boolean;
}

export interface AICollaborationPanelProps {
  context: AiContextSummary;
  isRunning: boolean;
  onSendMessage: (message: string) => Promise<string>;
  onRequestContinueAudit: () => void;
  onRequestRerunPoc: (findingId: string) => void;
  taskId?: string;
}

export interface HeaderProps {
  task: AgentTask | null;
  isRunning: boolean;
  isPaused: boolean;
  isPausing: boolean;
  isResuming: boolean;
  isDeleting?: boolean;
  onPause: () => void;
  onResume: () => void;
  onExport: () => void;
  onNewAudit: () => void;
  onDelete?: () => void;
  onOpenAiPanel?: () => void;
}

export interface ActivityLogProps {
  logs: LogItem[];
  filteredLogs: LogItem[];
  isConnected: boolean;
  isRunning: boolean;
  isAutoScroll: boolean;
  expandedIds: Set<string>;
  selectedAgentId: string | null;
  showAllLogs: boolean;
  onToggleAutoScroll: () => void;
  onToggleExpand: (id: string) => void;
  onClearFilter: () => void;
}

export interface AgentTreePanelProps {
  treeNodes: AgentTreeNode[];
  agentTree: AgentTreeResponse | null;
  selectedAgentId: string | null;
  showAllLogs: boolean;
  isRunning: boolean;
  onSelectAgent: (id: string) => void;
  onClearSelection: () => void;
}

// ============ Stream Event Types ============

export interface StreamEvent {
  type: string;
  message?: string;
  metadata?: {
    agent_name?: string;
    agent?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface StreamOptions {
  includeThinking?: boolean;
  includeToolCalls?: boolean;
  onEvent?: (event: StreamEvent) => void;
  onThinkingStart?: () => void;
  onThinkingToken?: (token: string, accumulated: string) => void;
  onThinkingEnd?: (response: string) => void;
  onToolStart?: (name: string, input: Record<string, unknown>) => void;
  onToolEnd?: (name: string, output: unknown, duration: number) => void;
  onFinding?: (finding: Record<string, unknown>) => void;
  onComplete?: () => void;
  onError?: (error: string) => void;
}


export interface SandboxAttempt {
  tool: string;
  success: boolean;
  exit_code: number | null;
  command: string;
  evidence_summary: string;
  target_ref?: string;
  finding_id?: string;
  weak_evidence?: boolean;
}

// Re-export from API
export type { AgentTask, AgentFinding, AgentTreeNode };
