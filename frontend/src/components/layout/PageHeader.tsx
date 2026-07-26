import type { ReactNode } from "react";

import { cn } from "@/shared/utils/utils";

interface PageHeaderProps {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn(
        "flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between",
        className
      )}
    >
      <div className="space-y-2">
        {eyebrow ? (
          <p className="text-sm font-medium uppercase tracking-[0.16em] text-primary">
            {eyebrow}
          </p>
        ) : null}
        <div className="space-y-3">
          <h1 className="text-3xl font-semibold tracking-[-0.03em] text-foreground sm:text-4xl">
            {title}
          </h1>
          {description ? (
            <p className="max-w-3xl text-base text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}
