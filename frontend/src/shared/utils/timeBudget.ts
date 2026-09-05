/**
 * 任务时间预算展示逻辑（sandbox-verification-hard-gate Task 17）
 *
 * StatsPanel 时间格的纯计算：
 * - 运行态：剩余时间 = started_at + timeout_seconds - now（wall-clock 倒计时；
 *   后端 watchdog 与 orchestrator deadline 同源，timeout_seconds 为详情接口
 *   回传的有效预算——显式值 > llmConfig.agentTimeout > 全局 1800 回退链结果）
 * - 终态：已用时间 = completed_at - started_at
 * - pending/paused/数据缺失：不展示（null）。暂停态 wall-clock 剩余会把暂停
 *   时长算进消耗，语义误导，故 paused 不显示倒计时。
 */

export type TimeBudgetTone = "running" | "overdue" | "done";

export interface TimeBudgetView {
  /** 格子标签：运行态「剩余时间」/ 终态「已用时间」 */
  label: string;
  /** 格式化时长（mm:ss，跨小时 h:mm:ss） */
  value: string;
  tone: TimeBudgetTone;
}

export interface TimeBudgetTask {
  status?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  /** 有效预算秒数（后端 resolve_task_timeout_seconds 回退链结果）；null=未知 */
  timeout_seconds?: number | null;
}

const TERMINAL_STATUSES = new Set(["completed", "completed_with_gaps", "failed", "cancelled"]);
const RUNNING_STATUSES = new Set([
  "running",
  "initializing",
  "planning",
  "indexing",
  "analyzing",
  "verifying",
  "reporting",
]);

/** 秒数格式化为 mm:ss（≥1 小时 h:mm:ss）；负数 clamp 为 0。 */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

/** 任务是否处于运行态（倒计时格子需要 1s tick 刷新）。 */
export function isRunningStatus(status: string | null | undefined): boolean {
  return RUNNING_STATUSES.has(status ?? "");
}

/**
 * 计算时间预算格视图。now 可注入（测试/确定性渲染），默认当前时间。
 * 数据不足或状态不适用时返回 null（调用方不渲染格子）。
 */
export function describeTimeBudget(
  task: TimeBudgetTask,
  now: Date | number = Date.now(),
): TimeBudgetView | null {
  const nowMs = typeof now === "number" ? now : now.getTime();
  const status = task.status ?? "";
  const startedMs = parseTime(task.started_at);

  if (TERMINAL_STATUSES.has(status)) {
    const completedMs = parseTime(task.completed_at);
    if (startedMs === null || completedMs === null) return null;
    return {
      label: "已用时间",
      value: formatElapsed((completedMs - startedMs) / 1000),
      tone: "done",
    };
  }

  if (RUNNING_STATUSES.has(status)) {
    if (startedMs === null) return null;
    const budget = task.timeout_seconds;
    if (budget === null || budget === undefined || !(Number(budget) > 0)) return null;
    const deadlineMs = startedMs + Number(budget) * 1000;
    const remainingMs = deadlineMs - nowMs;
    return {
      label: "剩余时间",
      value: formatElapsed(remainingMs / 1000),
      tone: remainingMs < 0 ? "overdue" : "running",
    };
  }

  // pending / paused / 未知状态：不展示
  return null;
}
