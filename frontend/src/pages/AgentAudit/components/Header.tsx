/**
 * Header Component
 * Enterprise clean header with status badge
 */

import { Pause, Play, Download, Loader2, Cpu, Sparkles, Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "./StatusBadge";
import type { HeaderProps } from "../types";

export function Header({
  task,
  isRunning,
  isPaused,
  isPausing,
  isResuming,
  onPause,
  onResume,
  onExport,
  onNewAudit,
  onOpenAiPanel,
}: HeaderProps) {
  return (
    <header className="flex-shrink-0 h-14 border-b border-border flex items-center justify-between px-6 bg-card">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2.5 pr-4 border-r border-border">
          <div className="p-1.5 rounded-md bg-primary/10 border border-primary/20">
            <Cpu className="w-4 h-4 text-primary" />
          </div>
          <div className="flex flex-col">
            <span className="font-semibold text-foreground text-sm leading-tight">
              蓝鉴<span className="text-primary font-bold">·lanjian</span>
            </span>
            <span className="text-[10px] text-muted-foreground">安全审计平台</span>
          </div>
        </div>

        {task && (
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground font-medium">任务</span>
            <span className="text-sm text-foreground font-medium truncate max-w-[200px]">
              {task.name || task.id.slice(0, 8)}
            </span>
            <StatusBadge status={task.status} />
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        {isRunning && (
          <Button
            variant="outline"
            size="sm"
            onClick={onPause}
            disabled={isPausing}
            className="h-8 px-3 text-xs text-amber-700 border-amber-200 hover:bg-amber-50 dark:border-amber-800 dark:hover:bg-amber-950/30"
          >
            {isPausing ? (
              <>
                <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                暂停中
              </>
            ) : (
              <>
                <Pause className="w-3 h-3 mr-1.5" />
                暂停
              </>
            )}
          </Button>
        )}

        {isPaused && (
          <Button
            size="sm"
            onClick={onResume}
            disabled={isResuming}
            className="h-8 px-3 text-xs"
          >
            {isResuming ? (
              <>
                <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                继续中
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 mr-1.5" />
                继续
              </>
            )}
          </Button>
        )}

        <Button variant="outline" size="sm" onClick={onExport} disabled={!task} className="h-8 px-3 text-xs">
          <Download className="w-3.5 h-3.5 mr-1.5" />
          导出
        </Button>

        {onOpenAiPanel && (
          <Button
            variant="outline"
            size="sm"
            onClick={onOpenAiPanel}
            className="h-8 px-3 text-xs"
          >
            <Bot className="w-3.5 h-3.5 mr-1.5" />
            AI 协同
          </Button>
        )}

        <Button size="sm" onClick={onNewAudit} className="h-8 px-3 text-xs">
          <Sparkles className="w-3.5 h-3.5 mr-1.5" />
          新建审计
        </Button>
      </div>
    </header>
  );
}

export default Header;
