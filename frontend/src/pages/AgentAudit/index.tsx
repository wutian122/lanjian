/**
 * Agent Audit Page - Modular Implementation
 * Main entry point for the Agent Audit feature
 * Cassette Futurism / Terminal Retro aesthetic
 */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useParams } from "react-router-dom";
import { Terminal, Bot, Loader2, Radio, Filter, Maximize2, ArrowDown, RefreshCw, AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useLocalStorage } from "@/shared/hooks/useLocalStorage";
import { toast } from "sonner";
import { useResilientStream, type ConnectionState } from "./hooks/useResilientStream";

import {
  getAgentTask,
  getAgentFindings,
  pauseAgentTask,
  resumeAgentTask,
  reAuditAgentTask,
  recoverAgentTask,
  getAgentTree,
  getAgentEvents,
  AgentEvent,
  chatWithAgentTask,
} from "@/shared/api/agentTasks";
import CreateAgentTaskDialog from "@/components/agent/CreateAgentTaskDialog";

// Local imports
import {
  InitProgress,
  SplashScreen,
  Header,
  LogEntry,
  AgentTreeNodeItem,
  AgentDetailPanel,
  StatsPanel,
  AgentErrorBoundary,
  ConnectionStatus,
  AICollaborationPanel,
} from "./components";
import ReportExportDialog from "./components/ReportExportDialog";
import { useAgentAuditState } from "./hooks";
import { ACTION_VERBS, POLLING_INTERVALS } from "./constants";
import { buildAiContextSummary, cleanThinkingContent, truncateOutput, createLogItem } from "./utils";
import type { LogItem } from "./types";

function AgentAuditPageContent() {
  const { taskId } = useParams<{ taskId: string }>();
  const {
    task, findings, agentTree, logs, selectedAgentId, showAllLogs,
    isLoading, connectionStatus, isAutoScroll, expandedLogIds,
    treeNodes, filteredLogs, isRunning, isInitializing, isPaused, isComplete, canReAudit, canRecover,
    initSteps,
    setTask, setFindings, setAgentTree, addLog, updateLog, removeLog,
    selectAgent, setLoading, setConnectionStatus, setAutoScroll, toggleLogExpanded,
    setCurrentAgentName, getCurrentAgentName, setCurrentThinkingId, getCurrentThinkingId,
    dispatch, reset,
  } = useAgentAuditState();

  // Local state
  const [showSplash, setShowSplash] = useState(!taskId);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showAiPanel, setShowAiPanel] = useState(false);
  const [panelWidth, setPanelWidth] = useLocalStorage<number>("ai-panel-width", 480);
  const [showExportDialog, setShowExportDialog] = useState(false);
  const [isPausing, setIsPausing] = useState(false);
  const [isResuming, setIsResuming] = useState(false);
  const [statusVerb, setStatusVerb] = useState(ACTION_VERBS[0]);
  const [statusDots, setStatusDots] = useState(0);

  const logContainerRef = useRef<HTMLDivElement>(null);
  const agentTreeRefreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAgentTreeRefreshTime = useRef<number>(0);
  const previousTaskIdRef = useRef<string | undefined>(undefined);
  const disconnectStreamRef = useRef<(() => void) | null>(null);
  const lastEventSequenceRef = useRef<number>(0);
  const hasConnectedRef = useRef<boolean>(false); // 🔥 追踪是否已连接 SSE
  const hasLoadedHistoricalEventsRef = useRef<boolean>(false); // 🔥 追踪是否已加载历史事件
  // 🔥 使用 state 来标记历史事件加载状态和触发 streamOptions 重新计算
  const [afterSequence, setAfterSequence] = useState<number>(0);
  const [historicalEventsLoaded, setHistoricalEventsLoaded] = useState<boolean>(false);

  // 🔥 当 taskId 变化时立即重置状态（新建任务时清理旧日志）
  useEffect(() => {
    // 如果 taskId 发生变化，立即重置
    if (taskId !== previousTaskIdRef.current) {
      // 1. 先断开旧的 SSE 流连接
      if (disconnectStreamRef.current) {
        disconnectStreamRef.current();
        disconnectStreamRef.current = null;
      }
      // 2. 重置所有状态
      reset();
      setShowSplash(!taskId);
      // 3. 重置事件序列号和加载状态
      lastEventSequenceRef.current = 0;
      hasConnectedRef.current = false; // 🔥 重置 SSE 连接标志
      hasLoadedHistoricalEventsRef.current = false; // 🔥 重置历史事件加载标志
      hasTransitionedRef.current = false; // 🔥 重置完成过渡标志
      hasCompletedViaSSE.current = false; // FIX F3: reset SSE completion flag
      setHistoricalEventsLoaded(false); // 🔥 重置历史事件加载状态
      setAfterSequence(0); // 🔥 重置 afterSequence state
    }
    previousTaskIdRef.current = taskId;
  }, [taskId, reset]);

  // ============ Data Loading ============

  const loadTask = useCallback(async () => {
    if (!taskId) return;
    try {
      const data = await getAgentTask(taskId);
      setTask(data);
    } catch {
      toast.error("Failed to load task");
    }
  }, [taskId, setTask]);

  const loadFindings = useCallback(async () => {
    if (!taskId) return;
    try {
      const data = await getAgentFindings(taskId);
      setFindings(data);
    } catch (err) {
      console.error(err);
    }
  }, [taskId, setFindings]);

  const loadAgentTree = useCallback(async () => {
    if (!taskId) return;
    try {
      const data = await getAgentTree(taskId);
      setAgentTree(data);
    } catch (err) {
      console.error(err);
    }
  }, [taskId, setAgentTree]);

  const debouncedLoadAgentTree = useCallback(() => {
    const now = Date.now();
    const minInterval = POLLING_INTERVALS.AGENT_TREE_DEBOUNCE;

    if (agentTreeRefreshTimer.current) {
      clearTimeout(agentTreeRefreshTimer.current);
    }

    const timeSinceLastRefresh = now - lastAgentTreeRefreshTime.current;
    if (timeSinceLastRefresh < minInterval) {
      agentTreeRefreshTimer.current = setTimeout(() => {
        lastAgentTreeRefreshTime.current = Date.now();
        loadAgentTree();
      }, minInterval - timeSinceLastRefresh);
    } else {
      agentTreeRefreshTimer.current = setTimeout(() => {
        lastAgentTreeRefreshTime.current = Date.now();
        loadAgentTree();
      }, POLLING_INTERVALS.AGENT_TREE_MIN_DELAY);
    }
  }, [loadAgentTree]);

  // 🔥 NEW: 加载历史事件并转换为日志项
  const loadHistoricalEvents = useCallback(async (force = false) => {
    if (!taskId) return 0;

    // 🔥 防止重复加载历史事件
    if (hasLoadedHistoricalEventsRef.current && !force) {
      console.log('[AgentAudit] Historical events already loaded, skipping');
      return 0;
    }
    hasLoadedHistoricalEventsRef.current = true;

    try {
      console.log(`[AgentAudit] Fetching historical events for task ${taskId}...`);
      // When force-refreshing after completion, only fetch events after the last
      // sequence we've already rendered. Otherwise we'd append the entire history
      // again and show "task complete" above older "task start" logs.
      const afterSeq = force ? lastEventSequenceRef.current : 0;
      // ✅ P2-3: 分页加载历史事件，避免 limit=500 导致大量事件丢失
      let allEvents: any[] = [];
      let currentAfterSeq = afterSeq;
      let hasMore = true;
      while (hasMore) {
        const batch = await getAgentEvents(taskId, { after_sequence: currentAfterSeq, limit: 2000 });
        allEvents = allEvents.concat(batch);
        if (batch.length < 2000) {
          hasMore = false;
        } else {
          currentAfterSeq = batch[batch.length - 1].sequence;
        }
      }
      const events = allEvents;
      console.log(`[AgentAudit] Received ${events.length} events from API`);

      if (events.length === 0) {
        console.log('[AgentAudit] No historical events found');
        return 0;
      }

      // 按 sequence 排序确保顺序正确
      events.sort((a, b) => a.sequence - b.sequence);

      // 转换事件为日志项
      let processedCount = 0;
      events.forEach((event: AgentEvent) => {
        // 去重：跳过已经通过 SSE 处理过的事件
        if (event.sequence <= lastEventSequenceRef.current ) {
          return;
        }
        // 更新最后的事件序列号
        if (event.sequence > lastEventSequenceRef.current) {
          lastEventSequenceRef.current = event.sequence;
        }

        // 提取 agent_name
        const agentName = (event.metadata?.agent_name as string) ||
          (event.metadata?.agent as string) ||
          undefined;

        // 根据事件类型创建日志项
        switch (event.event_type) {
          // LLM 思考相关
          case 'thinking':
          case 'llm_thought':
          case 'llm_decision':
          case 'llm_start':
          case 'llm_complete':
          case 'llm_action':
          case 'llm_observation':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'thinking',
                title: event.message?.slice(0, 100) + (event.message && event.message.length > 100 ? '...' : '') || 'Thinking...',
                content: event.message || (event.metadata?.thought as string) || '',
                agentName,
              }
            });
            processedCount++;
            break;

          // 工具调用相关
          case 'tool_call':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'tool',
                title: `Tool: ${event.tool_name || 'unknown'}`,
                content: event.tool_input ? `Input:\n${JSON.stringify(event.tool_input, null, 2)}` : '',
                tool: {
                  name: event.tool_name || 'unknown',
                  status: 'running' as const,
                },
                agentName,
              }
            });
            processedCount++;
            break;

          case 'tool_result':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'tool',
                title: `Completed: ${event.tool_name || 'unknown'}`,
                content: event.tool_output
                  ? `Output:\n${truncateOutput(typeof event.tool_output === 'string' ? event.tool_output : JSON.stringify(event.tool_output, null, 2))}`
                  : '',
                tool: {
                  name: event.tool_name || 'unknown',
                  duration: event.tool_duration_ms || 0,
                  status: 'completed' as const,
                },
                agentName,
              }
            });
            processedCount++;
            break;

          // 发现漏洞 - 🔥 包含所有 finding 相关事件类型
          case 'finding':
          case 'finding_new':
          case 'finding_verified':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'finding',
                title: event.message || (event.metadata?.title as string) || 'Vulnerability found',
                severity: (event.metadata?.severity as string) || 'medium',
                agentName,
              }
            });
            processedCount++;
            break;

          // 调度和阶段相关
          case 'dispatch':
          case 'dispatch_complete':
          case 'phase_start':
          case 'phase_complete':
          case 'node_start':
          case 'node_complete':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'dispatch',
                title: event.message || `Event: ${event.event_type}`,
                agentName,
              }
            });
            processedCount++;
            break;

          // 任务完成
          case 'task_complete':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'info',
                title: event.message || 'Task completed',
                agentName,
              }
            });
            processedCount++;
            break;

          // 任务错误
          case 'task_error':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'error',
                title: event.message || 'Task error',
                agentName,
              }
            });
            processedCount++;
            break;

          // 任务取消
          case 'task_cancel':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'info',
                title: event.message || 'Task cancelled',
                agentName,
              }
            });
            processedCount++;
            break;

          // 进度事件
          case 'progress':
            // 进度事件使用 UPDATE_OR_ADD_PROGRESS_LOG 来更新而不是添加
            if (event.message) {
              const progressPatterns: { pattern: RegExp; key: string }[] = [
                { pattern: /索引进度[:：]?\s*\d+\/\d+/, key: 'index_progress' },
                { pattern: /克隆进度[:：]?\s*\d+%/, key: 'clone_progress' },
                { pattern: /下载进度[:：]?\s*\d+%/, key: 'download_progress' },
                { pattern: /上传进度[:：]?\s*\d+%/, key: 'upload_progress' },
                { pattern: /扫描进度[:：]?\s*\d+/, key: 'scan_progress' },
                { pattern: /分析进度[:：]?\s*\d+/, key: 'analyze_progress' },
              ];
              const matchedProgress = progressPatterns.find(p => p.pattern.test(event.message || ''));
              if (matchedProgress) {
                dispatch({
                  type: 'UPDATE_OR_ADD_PROGRESS_LOG',
                  payload: {
                    progressKey: matchedProgress.key,
                    title: event.message,
                    agentName,
                  }
                });
              } else {
                dispatch({
                  type: 'ADD_LOG',
                  payload: {
                    type: 'info',
                    title: event.message,
                    agentName,
                  }
                });
              }
              processedCount++;
            }
            break;

          // 信息和错误
          case 'info':
          // Check for progress completion markers
          if (event.message && event.message.startsWith('progress_complete:')) {
            const progressKey = event.message.replace('progress_complete:', '');
            dispatch({
              type: 'COMPLETE_PROGRESS_LOG',
              payload: { progressKey },
            });
            return;
          }
          // Fall through to complete/error/warning for normal info handling
          case 'complete':
          case 'error':
          case 'warning': {
            const message = event.message || `${event.event_type}`;
            // 检测进度类型消息
            const progressPatterns: { pattern: RegExp; key: string }[] = [
              { pattern: /索引进度[:：]?\s*\d+\/\d+/, key: 'index_progress' },
              { pattern: /克隆进度[:：]?\s*\d+%/, key: 'clone_progress' },
              { pattern: /下载进度[:：]?\s*\d+%/, key: 'download_progress' },
              { pattern: /上传进度[:：]?\s*\d+%/, key: 'upload_progress' },
              { pattern: /扫描进度[:：]?\s*\d+/, key: 'scan_progress' },
              { pattern: /分析进度[:：]?\s*\d+/, key: 'analyze_progress' },
            ];
            const matchedProgress = progressPatterns.find(p => p.pattern.test(message));
            if (matchedProgress) {
              dispatch({
                type: 'UPDATE_OR_ADD_PROGRESS_LOG',
                payload: {
                  progressKey: matchedProgress.key,
                  title: message,
                  agentName,
                }
              });
            } else {
              dispatch({
                type: 'ADD_LOG',
                payload: {
                  type: event.event_type === 'error' ? 'error' : 'info',
                  title: message,
                  agentName,
                }
              });
            }
            processedCount++;
            break;
          }

          // ✅ P2-4: thinking 事件展示为状态指示（不跳过）
          case 'thinking_token':
            // 高频 token 事件仍然跳过，避免刷屏
            break;
          case 'thinking_start':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'thinking',
                title: '🧠 Agent 思考中...',
                agentName,
              }
            });
            processedCount++;
            break;
          case 'thinking_end':
            dispatch({
              type: 'ADD_LOG',
              payload: {
                type: 'thinking',
                title: '💭 Agent 思考完成',
                agentName,
              }
            });
            processedCount++;
            break;

          default:
            // 其他事件类型也显示为 info（如果有消息）
            if (event.message) {
              dispatch({
                type: 'ADD_LOG',
                payload: {
                  type: 'info',
                  title: event.message,
                  agentName,
                }
              });
              processedCount++;
            }
        }
      });

      console.log(`[AgentAudit] Processed ${processedCount} events into logs, last sequence: ${lastEventSequenceRef.current}`);
      // 🔥 更新 afterSequence state，触发 streamOptions 重新计算
      setAfterSequence(lastEventSequenceRef.current);
      return events.length;
    } catch (err) {
      console.error('[AgentAudit] Failed to load historical events:', err);
      return 0;
    }
  }, [taskId, dispatch, setAfterSequence]);

  // ============ Stream Event Handling ============

  const streamOptions = useMemo(() => ({
    includeThinking: true,
    includeToolCalls: true,
    // 🔥 使用 state 变量，确保在历史事件加载后能获取最新值
    afterSequence: afterSequence,
    onEvent: (event: { type: string; message?: string; metadata?: { agent_name?: string; agent?: string }; sequence?: number }) => {
      // 🔥 FIX F1: SSE 事件到达时同步更新 lastEventSequenceRef，防止 loadHistoricalEvents 重复拉取
      if (event.sequence && event.sequence > lastEventSequenceRef.current) {
        lastEventSequenceRef.current = event.sequence;
      }
      if (event.metadata?.agent_name) {
        setCurrentAgentName(event.metadata.agent_name);
      }

      const dispatchEvents = ['dispatch', 'dispatch_complete', 'node_start', 'phase_start', 'phase_complete'];
      if (dispatchEvents.includes(event.type)) {
        // 所有 dispatch 类型事件都添加到日志
        dispatch({
          type: 'ADD_LOG',
          payload: {
            type: 'dispatch',
            title: event.message || `Agent dispatch: ${event.metadata?.agent || 'unknown'}`,
            agentName: getCurrentAgentName() || undefined,
          }
        });
        // FIX SSE Wave 1 §InitProgress: phase_start 意味着任务已进入正式执行阶段，
        // 后端 status 已从 INITIALIZING 切换到 RUNNING。前端主动 loadTask() 更新
        // 缓存的 task.status，触发 isInitializing 变 false，从 InitProgress 页
        // 自动切换到主界面，无需用户手动刷新。
        if (event.type === 'phase_start') {
          loadTask();
        }
      }
        debouncedLoadAgentTree();
        return;
      }

      // 🔥 处理 info、warning、error 类型事件（克隆进度、索引进度等）
      const infoEvents = ['info', 'warning', 'error', 'progress'];
      if (infoEvents.includes(event.type)) {
        // Handle init step events
        if (event.metadata?.init_step) {
          dispatch({
            type: 'ADD_INIT_STEP',
            payload: {
              name: event.metadata.init_step as string,
              status: (event.metadata.init_status as 'start' | 'done') || 'start',
            }
          });
        }
        const message = event.message || event.type;

        // 🔥 检测进度类型消息，使用更新而不是添加
        const progressPatterns: { pattern: RegExp; key: string }[] = [
          { pattern: /索引进度[:：]?\s*\d+\/\d+/, key: 'index_progress' },
          { pattern: /克隆进度[:：]?\s*\d+%/, key: 'clone_progress' },
          { pattern: /下载进度[:：]?\s*\d+%/, key: 'download_progress' },
          { pattern: /上传进度[:：]?\s*\d+%/, key: 'upload_progress' },
          { pattern: /扫描进度[:：]?\s*\d+/, key: 'scan_progress' },
          { pattern: /分析进度[:：]?\s*\d+/, key: 'analyze_progress' },
        ];

        const matchedProgress = progressPatterns.find(p => p.pattern.test(message));

        if (matchedProgress) {
          // 使用 UPDATE_OR_ADD_PROGRESS_LOG 来更新进度而不是添加新日志
          dispatch({
            type: 'UPDATE_OR_ADD_PROGRESS_LOG',
            payload: {
              progressKey: matchedProgress.key,
              title: message,
              agentName: getCurrentAgentName() || undefined,
            }
          });
        } else {
          // 非进度消息正常添加
          dispatch({
            type: 'ADD_LOG',
            payload: {
              type: event.type === 'error' ? 'error' : 'info',
              title: message,
              agentName: getCurrentAgentName() || undefined,
            }
          });
        }
        return;
      }
    },
    onThinkingStart: () => {
      const currentId = getCurrentThinkingId();
      if (currentId) {
        updateLog(currentId, { isStreaming: false });
      }
      setCurrentThinkingId(null);
    },
    onThinkingToken: (_token: string, accumulated: string) => {
      if (!accumulated?.trim()) return;
      const cleanContent = cleanThinkingContent(accumulated);
      if (!cleanContent) return;

      const currentId = getCurrentThinkingId();
      if (!currentId) {
        // 预生成 ID，这样我们可以跟踪这个日志
        const newLogId = `thinking-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        dispatch({
          type: 'ADD_LOG', payload: {
            id: newLogId,
            type: 'thinking',
            title: 'Thinking...',
            content: cleanContent,
            isStreaming: true,
            agentName: getCurrentAgentName() || undefined,
          }
        });
        setCurrentThinkingId(newLogId);
      } else {
        updateLog(currentId, { content: cleanContent });
      }
    },
    onThinkingEnd: (response: string) => {
      const cleanResponse = cleanThinkingContent(response || "");
      const currentId = getCurrentThinkingId();

      if (!cleanResponse) {
        if (currentId) {
          removeLog(currentId);
        }
        setCurrentThinkingId(null);
        return;
      }

      if (currentId) {
        updateLog(currentId, {
          title: cleanResponse.slice(0, 100) + (cleanResponse.length > 100 ? '...' : ''),
          content: cleanResponse,
          isStreaming: false
        });
        setCurrentThinkingId(null);
      }
    },
    onToolStart: (name: string, input: Record<string, unknown>) => {
      const currentId = getCurrentThinkingId();
      if (currentId) {
        updateLog(currentId, { isStreaming: false });
        setCurrentThinkingId(null);
      }
      dispatch({
        type: 'ADD_LOG',
        payload: {
          type: 'tool',
          title: `Tool: ${name}`,
          content: `Input:\n${JSON.stringify(input, null, 2)}`,
          tool: { name, status: 'running' },
          agentName: getCurrentAgentName() || undefined,
        }
      });
    },
    onToolEnd: (name: string, output: unknown, duration: number) => {
      const outputStr = typeof output === 'string' ? output : JSON.stringify(output, null, 2);
      dispatch({
        type: 'COMPLETE_TOOL_LOG',
        payload: {
          toolName: name,
          output: truncateOutput(outputStr),
          duration,
        }
      });
    },
    onFinding: (finding: Record<string, unknown>) => {
      dispatch({
        type: 'ADD_LOG',
        payload: {
          type: 'finding',
          title: (finding.title as string) || 'Vulnerability found',
          severity: (finding.severity as string) || 'medium',
          agentName: getCurrentAgentName() || undefined,
        }
      });
      // 🔥 直接将 finding 添加到状态，不依赖 API（因为运行时数据库还没有数据）
      dispatch({
        type: 'ADD_FINDING',
        payload: {
          id: (finding.id as string) || `finding-${Date.now()}`,
          title: (finding.title as string) || 'Vulnerability found',
          severity: (finding.severity as string) || 'medium',
          vulnerability_type: (finding.vulnerability_type as string) || 'unknown',
          file_path: finding.file_path as string,
          line_start: finding.line_start as number,
          description: finding.description as string,
          is_verified: (finding.is_verified as boolean) || false,
        }
      });
    },
    onComplete: async () => {
      // 🔥 FIX F3: 标记 SSE 路径已触发完成，防止 finalizeTask 重复加载
      if (hasCompletedViaSSE.current) return;
      hasCompletedViaSSE.current = true;
      dispatch({ type: 'ADD_LOG', payload: { type: 'info', title: 'Audit completed successfully' } });
      dispatch({ type: 'COMPLETE_ALL_RUNNING_TOOLS' });
      await loadTask();
      await loadFindings();
      await loadAgentTree();
      await loadHistoricalEvents(true);
    },
    onTaskUpdate: async () => {
      // 🔥 任务状态变更（如暂停）后刷新 task 状态，使 isPaused 立即生效，渲染"继续"按钮
      dispatch({ type: 'ADD_LOG', payload: { type: 'info', title: '任务已暂停' } });
      await loadTask();
    },
    onError: (err: string) => {
      dispatch({ type: 'ADD_LOG', payload: { type: 'error', title: `Error: ${err}` } });
    },
    onConnectionStateChange: (state: ConnectionState) => {
      setConnectionStatus(state);
    },
    onReconnect: (attempt: number, reason?: string) => {
      dispatch({
        type: 'ADD_LOG',
        payload: {
          type: 'info',
          title: `Stream reconnecting (${attempt}/5)${reason ? `: ${reason}` : ''}...`,
        }
      });
    },
    onMaxRetriesReached: async () => {
      dispatch({
        type: 'ADD_LOG',
        payload: {
          type: 'error',
          title: 'Stream connection failed after 5 retries',
        }
      });
      await loadTask();
      await loadFindings();
      await loadAgentTree();
      await loadHistoricalEvents(true);
    },
  }), [afterSequence, dispatch, loadTask, loadFindings, loadAgentTree, debouncedLoadAgentTree,
    updateLog, removeLog, getCurrentAgentName, getCurrentThinkingId,
    setCurrentAgentName, setCurrentThinkingId]);

  const {
  connect: connectStream,
  disconnect: disconnectStream,
  connectionState,
  isConnected,
  reconnectAttempts,
  isFailed,
} = useResilientStream(taskId || null, streamOptions);

  // 保存 disconnect 函数到 ref，以便在 taskId 变化时使用
  useEffect(() => {
    disconnectStreamRef.current = disconnectStream;
  }, [disconnectStream]);

  // ============ Effects ============

  // Status animation
  useEffect(() => {
    if (!isRunning) return;
    const dotTimer = setInterval(() => setStatusDots(d => (d + 1) % 4), 500);
    const verbTimer = setInterval(() => {
      setStatusVerb(ACTION_VERBS[Math.floor(Math.random() * ACTION_VERBS.length)]);
    }, 5000);
    return () => {
      clearInterval(dotTimer);
      clearInterval(verbTimer);
    };
  }, [isRunning]);

  // Initial load - 🔥 加载任务数据和历史事件
  useEffect(() => {
    if (!taskId) {
      setShowSplash(true);
      return;
    }
    setShowSplash(false);
    setLoading(true);
    setHistoricalEventsLoaded(false);

    const loadAllData = async () => {
      try {
        // 先加载任务基本信息
        await Promise.all([loadTask(), loadFindings(), loadAgentTree()]);

        // 🔥 加载历史事件 - 无论任务是否运行都需要加载
        const eventsLoaded = await loadHistoricalEvents();
        console.log(`[AgentAudit] Loaded ${eventsLoaded} historical events for task ${taskId}`);

        // 标记历史事件已加载完成 (setAfterSequence 已在 loadHistoricalEvents 中调用)
        setHistoricalEventsLoaded(true);
      } catch (error) {
        console.error('[AgentAudit] Failed to load data:', error);
        setHistoricalEventsLoaded(true); // 即使出错也标记为完成，避免无限等待
      } finally {
        setLoading(false);
      }
    };

    loadAllData();
  }, [taskId, loadTask, loadFindings, loadAgentTree, loadHistoricalEvents, setLoading]);

  // Stream connection - 🔥 在历史事件加载完成后连接
  useEffect(() => {
    // 等待历史事件加载完成，且任务正在运行
    if (!taskId || !task?.status) return;
    if (!['pending', 'initializing', 'running'].includes(task.status)) return;

    // 🔥 使用 state 变量确保在历史事件加载完成后才连接
    if (!historicalEventsLoaded) return;

    // 🔥 避免重复连接 - 只连接一次
    if (hasConnectedRef.current) return;

    // Sync afterSequence from ref to state before connecting
    if (lastEventSequenceRef.current > 0) {
      setAfterSequence(lastEventSequenceRef.current);
    }

    hasConnectedRef.current = true;
    console.log(`[AgentAudit] Connecting to stream (afterSequence will be passed via streamOptions)`);
    connectStream();
    dispatch({ type: 'ADD_LOG', payload: { type: 'info', title: 'Connected to audit stream' } });

    return () => {
      console.log('[AgentAudit] Cleanup: disconnecting stream');
      disconnectStream();
      // FIX SSE Wave 1 §2.5: 复位 hasConnectedRef，允许 React 18 StrictMode 双挂载
      // 以及运行时断开后重新连接。若不复位，第二次 mount 时 hasConnectedRef.current === true
      // 会直接 early return，导致断流后（心跳超时、fetch 失败）无法自愈。
      hasConnectedRef.current = false;
    };
    // 🔥 CRITICAL FIX: 移除 afterSequence 依赖！
    // afterSequence 通过 streamOptions 传递，不需要在这里触发重连
    // 如果包含它，当 loadHistoricalEvents 更新 afterSequence 时会触发断开重连
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, task?.status, historicalEventsLoaded, connectStream, disconnectStream, dispatch]);

  useEffect(() => {
    if (!isPaused) return;
    disconnectStream();
    hasConnectedRef.current = false;
    setConnectionStatus('disconnected');
  }, [disconnectStream, isPaused, setConnectionStatus]);

  // Polling
  useEffect(() => {
    if (!taskId || !isRunning) return;
    const interval = setInterval(loadAgentTree, POLLING_INTERVALS.AGENT_TREE);
    return () => clearInterval(interval);
  }, [taskId, isRunning, loadAgentTree]);

  useEffect(() => {
    if (!taskId || !isRunning) return;
    const interval = setInterval(loadTask, POLLING_INTERVALS.TASK_STATS);
    return () => clearInterval(interval);
  }, [taskId, isRunning, loadTask]);

  // Task completion transition — detach SSE, load final data
  const hasTransitionedRef = useRef(false);
  const hasCompletedViaSSE = useRef(false); // FIX F3: prevent duplicate completion

  useEffect(() => {
    if (!taskId || !task?.status) return;

    const terminalStatuses = ['completed', 'completed_with_gaps', 'failed', 'cancelled'];
    if (terminalStatuses.includes(task.status) && !hasTransitionedRef.current) {
      hasTransitionedRef.current = true;
      console.log(`[AgentAudit] Task reached terminal status: ${task.status}, transitioning UI`);
      const finalizeTask = async () => {
        // 🔥 FIX F3: 如果 SSE 路径已经触发过完成，跳过重复加载
        if (hasCompletedViaSSE.current) {
          console.log('[AgentAudit] Already completed via SSE, skipping finalizeTask reload');
          return;
        }
        hasCompletedViaSSE.current = true;
        disconnectStream();
        hasConnectedRef.current = false;
        dispatch({ type: 'COMPLETE_ALL_RUNNING_TOOLS' });
        await Promise.all([loadTask(), loadFindings(), loadAgentTree()]);
        await loadHistoricalEvents(true);
      };
      finalizeTask();
    }
  }, [taskId, task?.status, disconnectStream, loadTask, loadFindings, loadAgentTree, loadHistoricalEvents]);

  // Auto scroll（仅滚动 Log Content 容器本身，不牵连任何祖先）
  useEffect(() => {
    if (isAutoScroll && logContainerRef.current) {
      const el = logContainerRef.current;
      el.scrollTop = el.scrollHeight;
    }
  }, [logs, isAutoScroll]);

  // ============ Handlers ============

  const handleAgentSelect = useCallback((agentId: string) => {
    if (selectedAgentId === agentId) {
      selectAgent(null);
    } else {
      selectAgent(agentId);
    }
  }, [selectedAgentId, selectAgent]);

  const handlePause = async () => {
    if (!taskId || isPausing) return;
    setIsPausing(true);
    dispatch({ type: 'ADD_LOG', payload: { type: 'info', title: '正在请求暂停任务...' } });

    try {
      const result = await pauseAgentTask(taskId);
      toast.success(result.message || "任务已暂停");
      dispatch({ type: 'ADD_LOG', payload: { type: 'info', title: '任务已暂停，可稍后继续执行' } });
      await loadTask();
      disconnectStream();
      hasConnectedRef.current = false;
    } catch (error) {
      const detail = (error as any)?.response?.data?.detail;
      const errorMessage = detail || (error instanceof Error ? error.message : 'Unknown error');
      toast.error(`暂停任务失败: ${errorMessage}`);
      dispatch({ type: 'ADD_LOG', payload: { type: 'error', title: `暂停失败: ${errorMessage}` } });
    } finally {
      setIsPausing(false);
    }
  };

  const [isReAuditing, setIsReAuditing] = useState(false);
  const [isRecovering, setIsRecovering] = useState(false);

  const handleReAudit = async () => {
    if (!taskId) return;
    setIsReAuditing(true);
    try {
      const result = await reAuditAgentTask(taskId);
      toast.success(result.message || 'Re-audit started');
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      toast.error('Re-audit failed: ' + errorMessage);
    } finally {
      setIsReAuditing(false);
    }
  };

  const handleRecover = async () => {
    if (!taskId) return;
    setIsRecovering(true);
    try {
      const result = await recoverAgentTask(taskId);
      toast.success(result.message || 'Task recovered');
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      toast.error('Recovery failed: ' + errorMessage);
    } finally {
      setIsRecovering(false);
    }
  };

  const handleResume = async () => {
    if (!taskId || isResuming) return;
    setIsResuming(true);
    dispatch({ type: 'ADD_LOG', payload: { type: 'info', title: '正在从检查点恢复任务...' } });

    try {
      const result = await resumeAgentTask(taskId);
      toast.success(result.message || "任务已继续");
      dispatch({ type: 'ADD_LOG', payload: { type: 'info', title: '任务已恢复，正在重新建立实时连接' } });
      hasConnectedRef.current = false;
      hasTransitionedRef.current = false;
      hasCompletedViaSSE.current = false;
      await Promise.all([loadTask(), loadFindings(), loadAgentTree()]);
    } catch (error) {
      const detail = (error as any)?.response?.data?.detail;
      const errorMessage = detail || (error instanceof Error ? error.message : 'Unknown error');
      toast.error(`继续任务失败: ${errorMessage}`);
      dispatch({ type: 'ADD_LOG', payload: { type: 'error', title: `继续失败: ${errorMessage}` } });
    } finally {
      setIsResuming(false);
    }
  };

  const handleExportReport = () => {
    if (!task) return;
    setShowExportDialog(true);
  };

  const handlePanelResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startWidth = panelWidth;
    const onMove = (ev: MouseEvent) => {
      const delta = startX - ev.clientX;
      const next = Math.min(900, Math.max(360, startWidth + delta));
      setPanelWidth(next);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [panelWidth, setPanelWidth]);

  const aiContext = useMemo(() => buildAiContextSummary({
    task,
    agentTree,
    findings,
    logs,
    selectedAgentId,
  }), [task, agentTree, findings, logs, selectedAgentId]);

  const handleAiSendMessage = useCallback(async (message: string) => {
    dispatch({
      type: 'ADD_LOG',
      payload: {
        type: 'user',
        title: 'AI 协同指令',
        content: message,
      },
    });

    if (!taskId) {
      return "当前没有可用的审计任务上下文。";
    }

    const response = await chatWithAgentTask(taskId, { message });
    dispatch({
      type: 'ADD_LOG',
      payload: {
        type: 'info',
        title: 'AI 协同回复',
        content: response.reply,
      },
    });
    return response.reply;
  }, [dispatch, taskId]);

  const handleContinueAudit = useCallback(() => {
    dispatch({
      type: 'ADD_LOG',
      payload: {
        type: 'dispatch',
        title: 'AI 请求继续审计',
        content: '用户从 AI 协同栏请求继续推进当前审计任务。',
      },
    });
  }, [dispatch]);

  const handleRerunPoc = useCallback((findingId: string) => {
    dispatch({
      type: 'ADD_LOG',
      payload: {
        type: 'dispatch',
        title: 'AI 请求重新验证 PoC',
        content: `用户请求重新验证 finding: ${findingId}`,
      },
    });
  }, [dispatch]);

  // ============ Render ============

  if (showSplash && !taskId) {
    return (
      <>
        <SplashScreen onComplete={() => setShowCreateDialog(true)} />
        <CreateAgentTaskDialog open={showCreateDialog} onOpenChange={setShowCreateDialog} />
      </>
    );
  }

  if (isLoading && !task) {
    return (
      <div className="h-screen bg-background flex items-center justify-center relative overflow-hidden">
        {/* Grid background */}
        <div className="absolute inset-0 cyber-grid opacity-30" />
        {/* Vignette */}
        <div className="absolute inset-0 vignette pointer-events-none" />
        <div className="flex items-center gap-3 text-muted-foreground relative z-10">
          <Loader2 className="w-5 h-5 animate-spin text-primary" />
          <span className="font-mono text-sm tracking-wide">LOADING AUDIT TASK...</span>
        </div>
      </div>
    );
  }

  if (isInitializing && task) {
    return (
      <div className="h-screen bg-background flex items-center justify-center relative overflow-hidden">
        <div className="absolute inset-0 cyber-grid opacity-30" />
        <div className="absolute inset-0 vignette pointer-events-none" />
        <InitProgress steps={initSteps} />
      </div>
    );
  }

  return (
    <div className="h-full bg-background flex flex-col overflow-hidden relative">

      {/* Header */}
      <Header
        task={task}
        isRunning={isRunning}
        isPaused={isPaused}
        isPausing={isPausing}
        isResuming={isResuming}
        onPause={handlePause}
        onResume={handleResume}
        onExport={handleExportReport}
        onNewAudit={() => setShowCreateDialog(true)}
        onOpenAiPanel={() => setShowAiPanel(true)}
      />

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Reconnecting banner */}
        {connectionState === 'reconnecting' && (
          <div className="absolute top-0 left-0 right-0 z-10 bg-yellow-50 border-b border-yellow-200 px-4 py-2 flex items-center gap-2">
            <RefreshCw className="w-4 h-4 text-yellow-600 animate-spin" />
            <span className="text-sm text-yellow-700">
              连接中断，正在重连 ({reconnectAttempts}/5)...
            </span>
          </div>
        )}

        {/* Failed banner */}
        {isFailed && (
          <div className="absolute top-0 left-0 right-0 z-10 bg-red-50 border-b border-red-200 px-4 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-600" />
              <span className="text-sm text-red-700">
                连接已断开，无法恢复。请刷新页面或返回审计任务列表。
              </span>
            </div>
            <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
              刷新页面
            </Button>
          </div>
        )}

        {isPaused && (
          <div className="absolute top-0 left-0 right-0 z-10 bg-amber-50 border-b border-amber-200 px-4 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-amber-600" />
              <span className="text-sm text-amber-700">
                任务已暂停{task?.pause_reason ? `，原因：${task.pause_reason}` : ""}{task?.last_error_code ? `（错误码：${task.last_error_code}）` : ""}。
              </span>
            </div>
            <Button size="sm" onClick={handleResume} disabled={isResuming}>
              {isResuming ? "继续中..." : "继续执行"}
            </Button>
          </div>
        )}

        {canReAudit && (
          <div className="absolute top-0 left-0 right-0 z-10 bg-blue-50 border-b border-blue-200 px-4 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-blue-600" />
              <span className="text-sm text-blue-700">
                {"任务已完成但存在未验证的漏洞，可补充审计。"}
              </span>
            </div>
            <Button size="sm" onClick={handleReAudit} disabled={isReAuditing}>
              {isReAuditing ? "审计中..." : "补充审计"}
            </Button>
          </div>
        )}

        {canRecover && !isRunning && (
          <div className="absolute top-0 left-0 right-0 z-10 bg-red-50 border-b border-red-200 px-4 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-600" />
              <span className="text-sm text-red-700">
                {"任务可能已断开，点击恢复后可继续执行。"}
              </span>
            </div>
            <Button size="sm" onClick={handleRecover} disabled={isRecovering}>
              {isRecovering ? "恢复中..." : "恢复任务"}
            </Button>
          </div>
        )}

        {/* Left Panel - Activity Log */}
        <div className="w-[72%] flex flex-col border-r border-border relative min-w-0">
          {/* Log header */}
          <div className="flex-shrink-0 h-12 border-b border-border flex items-center justify-between px-5 bg-card">
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <div className="flex items-center gap-2.5">
                <Terminal className="w-4 h-4 text-primary" />
                <span className="uppercase font-bold tracking-wider text-foreground text-sm">Activity Log</span>
              </div>
              <ConnectionStatus
                state={connectionState}
                reconnectAttempts={reconnectAttempts}
                maxReconnectAttempts={5}
              />
              <Badge variant="outline" className="h-6 px-2 text-xs border-border text-muted-foreground font-mono bg-muted">
                {filteredLogs.length}{!showAllLogs && logs.length !== filteredLogs.length ? ` / ${logs.length}` : ''} entries
              </Badge>
            </div>

            <button
              onClick={() => setAutoScroll(!isAutoScroll)}
              className={`
                flex items-center gap-2 text-xs px-3 py-1.5 rounded-md font-mono uppercase tracking-wider
                ${isAutoScroll
                  ? 'bg-primary/15 text-primary border border-primary/50'
                  : 'text-muted-foreground hover:text-foreground border border-border hover:bg-muted'
                }
              `}
            >
              <ArrowDown className="w-3.5 h-3.5" />
              <span>Auto-scroll</span>
            </button>
          </div>

          {/* Log content */}
          <div ref={logContainerRef} className="flex-1 min-h-0 overflow-y-auto p-5 custom-scrollbar bg-muted/30">
            {/* Filter indicator */}
            {selectedAgentId && !showAllLogs && (
              <div className="mb-4 px-4 py-2.5 bg-primary/10 border border-primary/30 rounded-lg flex items-center justify-between">
                <div className="flex items-center gap-2.5 text-sm text-primary">
                  <Filter className="w-3.5 h-3.5" />
                  <span className="font-medium">Filtering logs for selected agent</span>
                </div>
                <button
                  onClick={() => selectAgent(null)}
                  className="text-xs text-muted-foreground hover:text-primary font-mono uppercase px-2 py-1 rounded hover:bg-primary/10"
                >
                  Clear Filter
                </button>
              </div>
            )}

            {/* Logs */}
            {filteredLogs.length === 0 ? (
              <div className="h-full flex items-center justify-center">
                <div className="text-center text-muted-foreground">
                  {isRunning ? (
                    <div className="flex flex-col items-center gap-3">
                      <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                      <span className="text-sm font-mono tracking-wide">
                        {selectedAgentId && !showAllLogs
                          ? 'WAITING FOR ACTIVITY FROM SELECTED AGENT...'
                          : 'WAITING FOR AGENT ACTIVITY...'}
                      </span>
                    </div>
                  ) : isPaused ? (
                    <span className="text-sm font-mono tracking-wide">
                      {selectedAgentId && !showAllLogs
                        ? 'SELECTED AGENT PAUSED'
                        : 'TASK PAUSED'}
                    </span>
                  ) : (
                    <span className="text-sm font-mono tracking-wide">
                      {selectedAgentId && !showAllLogs
                        ? 'NO ACTIVITY FROM SELECTED AGENT'
                        : 'NO ACTIVITY YET'}
                    </span>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                {filteredLogs.map(item => (
                  <LogEntry
                    key={item.id}
                    item={item}
                    isExpanded={expandedLogIds.has(item.id)}
                    onToggle={() => toggleLogExpanded(item.id)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Status bar */}
          {task && (
            <div className="flex-shrink-0 h-10 border-t border-border flex items-center justify-between px-5 text-xs bg-card relative overflow-hidden">
              {/* Progress bar background */}
              <div
                className="absolute inset-0 bg-primary/10"
                style={{ width: `${task.progress_percentage || 0}%` }}
              />

              <span className="relative z-10">
                {isRunning ? (
                  <span className="flex items-center gap-2.5 text-emerald-600 dark:text-emerald-400">
                    <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
                    <span className="font-mono font-semibold">{statusVerb}{'.'.repeat(statusDots)}</span>
                  </span>
                ) : isPaused ? (
                  <span className="flex items-center gap-2 text-amber-600 dark:text-amber-400 font-mono">
                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                    TASK PAUSED
                  </span>
                ) : isComplete ? (
                  <span className="flex items-center gap-2 text-muted-foreground font-mono">
                    <span className={`w-2 h-2 rounded-full ${task.status === 'completed' ? 'bg-emerald-500' : task.status === 'failed' ? 'bg-rose-500' : 'bg-amber-500'}`} />
                    AUDIT {task.status?.toUpperCase()}
                  </span>
                ) : (
                  <span className="text-muted-foreground font-mono">READY</span>
                )}
              </span>
              <div className="flex items-center gap-5 font-mono text-muted-foreground relative z-10">
                <div className="flex items-center gap-1.5">
                  <span className="text-primary font-bold text-sm">{task.progress_percentage?.toFixed(0) || 0}</span>
                  <span className="text-muted-foreground text-xs">%</span>
                </div>
                <div className="w-px h-4 bg-border" />
                <div className="flex items-center gap-1.5">
                  <span className="text-foreground font-semibold">{task.analyzed_files}</span>
                  <span className="text-muted-foreground">/ {task.total_files}</span>
                  <span className="text-muted-foreground text-xs">files</span>
                </div>
                <div className="w-px h-4 bg-border" />
                <div className="flex items-center gap-1.5">
                  <span className="text-foreground font-semibold">{task.tool_calls_count || 0}</span>
                  <span className="text-muted-foreground text-xs">tools</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Right Panel - Agent Tree / Stats / AI Collaboration */}
        <div className="w-[28%] flex flex-col bg-background relative min-w-[340px]">
          {/* Agent Tree section */}
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
            {/* Tree header */}
            <div className="flex-shrink-0 h-12 border-b border-border flex items-center justify-between px-4 bg-card">
              <div className="flex items-center gap-2.5 text-xs text-muted-foreground">
                <Bot className="w-4 h-4 text-violet-600 dark:text-violet-500" />
                <span className="uppercase font-bold tracking-wider text-foreground text-sm">
                  {selectedAgentId && !showAllLogs ? 'Agent Detail' : 'Agent Tree'}
                </span>
                {!selectedAgentId && agentTree && (
                  <Badge variant="outline" className="h-5 px-2 text-xs border-violet-500/30 text-violet-600 dark:text-violet-500 font-mono bg-violet-500/10">
                    {agentTree.total_agents}
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-2">
                {selectedAgentId && !showAllLogs && (
                  <button
                    onClick={() => selectAgent(null)}
                    className="text-xs text-primary hover:text-primary/80 font-mono uppercase px-2 py-1 rounded hover:bg-primary/10"
                  >
                    Back
                  </button>
                )}
                {!selectedAgentId && agentTree && agentTree.running_agents > 0 && (
                  <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                    <span className="text-xs font-mono text-emerald-600 dark:text-emerald-400 font-semibold">{agentTree.running_agents}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Tree content or Agent Detail */}
            <div className="flex-1 overflow-y-auto p-3 custom-scrollbar bg-muted/20">
              {selectedAgentId && !showAllLogs ? (
                /* Agent Detail Panel - 覆盖整个内容区域 */
                <AgentDetailPanel
                  agentId={selectedAgentId}
                  treeNodes={treeNodes}
                  onClose={() => selectAgent(null)}
                />
              ) : treeNodes.length > 0 ? (
                <div className="space-y-0.5">
                  {treeNodes.map(node => (
                    <AgentTreeNodeItem
                      key={node.agent_id}
                      node={node}
                      selectedId={selectedAgentId}
                      onSelect={handleAgentSelect}
                    />
                  ))}
                </div>
              ) : (
                <div className="h-full flex items-center justify-center text-muted-foreground text-xs">
                  {isRunning ? (
                    <div className="flex flex-col items-center gap-3 p-6">
                      <Loader2 className="w-6 h-6 animate-spin text-violet-600 dark:text-violet-500" />
                      <span className="font-mono text-center">INITIALIZING<br/>AGENTS...</span>
                    </div>
                  ) : isPaused ? (
                    <div className="flex flex-col items-center gap-2 p-6 text-center">
                      <AlertCircle className="w-8 h-8 text-amber-500" />
                      <span className="font-mono">TASK PAUSED</span>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-2 p-6 text-center">
                      <Bot className="w-8 h-8 text-muted-foreground/50" />
                      <span className="font-mono">NO AGENTS YET</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Middle section - Stats */}
          <div className="flex-shrink-0 border-t border-border bg-card p-3">
            <StatsPanel task={task} findings={findings} compact />
          </div>
        </div>
      </div>

      {/* Create dialog */}
      <CreateAgentTaskDialog open={showCreateDialog} onOpenChange={setShowCreateDialog} />

      {/* AI 协同抽屉 */}
      <Sheet open={showAiPanel} onOpenChange={setShowAiPanel}>
        <SheetContent
          side="right"
          style={{ width: `${panelWidth}px` }}
          className="sm:max-w-none p-0 flex flex-col"
        >
          <div
            onMouseDown={handlePanelResize}
            className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize bg-border hover:bg-primary/40 transition-colors z-10"
          />
          <SheetHeader className="px-4 py-3 border-b border-border">
            <SheetTitle className="text-base">AI 协同</SheetTitle>
          </SheetHeader>
          <div className="flex-1 min-h-0 overflow-hidden">
            <AICollaborationPanel
              context={aiContext}
              isRunning={isRunning}
              onSendMessage={handleAiSendMessage}
              onRequestContinueAudit={handleContinueAudit}
              onRequestRerunPoc={handleRerunPoc}
              taskId={taskId}
            />
          </div>
        </SheetContent>
      </Sheet>

      {/* Export dialog */}
      <ReportExportDialog
        open={showExportDialog}
        onOpenChange={setShowExportDialog}
        task={task}
        findings={findings}
      />
    </div>
  );
}

// Wrapped export with Error Boundary
export default function AgentAuditPage() {
  const { taskId } = useParams<{ taskId: string }>();

  return (
    <AgentErrorBoundary
      taskId={taskId}
      onRetry={() => window.location.reload()}
    >
      <AgentAuditPageContent />
    </AgentErrorBoundary>
  );
}
