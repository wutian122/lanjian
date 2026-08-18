import {
  getEffectiveAfterSequence,
  getHeartbeatTimeoutMs,
  getNextLongOperationState,
} from "./resilientStreamPolicy.js";

function assertEqual<T>(actual: T, expected: T, message: string) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

assertEqual(
  getEffectiveAfterSequence(8, 1455),
  1455,
  "reconnect should resume after the latest SSE event sequence",
);

assertEqual(
  getEffectiveAfterSequence(1455, 8),
  1455,
  "historical afterSequence should still win when it is newer",
);

assertEqual(
  getHeartbeatTimeoutMs({
    defaultTimeoutMs: 45_000,
    longOperationTimeoutMs: 180_000,
    inLongOperation: true,
  }),
  180_000,
  "long-running tool calls should not trip the short heartbeat watchdog",
);

assertEqual(
  getNextLongOperationState("tool_call", false),
  true,
  "tool_call should enter long-operation heartbeat mode",
);

assertEqual(
  getNextLongOperationState("tool_result", true),
  false,
  "tool_result should leave long-operation heartbeat mode",
);

console.log("resilientStreamPolicy tests passed");
