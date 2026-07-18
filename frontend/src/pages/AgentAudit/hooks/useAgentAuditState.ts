/**
 * Agent Audit State Hook
 * Centralized state management using useReducer
 */

import { useReducer, useCallback, useMemo, useRef } from "react";
import type {
  AgentAuditState,
  AgentAuditAction,
  InitStep,
  LogItem,
  AgentTask,
  AgentFinding,
  AgentTreeResponse,
  ConnectionStatus,
} from "../types";
import { createLogItem, filterLogsByAgent, buildAgentTree } from "../utils";
import type { AgentTreeNode } from "@/shared/api/agentTasks";

// ============ Initial State ============

const initialState: AgentAuditState = {
  task: null,
  findings: [],
  agentTree: null,
  logs: [],
  initSteps: [] as InitStep[],
  selectedAgentId: null,
  showAllLogs: true,
  isLoading: false,
  error: null,
  connectionStatus: 'disconnected',
  isAutoScroll: true,
  expandedLogIds: new Set(),
  // Wave 2 §3.5: 重连与断流状态
  reconnectAttempt: 0,
  streamDied: false,
};

// ============ Reducer ============

function agentAuditReducer(state: AgentAuditState, action: AgentAuditAction): AgentAuditState {
  switch (action.type) {
    case 'SET_TASK':
      return { ...state, task: action.payload };

    case 'SET_FINDINGS':
      return { ...state, findings: action.payload };

    case 'ADD_FINDING': {
      // 🔥 添加单个 finding，避免重�?
      const newFinding = action.payload;
      const existingIds = new Set(state.findings.map(f => f.id));
      if (newFinding.id && existingIds.has(newFinding.id)) {
        return state; // 已存在，不添�?
      }
      return { ...state, findings: [...state.findings, newFinding as AgentFinding] };
    }

    case 'SET_AGENT_TREE':
      return { ...state, agentTree: action.payload };

    case 'SET_LOGS':
      return { ...state, logs: action.payload };

    case 'ADD_LOG': {
      const { id: providedId, ...logData } = action.payload;
      const newLog = providedId
        ? { ...createLogItem(logData), id: providedId }
        : createLogItem(logData);
      return { ...state, logs: [...state.logs, newLog] };
    }

    case 'UPDATE_LOG': {
      const { id, updates } = action.payload;
      return {
        ...state,
        logs: state.logs.map(log =>
          log.id === id ? { ...log, ...updates } : log
        ),
      };
    }

    case 'REMOVE_LOG':
      return {
        ...state,
        logs: state.logs.filter(log => log.id !== action.payload),
      };

    case 'COMPLETE_TOOL_LOG': {
      const { toolName, output, duration } = action.payload;
      const updatedLogs = [...state.logs];
      for (let i = updatedLogs.length - 1; i >= 0; i--) {
        const log = updatedLogs[i];
        if (log.type === 'tool' && log.tool?.name === toolName && log.tool?.status === 'running') {
          const previousContent = log.content || '';
          updatedLogs[i] = {
            ...log,
            title: `Completed: ${toolName}`,
            content: `${previousContent}\n\nOutput:\n${output}`,
            tool: { name: toolName, duration, status: 'completed' },
          };
          break;
        }
      }
      return { ...state, logs: updatedLogs };
    }

    case 'UPDATE_OR_ADD_PROGRESS_LOG': {
      const { progressKey, title, agentName } = action.payload;
      // 查找是否已存在相�?progressKey 的进度日�?
      const existingIndex = state.logs.findIndex(
        log => log.type === 'progress' && log.progressKey === progressKey
      );

      if (existingIndex >= 0) {
        // 更新现有日志�?title �?time
        const updatedLogs = [...state.logs];
        updatedLogs[existingIndex] = {
          ...updatedLogs[existingIndex],
          title,
          time: new Date().toLocaleTimeString('en-US', { hour12: false }),
        };
        return { ...state, logs: updatedLogs };
      } else {
        // 添加新的进度日志
        const newLog = createLogItem({
          type: 'progress',
          title,
          progressKey,
          agentName,
        });
        return { ...state, logs: [...state.logs, newLog] };
      }
    }

    case 'COMPLETE_PROGRESS_LOG': {
      const { progressKey } = action.payload;
      // Skip if no matching progress log (already completed or not found)
      const idx = state.logs.findIndex(
        log => log.type === 'progress' && log.progressKey === progressKey
      );
      if (idx < 0) return state;  // Already completed or not found, skip
      if (idx >= 0) {
        const updatedLogs = [...state.logs];
        updatedLogs[idx] = {
          ...updatedLogs[idx],
          type: 'info',
          title: updatedLogs[idx].title + ' - Complete',
          time: new Date().toLocaleTimeString('en-US', { hour12: false }),
        };
        return { ...state, logs: updatedLogs };
      }
      return state;
    }

    case 'SELECT_AGENT':
      return {
        ...state,
        selectedAgentId: action.payload,
        showAllLogs: action.payload === null,
      };

    case 'TOGGLE_SHOW_ALL_LOGS':
      return {
        ...state,
        showAllLogs: !state.showAllLogs,
        selectedAgentId: state.showAllLogs ? state.selectedAgentId : null,
      };

    case 'SET_LOADING':
      return { ...state, isLoading: action.payload };

    case 'SET_ERROR':
      return { ...state, error: action.payload };

    case 'SET_CONNECTION_STATUS':
      return { ...state, connectionStatus: action.payload };

    case 'SET_AUTO_SCROLL':
      return { ...state, isAutoScroll: action.payload };

    case 'TOGGLE_LOG_EXPANDED': {
      const newExpanded = new Set(state.expandedLogIds);
      if (newExpanded.has(action.payload)) {
        newExpanded.delete(action.payload);
      } else {
        newExpanded.add(action.payload);
      }
      return { ...state, expandedLogIds: newExpanded };
    }

    case 'COMPLETE_ALL_RUNNING_TOOLS': {
      const updatedLogs = state.logs.map(log => {
        if (log.type === 'tool' && log.tool?.status === 'running') {
          return {
            ...log,
            title: `Completed: ${log.tool.name}`,
            tool: { ...log.tool, status: 'completed' as const },
          };
        }
        return log;
      });
      return { ...state, logs: updatedLogs };
    }

    case 'ADD_INIT_STEP': {
      const step = action.payload;
      const existing = state.initSteps.find(s => s.name === step.name);
      if (existing) {
        return {
          ...state,
          initSteps: state.initSteps.map(s =>
            s.name === step.name ? { ...s, status: step.status } : s
          ),
        };
      }
      return { ...state, initSteps: [...state.initSteps, step] };
    }

    // Wave 2 §3.5: 追踪 SSE 重连尝试与断流原因，供 UI 显示重连横幅
    case 'RECONNECT_ATTEMPT':
      return {
        ...state,
        reconnectAttempt: action.payload.attempt,
        reconnectReason: action.payload.reason,
        streamDied: false,
      };

    case 'SSE_STREAM_DIED':
      return {
        ...state,
        streamDied: true,
        streamDiedReason: action.payload.reason,
      };

    case 'RESET':
      return { ...initialState };

    default:
      return state;
  }
}

// ============ Hook ============

export function useAgentAuditState() {
  const [state, dispatch] = useReducer(agentAuditReducer, initialState);
  const currentThinkingId = useRef<string | null>(null);
  const currentAgentName = useRef<string | null>(null);

  // ============ Action Creators ============

  const setTask = useCallback((task: AgentTask) => {
    dispatch({ type: 'SET_TASK', payload: task });
  }, []);

  const setFindings = useCallback((findings: AgentFinding[]) => {
    dispatch({ type: 'SET_FINDINGS', payload: findings });
  }, []);

  const setAgentTree = useCallback((tree: AgentTreeResponse) => {
    dispatch({ type: 'SET_AGENT_TREE', payload: tree });
  }, []);

  const addLog = useCallback((log: Omit<LogItem, 'id' | 'time'>): string => {
    const newLog = createLogItem(log);
    dispatch({ type: 'SET_LOGS', payload: [...state.logs, newLog] });
    return newLog.id;
  }, [state.logs]);

  const updateLog = useCallback((id: string, updates: Partial<LogItem>) => {
    dispatch({ type: 'UPDATE_LOG', payload: { id, updates } });
  }, []);

  const removeLog = useCallback((id: string) => {
    dispatch({ type: 'REMOVE_LOG', payload: id });
  }, []);

  const selectAgent = useCallback((id: string | null) => {
    dispatch({ type: 'SELECT_AGENT', payload: id });
  }, []);

  const toggleShowAllLogs = useCallback(() => {
    dispatch({ type: 'TOGGLE_SHOW_ALL_LOGS' });
  }, []);

  const setLoading = useCallback((loading: boolean) => {
    dispatch({ type: 'SET_LOADING', payload: loading });
  }, []);

  const setError = useCallback((error: string | null) => {
    dispatch({ type: 'SET_ERROR', payload: error });
  }, []);

  const setConnectionStatus = useCallback((status: ConnectionStatus) => {
    dispatch({ type: 'SET_CONNECTION_STATUS', payload: status });
  }, []);

  const setAutoScroll = useCallback((enabled: boolean) => {
    dispatch({ type: 'SET_AUTO_SCROLL', payload: enabled });
  }, []);

  const toggleLogExpanded = useCallback((id: string) => {
    dispatch({ type: 'TOGGLE_LOG_EXPANDED', payload: id });
  }, []);

  const reset = useCallback(() => {
    dispatch({ type: 'RESET' });
    currentThinkingId.current = null;
    currentAgentName.current = null;
  }, []);

  // ============ Thinking State Management ============

  const setCurrentAgentName = useCallback((name: string | null) => {
    currentAgentName.current = name;
  }, []);

  const getCurrentAgentName = useCallback(() => {
    return currentAgentName.current;
  }, []);

  const setCurrentThinkingId = useCallback((id: string | null) => {
    currentThinkingId.current = id;
  }, []);

  const getCurrentThinkingId = useCallback(() => {
    return currentThinkingId.current;
  }, []);

  // ============ Computed Values ============

  const treeNodes = useMemo(() => {
    if (!state.agentTree?.nodes) return [];
    return buildAgentTree(state.agentTree.nodes);
  }, [state.agentTree?.nodes]);

  const filteredLogs = useMemo(() => {
    return filterLogsByAgent(
      state.logs,
      state.selectedAgentId,
      treeNodes,
      state.showAllLogs
    );
  }, [state.logs, state.selectedAgentId, treeNodes, state.showAllLogs]);

  const isRunning = useMemo(() => {
    return state.task?.status === 'running' || state.task?.status === 'pending';
  }, [state.task?.status]);

  const isInitializing = useMemo(() => {
    const status = state.task?.status;
    return status === 'pending' || status === 'initializing';
  }, [state.task?.status]);

  const isPaused = useMemo(() => {
    return state.task?.status === 'paused';
  }, [state.task?.status]);

  const canReAudit = useMemo(() => {
    return state.task?.status === 'completed_with_gaps';
  }, [state.task?.status]);

  const canRecover = useMemo(() => {
    return state.task?.status === 'running' && state.task?.orchestrator_alive === false;
  }, [state.task?.status, state.task?.orchestrator_alive]);

  const isComplete = useMemo(() => {
    const status = state.task?.status;
    return status === 'completed' || status === 'completed_with_gaps' || status === 'failed' || status === 'cancelled';
  }, [state.task?.status]);

  return {
    // State
    ...state,
    treeNodes,
    filteredLogs,
    isRunning,
    isInitializing,
    isPaused,
    isComplete,
    canReAudit,
    canRecover,

    // Actions
    setTask,
    setFindings,
    setAgentTree,
    addLog,
    updateLog,
    removeLog,
    selectAgent,
    toggleShowAllLogs,
    setLoading,
    setError,
    setConnectionStatus,
    setAutoScroll,
    toggleLogExpanded,
    reset,

    // Thinking state
    setCurrentAgentName,
    getCurrentAgentName,
    setCurrentThinkingId,
    getCurrentThinkingId,

    // Direct dispatch for complex operations
    dispatch,
  };
}

export type AgentAuditStateHook = ReturnType<typeof useAgentAuditState>;
