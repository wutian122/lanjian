/**
 * Resilient Stream Hook
 * Enhanced stream connection with automatic reconnection, heartbeat monitoring,
 * and exponential backoff
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { StreamOptions, StreamEventData } from '@/shared/api/agentStream';
import {
  getEffectiveAfterSequence,
  getHeartbeatTimeoutMs,
  getNextLongOperationState,
} from './resilientStreamPolicy';

// ============ Types ============

export type ConnectionState =
  | 'disconnected'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'failed';

export interface ResilientStreamConfig {
  maxReconnectAttempts?: number;
  initialReconnectDelay?: number;
  maxReconnectDelay?: number;
  heartbeatTimeout?: number;
  longOperationHeartbeatTimeout?: number;
  jitterFactor?: number;
}

export interface ResilientStreamState {
  connectionState: ConnectionState;
  reconnectAttempts: number;
  lastHeartbeat: Date | null;
  error: string | null;
}

interface UseResilientStreamOptions extends StreamOptions {
  autoConnect?: boolean;
  config?: ResilientStreamConfig;
  onConnectionStateChange?: (state: ConnectionState) => void;
  onReconnect?: (attempt: number, reason?: string) => void;
  onMaxRetriesReached?: () => void;
}

// ============ Default Configuration ============

const DEFAULT_CONFIG: Required<ResilientStreamConfig> = {
  maxReconnectAttempts: 5,
  initialReconnectDelay: 1000,
  maxReconnectDelay: 30000,
  heartbeatTimeout: 45000, // 45 seconds
  longOperationHeartbeatTimeout: 180000, // Long tool calls such as semgrep can run up to 120s.
  jitterFactor: 0.3,
};

// ============ Hook ============

export function useResilientStream(
  taskId: string | null,
  options: UseResilientStreamOptions = {}
) {
  const {
    autoConnect = false,
    config: userConfig,
    onConnectionStateChange,
    onReconnect,
    onMaxRetriesReached,
    ...streamOptions
  } = options;

  // FIX SSE Post-Wave 2: config 用 useMemo 稳定化。
  // 之前 `const config = { ...DEFAULT_CONFIG, ...userConfig };` 每次 hook 调用（每次 rerender）
  // 都创建新对象，导致下游所有依赖 config.* 的 useCallback（resetHeartbeatTimer、handleHeartbeat、
  // handleEvent、connectInternal）identity 每次变化，最终引发 index.tsx 的 stream connection
  // useEffect 依赖变动误 cleanup + 重建 SSE 连接。真实故障：SSE 每秒断连一次。
  const config = useMemo(
    () => ({ ...DEFAULT_CONFIG, ...userConfig }),
    [
      userConfig?.maxReconnectAttempts,
      userConfig?.initialReconnectDelay,
      userConfig?.maxReconnectDelay,
      userConfig?.heartbeatTimeout,
      userConfig?.longOperationHeartbeatTimeout,
      userConfig?.jitterFactor,
    ]
  );

  // State
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [lastHeartbeat, setLastHeartbeat] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  // Refs
  const abortControllerRef = useRef<AbortController | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamOptionsRef = useRef(streamOptions);
  const isDisconnectingRef = useRef(false);
  const handleReconnectRef = useRef<(reason?: string) => void>(() => {});
  const connectInternalRef = useRef<() => Promise<void>>(async () => {});
  const latestSeenSequenceRef = useRef(0);
  const lastActivityAtRef = useRef(Date.now());
  const inLongOperationRef = useRef(false);

  // Update refs when options change
  streamOptionsRef.current = streamOptions;

  // ============ Connection State Management ============

  const connectionStateRef = useRef<ConnectionState>('disconnected');

  const updateConnectionState = useCallback((newState: ConnectionState) => {
    connectionStateRef.current = newState;
    setConnectionState(newState);
    setIsConnected(newState === 'connected');
    onConnectionStateChange?.(newState);
  }, [onConnectionStateChange]);

  // ============ Heartbeat Monitoring ============

  const resetHeartbeatTimer = useCallback(() => {
    if (heartbeatTimeoutRef.current) {
      clearTimeout(heartbeatTimeoutRef.current);
    }

    const timeoutMs = getHeartbeatTimeoutMs({
      defaultTimeoutMs: config.heartbeatTimeout,
      longOperationTimeoutMs: config.longOperationHeartbeatTimeout,
      inLongOperation: inLongOperationRef.current,
    });

    heartbeatTimeoutRef.current = setTimeout(() => {
      const elapsed = Math.round((Date.now() - lastActivityAtRef.current) / 1000);
      console.warn(`[ResilientStream] Heartbeat timeout after ${elapsed}s idle (timeout=${timeoutMs}ms) - connection may be stale`);
      if (!isDisconnectingRef.current && connectionStateRef.current === 'connected') {
        console.log(`[ResilientStream] Triggering reconnect due to heartbeat timeout`);
        handleReconnectRef.current('heartbeat timeout');
      }
    }, timeoutMs);
  }, [config.heartbeatTimeout, config.longOperationHeartbeatTimeout]);

  const handleHeartbeat = useCallback(() => {
    const now = new Date();
    lastActivityAtRef.current = now.getTime();
    setLastHeartbeat(now);
    resetHeartbeatTimer();
  }, [resetHeartbeatTimer]);

  // ============ Reconnection Logic ============

  const calculateReconnectDelay = useCallback((attempt: number): number => {
    // Exponential backoff with jitter
    const baseDelay = config.initialReconnectDelay * Math.pow(2, attempt);
    const cappedDelay = Math.min(baseDelay, config.maxReconnectDelay);
    const jitter = cappedDelay * config.jitterFactor * (Math.random() - 0.5) * 2;
    return Math.round(cappedDelay + jitter);
  }, [config.initialReconnectDelay, config.maxReconnectDelay, config.jitterFactor]);

  const handleReconnect = useCallback((reason = 'connection error') => {
    if (isDisconnectingRef.current) return;

    const newAttempt = reconnectAttempts + 1;

    if (newAttempt > config.maxReconnectAttempts) {
      console.error('[ResilientStream] Max reconnect attempts reached');
      updateConnectionState('failed');
      setError('Maximum reconnection attempts reached');
      onMaxRetriesReached?.();
      return;
    }

    updateConnectionState('reconnecting');
    setReconnectAttempts(newAttempt);
    onReconnect?.(newAttempt, reason);

    const delay = calculateReconnectDelay(newAttempt - 1);
    console.log(`[ResilientStream] Reconnecting in ${delay}ms (attempt ${newAttempt}/${config.maxReconnectAttempts}, reason=${reason})`);

    reconnectTimeoutRef.current = setTimeout(() => {
      if (!isDisconnectingRef.current) {
        connectInternalRef.current();
      }
    }, delay);
  }, [reconnectAttempts, config.maxReconnectAttempts, calculateReconnectDelay, onReconnect, onMaxRetriesReached, updateConnectionState]);

  handleReconnectRef.current = handleReconnect;

  // ============ SSE Parsing ============

  const parseSSE = useCallback((buffer: string): { parsed: StreamEventData[]; remaining: string } => {
    const parsed: StreamEventData[] = [];
    const lines = buffer.split('\n');
    let remaining = '';
    let currentEvent: Partial<StreamEventData> = {};

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      if (line === '') {
        if (currentEvent.type) {
          parsed.push(currentEvent as StreamEventData);
          currentEvent = {};
        }
        continue;
      }

      if (i === lines.length - 1 && !buffer.endsWith('\n')) {
        remaining = line;
        break;
      }

      if (line.startsWith('event:')) {
        currentEvent.type = line.slice(6).trim() as StreamEventData['type'];
      } else if (line.startsWith('id:')) {
        // FIX SSE Wave 1 §2.5: 解析 SSE 标准 id: 字段
        // 后端 SSE 流中 id: 字段的语义与 data 中的 sequence 等价，
        // 用于支持 Last-Event-ID header 的重连续传。
        const idStr = line.slice(3).trim();
        if (idStr.length > 0) {
          const idNum = Number(idStr);
          if (!Number.isNaN(idNum)) {
            currentEvent.sequence = idNum;
          }
        }
      } else if (line.startsWith('data:')) {
        try {
          const data = JSON.parse(line.slice(5).trim());
          currentEvent = { ...currentEvent, ...data };
        } catch {
          // Ignore parse errors
        }
      }
    }

    return { parsed, remaining };
  }, []);

  // ============ Event Handling ============

  const handleEvent = useCallback((event: StreamEventData) => {
    const opts = streamOptionsRef.current;

    if (typeof event.sequence === 'number' && event.sequence > latestSeenSequenceRef.current) {
      latestSeenSequenceRef.current = event.sequence;
    }

    const nextLongOperationState = getNextLongOperationState(event.type, inLongOperationRef.current);
    if (nextLongOperationState !== inLongOperationRef.current) {
      inLongOperationRef.current = nextLongOperationState;
    }

    // Extract agent_name from metadata
    if (event.metadata?.agent_name && !event.agent_name) {
      event.agent_name = event.metadata.agent_name as string;
    }

    // General callback
    opts.onEvent?.(event);
    // Reset heartbeat timer on ANY received event (not just heartbeat type)
    handleHeartbeat();


    switch (event.type) {
      case 'thinking_start':
        opts.onThinkingStart?.();
        break;

      case 'thinking_token': {
        const token = event.token || (event.metadata?.token as string);
        const accumulated = event.accumulated || (event.metadata?.accumulated as string) || '';
        if (token) {
          opts.onThinkingToken?.(token, accumulated);
        }
        break;
      }

      case 'thinking_end': {
        const fullResponse = event.accumulated || (event.metadata?.accumulated as string) || '';
        opts.onThinkingEnd?.(fullResponse);
        break;
      }

      case 'tool_call_start':
        if (event.tool) {
          opts.onToolStart?.(event.tool.name, event.tool.input || {});
        }
        break;

      case 'tool_call_end':
        if (event.tool) {
          opts.onToolEnd?.(event.tool.name, event.tool.output, event.tool.duration_ms || 0);
        }
        break;

      case 'tool_call':
        opts.onToolStart?.(event.tool_name || 'unknown', event.tool_input || {});
        break;

      case 'tool_result':
        opts.onToolEnd?.(event.tool_name || 'unknown', event.tool_output, event.tool_duration_ms || 0);
        break;

      case 'node_start':
        opts.onNodeStart?.(
          event.metadata?.node as string || 'unknown',
          event.phase || ''
        );
        break;

      case 'node_end':
        opts.onNodeEnd?.(
          event.metadata?.node as string || 'unknown',
          event.metadata?.summary as Record<string, unknown> || {}
        );
        break;

      case 'finding_new':
      case 'finding_verified':
        opts.onFinding?.(event.metadata || {}, event.type === 'finding_verified');
        break;

      case 'progress':
        opts.onProgress?.(
          event.metadata?.current as number || 0,
          event.metadata?.total as number || 100,
          event.message || ''
        );
        break;

      case 'task_complete':
      case 'task_end':
        if (event.status === 'paused') {
          // 暂停状态：刷新 task 状态使前端感知暂停，不调 onComplete，断开流
          opts.onTaskUpdate?.();
          disconnectInternal();
        } else if (event.status !== 'cancelled' && event.status !== 'failed') {
          opts.onComplete?.({
            findingsCount: event.findings_count || event.metadata?.findings_count as number || 0,
            securityScore: event.security_score || event.metadata?.security_score as number || 100,
          });
          disconnectInternal();
        } else {
          disconnectInternal();
        }
        break;

      case 'task_error':
        opts.onError?.(event.error || event.message || 'Unknown error');
        disconnectInternal();
        break;

      case 'error':
        opts.onError?.(event.error || event.message || 'Unknown error');
        break;

      case 'heartbeat':
        handleHeartbeat();
        opts.onHeartbeat?.();
        break;
    }
  }, [handleHeartbeat]);

  // ============ Connection ============

  const connectInternal = useCallback(async () => {
    if (!taskId || isDisconnectingRef.current) return;

    // Abort any existing connection before starting a new one,
    // so the old read() call is released and reconnection can proceed.
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    // Release reader on old connection so the finally block has nothing to clean.
    if (readerRef.current) {
      try { readerRef.current.cancel(); } catch { /* ignore */ }
      try { readerRef.current.releaseLock(); } catch { /* ignore */ }
      readerRef.current = null;
    }

    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    if (!token) {
      setError('Not authenticated');
      updateConnectionState('failed');
      return;
    }

    updateConnectionState('connecting');

    const effectiveAfterSequence = getEffectiveAfterSequence(
      streamOptionsRef.current.afterSequence,
      latestSeenSequenceRef.current,
    );

    const params = new URLSearchParams({
      include_thinking: String(streamOptionsRef.current.includeThinking ?? true),
      include_tool_calls: String(streamOptionsRef.current.includeToolCalls ?? true),
      after_sequence: String(effectiveAfterSequence),
    });

    const url = `/api/v1/agent-tasks/${taskId}/stream?${params}`;
    abortControllerRef.current = new AbortController();

    try {
      const response = await fetch(url, {
        headers: {
          'Authorization': `Bearer ${token}`,
          'Accept': 'text/event-stream',
          // FIX SSE Wave 1 §2.5: 重连时携带 Last-Event-ID header
          // SSE 标准：客户端通过 Last-Event-ID 告知服务端最后收到的事件 ID，
          // 服务端可从该位置之后继续推送，避免重复事件。值来自 latestSeenSequenceRef，
          // 在 disconnect 时不再清零，确保重连后从正确位置继续。
          ...(latestSeenSequenceRef.current > 0 && {
            'Last-Event-ID': String(latestSeenSequenceRef.current),
          }),
        },
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      updateConnectionState('connected');
      setReconnectAttempts(0);
      setError(null);
      lastActivityAtRef.current = Date.now();
      resetHeartbeatTimer();

      readerRef.current = response.body?.getReader() || null;
      if (!readerRef.current) {
        throw new Error('Unable to get response stream');
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        if (isDisconnectingRef.current) {
          console.log(`[ResilientStream] Stream loop exiting due to disconnect flag`);
          break;
        }

        const { done, value } = await readerRef.current.read();
        if (done) {
          console.log(`[ResilientStream] Server closed the stream normally for task ${taskId}`);
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const events = parseSSE(buffer);
        buffer = events.remaining;

        for (const event of events.parsed) {
          handleEvent(event);
        }
      }

      if (readerRef.current) {
        readerRef.current.releaseLock();
        readerRef.current = null;
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        console.log(`[ResilientStream] Stream aborted (expected during disconnect)`);
        return;
      }

      const errMsg = err instanceof Error ? err.message : String(err);
      console.error(`[ResilientStream] Connection error for task ${taskId}:`, errMsg);
      updateConnectionState('disconnected');

      if (!isDisconnectingRef.current) {
        console.log(`[ResilientStream] Triggering reconnect after connection error`);
        handleReconnectRef.current('connection error');
      }
    } finally {
      if (heartbeatTimeoutRef.current) {
        clearTimeout(heartbeatTimeoutRef.current);
      }
      if (readerRef.current) {
        try {
          readerRef.current.releaseLock();
        } catch {
          // Ignore
        }
        readerRef.current = null;
      }
    }
  }, [taskId, updateConnectionState, resetHeartbeatTimer, parseSSE, handleEvent]);

  connectInternalRef.current = connectInternal;

  const disconnectInternal = useCallback(() => {
    isDisconnectingRef.current = true;
    updateConnectionState('disconnected');

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    if (readerRef.current) {
      try {
        readerRef.current.cancel();
        readerRef.current.releaseLock();
      } catch {
        // Ignore
      }
      readerRef.current = null;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    if (heartbeatTimeoutRef.current) {
      clearTimeout(heartbeatTimeoutRef.current);
      heartbeatTimeoutRef.current = null;
    }

    setReconnectAttempts(0);
    // FIX SSE Wave 1 §2.5: 保留 latestSeenSequenceRef 高水位，不再清零
    // 之前 latestSeenSequenceRef.current = 0 导致重连时 getEffectiveAfterSequence
    // 取到较小值，回补大量已处理的老事件，造成日志重复和性能浪费。
    // 重连时应携带 Last-Event-ID header 从最新 sequence 继续。
    inLongOperationRef.current = false;
  }, [updateConnectionState]);

  // ============ Public API ============

  const connect = useCallback(() => {
    isDisconnectingRef.current = false;
    setError(null);
    connectInternal();
  }, [connectInternal]);

  const disconnect = useCallback(() => {
    disconnectInternal();
  }, [disconnectInternal]);

  const resetConnection = useCallback(() => {
    disconnectInternal();
    setReconnectAttempts(0);
    setError(null);
    isDisconnectingRef.current = false;
  }, [disconnectInternal]);

  // ============ Effects ============

  // Auto-connect
  useEffect(() => {
    if (autoConnect && taskId) {
      connect();
    }
    return () => {
      disconnectInternal();
    };
  }, [taskId, autoConnect]);

  // Cleanup
  useEffect(() => {
    return () => {
      isDisconnectingRef.current = true;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (heartbeatTimeoutRef.current) {
        clearTimeout(heartbeatTimeoutRef.current);
      }
    };
  }, []);

  return {
    // Connection control
    connect,
    disconnect,
    resetConnection,

    // State
    connectionState,
    isConnected,
    reconnectAttempts,
    maxReconnectAttempts: config.maxReconnectAttempts,
    lastHeartbeat,
    error,

    // Computed
    isReconnecting: connectionState === 'reconnecting',
    isFailed: connectionState === 'failed',
  };
}

export default useResilientStream;
