export interface HeartbeatTimeoutOptions {
  defaultTimeoutMs: number;
  longOperationTimeoutMs: number;
  inLongOperation: boolean;
}

export type StreamLifecycleEventType =
  | "tool_call"
  | "tool_call_start"
  | "tool_result"
  | "tool_call_end"
  | "tool_call_error"
  | "task_complete"
  | "task_end"
  | "task_error"
  | "task_cancel"
  | string;

const LONG_OPERATION_START_EVENTS = new Set<StreamLifecycleEventType>([
  "tool_call",
  "tool_call_start",
]);

const LONG_OPERATION_END_EVENTS = new Set<StreamLifecycleEventType>([
  "tool_result",
  "tool_call_end",
  "tool_call_error",
  "task_complete",
  "task_end",
  "task_error",
  "task_cancel",
]);

export function getEffectiveAfterSequence(
  configuredAfterSequence: number | undefined,
  latestSeenSequence: number,
): number {
  return Math.max(configuredAfterSequence ?? 0, latestSeenSequence);
}

export function getHeartbeatTimeoutMs(options: HeartbeatTimeoutOptions): number {
  return options.inLongOperation
    ? options.longOperationTimeoutMs
    : options.defaultTimeoutMs;
}

export function getNextLongOperationState(
  eventType: StreamLifecycleEventType,
  currentState: boolean,
): boolean {
  if (LONG_OPERATION_START_EVENTS.has(eventType)) {
    return true;
  }
  if (LONG_OPERATION_END_EVENTS.has(eventType)) {
    return false;
  }
  return currentState;
}
