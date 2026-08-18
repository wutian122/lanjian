/**
 * Status Badge Component - Re-export shared StatusBadge
 * Converts status string to shared StatusBadge format
 */

import { StatusBadge as SharedStatusBadge } from "@/components/ui/status-badge";

interface StatusBadgeProps {
  status: string;
  size?: "sm" | "default";
}

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  queued: "排队中",
  error: "错误",
};

export function StatusBadge({ status, size: _size }: StatusBadgeProps) {
  const label = STATUS_LABELS[status] || _getFallbackLabel(status);

  return (
    <SharedStatusBadge
      value={status}
      label={label}
      type="status"
    />
  );
}

function _getFallbackLabel(status: string): string {
  const fallbackLabels: Record<string, string> = {
    error: "错误",
    warning: "警告",
    success: "成功",
  };
  return fallbackLabels[status] || status.toUpperCase();
}

export default StatusBadge;
