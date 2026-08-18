import type { ReactNode } from "react";

import { cn } from "@/shared/utils/utils";

type MetricCardTone = "default" | "blue" | "green" | "amber" | "red";

interface MetricCardProps {
  label: string;
  value: ReactNode;
  description?: string;
  icon?: ReactNode;
  tone?: MetricCardTone;
  className?: string;
}

const toneClasses: Record<MetricCardTone, string> = {
  default: "bg-muted text-muted-foreground",
  blue: "bg-blue-50 text-blue-700",
  green: "bg-emerald-50 text-emerald-700",
  amber: "bg-amber-50 text-amber-700",
  red: "bg-red-50 text-red-700",
};

export function MetricCard({
  label,
  value,
  description,
  icon,
  tone = "default",
  className,
}: MetricCardProps) {
  return (
    <section
      className={cn(
        "rounded-xl border border-border bg-card p-5 text-card-foreground shadow-card",
        className
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <div className="text-3xl font-semibold tracking-[-0.03em] text-foreground">
            {value}
          </div>
        </div>
        {icon ? (
          <div className={cn("rounded-xl p-3", toneClasses[tone])}>{icon}</div>
        ) : null}
      </div>
      {description ? (
        <p className="mt-4 text-sm text-muted-foreground">{description}</p>
      ) : null}
    </section>
  );
}
