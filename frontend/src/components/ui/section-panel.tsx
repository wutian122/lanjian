import type { ReactNode } from "react";

import { cn } from "@/shared/utils/utils";

interface SectionPanelProps {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function SectionPanel({
  title,
  description,
  actions,
  children,
  className,
}: SectionPanelProps) {
  const hasHeader = Boolean(title || description || actions);

  return (
    <section
      className={cn(
        "rounded-xl border border-border bg-card text-card-foreground shadow-card",
        className
      )}
    >
      {hasHeader ? (
        <div className="flex flex-col gap-3 border-b border-border px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            {title ? <h2 className="text-lg font-semibold text-foreground">{title}</h2> : null}
            {description ? (
              <p className="text-sm text-muted-foreground">{description}</p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className="p-5">{children}</div>
    </section>
  );
}
