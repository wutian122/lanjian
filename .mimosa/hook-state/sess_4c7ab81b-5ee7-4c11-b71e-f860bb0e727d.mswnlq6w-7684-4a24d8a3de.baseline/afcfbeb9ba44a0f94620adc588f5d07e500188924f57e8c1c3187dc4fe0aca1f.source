import { Badge } from "@/components/ui/badge";
import { cn } from "@/shared/utils/utils";

type StatusBadgeType = "status" | "severity";

interface StatusBadgeProps {
  value: string;
  label: string;
  type?: StatusBadgeType;
  className?: string;
}

const statusClasses: Record<string, string> = {
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  running: "border-blue-200 bg-blue-50 text-blue-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  pending: "border-slate-200 bg-slate-50 text-slate-600",
  queued: "border-violet-200 bg-violet-50 text-violet-700",
  paused: "border-amber-200 bg-amber-50 text-amber-700",
  cancelled: "border-zinc-200 bg-zinc-50 text-zinc-600",
};

const severityClasses: Record<string, string> = {
  critical: "border-red-300 bg-red-50 text-red-800",
  high: "border-orange-300 bg-orange-50 text-orange-700",
  medium: "border-amber-300 bg-amber-50 text-amber-700",
  low: "border-blue-200 bg-blue-50 text-blue-700",
  info: "border-slate-200 bg-slate-50 text-slate-600",
};

export function StatusBadge({
  value,
  label,
  type = "status",
  className,
}: StatusBadgeProps) {
  const classes = type === "severity" ? severityClasses : statusClasses;

  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-full px-2.5 py-1 text-xs font-medium normal-case tracking-normal",
        classes[value] ?? "border-border bg-muted text-muted-foreground",
        className
      )}
    >
      {label}
    </Badge>
  );
}
