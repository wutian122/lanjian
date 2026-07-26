export const taskStatusText = {
  completed: "已完成",
  completed_with_gaps: "已完成(覆盖率不足)",
  running: "运行中",
  failed: "失败",
  pending: "待处理",
  queued: "排队中",
  paused: "已暂停",
  cancelled: "已取消",
} as const;

export const severityText = {
  critical: "严重",
  high: "高危",
  medium: "中危",
  low: "低危",
  info: "信息",
} as const;

export function getTaskStatusText(status: string) {
  return taskStatusText[status as keyof typeof taskStatusText] ?? status;
}

export function getSeverityText(severity: string) {
  return severityText[severity as keyof typeof severityText] ?? severity;
}
