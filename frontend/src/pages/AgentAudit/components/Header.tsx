/**
 * Header Component
 * Enterprise clean header with status badge
 */

import { useState } from "react";
import { Pause, Play, Download, Loader2, Cpu, Sparkles, Bot, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { StatusBadge } from "./StatusBadge";
import type { HeaderProps } from "../types";

// 与 AuditTasks.tsx ACTIVE_AGENT_TASK_STATUSES 语义保持一致：
// 只有真正处于活跃执行阶段的任务才禁用删除按钮，paused 可以删除。
const ACTIVE_AGENT_TASK_STATUSES = new Set([
  "pending",
  "initializing",
  "running",
  "planning",
  "indexing",
  "analyzing",
  "verifying",
  "reporting",
]);

export function Header({
  task,
  isRunning,
  isPaused,
  isPausing,
  isResuming,
  isDeleting,
  onPause,
  onResume,
  onExport,
  onNewAudit,
  onDelete,
  onOpenAiPanel,
}: HeaderProps) {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  const deleteDisabled =
    !onDelete ||
    !task ||
    !!isDeleting ||
    ACTIVE_AGENT_TASK_STATUSES.has(task.status);

  const handleConfirmDelete = () => {
    setShowDeleteDialog(false);
    onDelete?.();
  };

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

        {onDelete && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowDeleteDialog(true)}
            disabled={deleteDisabled}
            className="h-8 px-3 text-xs text-red-600 border-red-200 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950/30"
          >
            {isDeleting ? (
              <>
                <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                删除中
              </>
            ) : (
              <>
                <Trash2 className="w-3.5 h-3.5 mr-1.5" />
                删除
              </>
            )}
          </Button>
        )}

        <Button size="sm" onClick={onNewAudit} className="h-8 px-3 text-xs">
          <Sparkles className="w-3.5 h-3.5 mr-1.5" />
          新建审计
        </Button>
      </div>

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除任务</AlertDialogTitle>
            <AlertDialogDescription>
              将永久删除该 Agent 审计任务及其关联的事件、发现、检查点等数据，此操作不可撤销。是否继续？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-red-600 hover:bg-red-700 focus:ring-red-500"
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </header>
  );
}

export default Header;
