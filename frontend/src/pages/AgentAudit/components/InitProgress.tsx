import { Loader2, CheckCircle2, Circle } from "lucide-react";
import type { InitStep } from "../types";

interface InitProgressProps {
  steps: InitStep[];
}

export function InitProgress({ steps }: InitProgressProps) {
  const completedCount = steps.filter((s) => s.status === "done").length;
  const totalCount = steps.length;
  const progressPercent = totalCount > 0 ? (completedCount / totalCount) * 100 : 0;

  return (
    <div className="flex flex-col items-center gap-8 relative z-10">
      {/* Title */}
      <div className="flex items-center gap-3">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
        <span className="text-xl font-mono tracking-wide text-foreground">
          {"\u521d\u59cb\u5316\u5ba1\u8ba1\u73af\u5883"}
        </span>
      </div>

      {/* Progress bar */}
      <div className="w-80 max-w-[80vw]">
        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
          <div
            className="h-full bg-primary transition-all duration-500 ease-out"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
        <div className="flex justify-between mt-2 text-xs text-muted-foreground font-mono">
          <span>{completedCount} / {Math.max(totalCount, 4)}</span>
          <span>{Math.round(progressPercent)}%</span>
        </div>
      </div>

      {/* Steps list */}
      <div className="w-96 max-w-[90vw] space-y-3">
        {steps.length === 0 ? (
          <div className="flex items-center gap-3 text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin text-primary" />
            <span className="text-sm font-mono">
              {"\u542f\u52a8\u4e2d..."}
            </span>
          </div>
        ) : (
          steps.map((step, i) => (
            <div
              key={i}
              className="flex items-center gap-3 transition-all duration-300"
            >
              {step.status === "done" ? (
                <CheckCircle2 className="w-5 h-5 text-emerald-500 flex-shrink-0" />
              ) : (
                <Loader2 className="w-5 h-5 animate-spin text-primary flex-shrink-0" />
              )}
              <span
                className={
                  step.status === "done"
                    ? "text-sm text-muted-foreground line-through decoration-muted-foreground/40"
                    : "text-sm text-foreground font-medium"
                }
              >
                {step.name}
              </span>
              {step.status === "done" && (
                <span className="text-xs text-emerald-500 font-mono ml-auto">
                  {"\u2713"}
                </span>
              )}
            </div>
          ))
        )}

        {/* Placeholder for upcoming steps */}
        {totalCount < 4 && totalCount > 0 && (
          Array.from({ length: Math.min(4 - totalCount, 2) }).map((_, i) => (
            <div key={`placeholder-${i}`} className="flex items-center gap-3 opacity-30">
              <Circle className="w-5 h-5 text-muted-foreground flex-shrink-0" />
              <span className="text-sm text-muted-foreground">
                {"\u7b49\u5f85\u4e2d..."}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
