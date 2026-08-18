/**
 * Log Entry Component
 * Enterprise clean log entry with structured layout
 * Raw log messages are rendered directly, labels are translated to Chinese
 */

import { memo } from "react";
import {
  ChevronDown, ChevronUp, Loader2,
  CheckCircle2, Wifi, XOctagon, AlertTriangle,
  Play, ArrowRight
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/shared/utils/utils";
import { LOG_TYPE_CONFIG, SEVERITY_COLORS } from "../constants";
import type { LogEntryProps } from "../types";

// Chinese log type labels
const LOG_TYPE_LABELS: Record<string, string> = {
  thinking: '思考',
  tool: '工具',
  phase: '阶段',
  finding: '漏洞',
  dispatch: '调度',
  info: '信息',
  error: '错误',
  user: '用户',
  progress: '进度',
};

// Helper to format title (remove emojis and clean up)
function formatTitle(title: string, _type: string): string {
  let cleaned = title
    .replace(/[\u{1F300}-\u{1F9FF}]/gu, '')
    .replace(/[\u{2600}-\u{26FF}]/gu, '')
    .replace(/[\u{2700}-\u{27BF}]/gu, '')
    .replace(/[\u{FE00}-\u{FE0F}]/gu, '')
    .replace(/[\u{1F000}-\u{1F02F}]/gu, '')
    .replace(/[✅🔗🛑✕⚠️❌⚡🔄🔍💡📁📄🐛🛡️]/g, '')
    .trim();
  cleaned = cleaned.replace(/^[:\-–—•·]\s*/, '');
  return cleaned || title;
}

// Get status icon for info/system messages
function getStatusIcon(title: string) {
  const lowerTitle = title.toLowerCase();
  if (lowerTitle.includes('connect') || lowerTitle.includes('stream')) {
    return <Wifi className="w-3 h-3 text-green-500" />;
  }
  if (lowerTitle.includes('complete') || lowerTitle.includes('success') || lowerTitle.includes('done')) {
    return <CheckCircle2 className="w-3 h-3 text-green-500" />;
  }
  if (lowerTitle.includes('cancel') || lowerTitle.includes('stop') || lowerTitle.includes('abort')) {
    return <XOctagon className="w-3 h-3 text-yellow-500" />;
  }
  if (lowerTitle.includes('error') || lowerTitle.includes('fail')) {
    return <AlertTriangle className="w-3 h-3 text-red-500" />;
  }
  if (lowerTitle.includes('start') || lowerTitle.includes('begin') || lowerTitle.includes('init')) {
    return <Play className="w-3 h-3 text-blue-500" />;
  }
  return null;
}

const typeLabelColors: Record<string, string> = {
  thinking: 'bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-950/20 dark:text-violet-300 dark:border-violet-800',
  tool: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/20 dark:text-amber-300 dark:border-amber-800',
  finding: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/20 dark:text-red-300 dark:border-red-800',
  error: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/20 dark:text-red-300 dark:border-red-800',
  info: 'bg-muted text-muted-foreground border-border',
  progress: 'bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-950/20 dark:text-cyan-300 dark:border-cyan-800',
  dispatch: 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/20 dark:text-sky-300 dark:border-sky-800',
  phase: 'bg-teal-50 text-teal-700 border-teal-200 dark:bg-teal-950/20 dark:text-teal-300 dark:border-teal-800',
  user: 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-950/20 dark:text-indigo-300 dark:border-indigo-800',
};

export const LogEntry = memo(function LogEntry({ item, isExpanded, onToggle }: LogEntryProps) {
  const config = LOG_TYPE_CONFIG[item.type] || LOG_TYPE_CONFIG.info;
  const isThinking = item.type === 'thinking';
  const isFinding = item.type === 'finding';
  const isError = item.type === 'error';
  const isInfo = item.type === 'info';
  const showContent = isThinking || isExpanded;
  const isCollapsible = !isThinking && item.content;

  const formattedTitle = formatTitle(item.title, item.type);
  const statusIcon = isInfo ? getStatusIcon(formattedTitle) : null;
  const labelColor = typeLabelColors[item.type] || typeLabelColors.info;

  return (
    <div
      className={cn(
        "group relative transition-all duration-200",
        isCollapsible && "cursor-pointer"
      )}
      onClick={isCollapsible ? onToggle : undefined}
    >
      <div className={cn(
        "relative rounded-lg border overflow-hidden",
        isExpanded ? 'bg-card shadow-sm' : 'bg-card/60',
        isCollapsible && 'hover:bg-card hover:shadow-sm',
        isFinding && 'border-red-200 dark:border-red-800 bg-red-50/50 dark:bg-red-950/10',
        isError && 'border-red-200 dark:border-red-800 bg-red-50/50 dark:bg-red-950/10',
        !isFinding && !isError && 'border-border'
      )}>
        <div className="px-4 py-2.5">
          {/* Header row */}
          <div className="flex items-center gap-2.5">
            {/* Type icon */}
            <div className="flex-shrink-0">
              {config.icon}
            </div>

            {/* Type label */}
            <span className={cn(
              "text-xs font-medium px-2 py-0.5 rounded border flex-shrink-0",
              labelColor
            )}>
              {LOG_TYPE_LABELS[item.type] || item.type.toUpperCase()}
            </span>

            {/* Timestamp */}
            <span className="text-xs text-muted-foreground flex-shrink-0 tabular-nums">
              {item.time}
            </span>

            {/* Separator */}
            <ArrowRight className="w-3 h-3 text-muted-foreground/40 flex-shrink-0" />

            {/* Status icon for info messages */}
            {statusIcon && <span className="flex-shrink-0">{statusIcon}</span>}

            {/* Title - for non-thinking types */}
            {!isThinking && (
              <span className="text-sm text-foreground truncate flex-1">
                {formattedTitle}
              </span>
            )}

            {/* Streaming cursor */}
            {item.isStreaming && (
              <span className="w-2 h-4 bg-violet-500 rounded-sm flex-shrink-0 animate-pulse" />
            )}

            {/* Tool status */}
            {item.tool?.status === 'running' && (
              <div className="flex items-center gap-1.5 flex-shrink-0 bg-amber-50 dark:bg-amber-950/20 px-2 py-0.5 rounded border border-amber-200 dark:border-amber-800">
                <Loader2 className="w-3 h-3 animate-spin text-amber-600" />
                <span className="text-xs text-amber-600 dark:text-amber-400 font-medium">运行中</span>
              </div>
            )}

            {item.tool?.status === 'completed' && (
              <div className="flex items-center gap-1 flex-shrink-0 px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800">
                <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">完成</span>
              </div>
            )}

            {/* Agent badge */}
            {item.agentName && (
              <Badge variant="outline" className="h-5 px-2 text-xs font-medium flex-shrink-0">
                {item.agentName}
              </Badge>
            )}

            {/* Right side info */}
            <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
              {/* Duration badge */}
              {item.tool?.duration !== undefined && (
                <span className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded tabular-nums">
                  {item.tool.duration}ms
                </span>
              )}

              {/* Severity badge */}
              {item.severity && (
                <Badge
                  className={cn(
                    "text-xs font-semibold px-2 py-0.5",
                    SEVERITY_COLORS[item.severity] || SEVERITY_COLORS.info
                  )}
                >
                  {item.severity}
                </Badge>
              )}

              {/* Expand indicator */}
              {isCollapsible && (
                <div className={cn(
                  "w-5 h-5 flex items-center justify-center rounded",
                  isExpanded ? 'bg-primary/10 border border-primary/20' : 'bg-muted border border-border'
                )}>
                  {isExpanded ? (
                    <ChevronUp className="w-3.5 h-3.5 text-primary" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Thinking content - always visible */}
          {isThinking && item.content && (
            <div className="mt-2.5 relative">
              <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-violet-300 dark:bg-violet-700 rounded-full" />
              <div className="pl-4 text-sm text-foreground/85 whitespace-pre-wrap break-words">
                {item.content}
              </div>
            </div>
          )}

          {/* Collapsible content */}
          {!isThinking && showContent && item.content && (
            <div className="mt-2.5 overflow-hidden">
              <div className="bg-muted/30 rounded-md border border-border overflow-hidden">
                <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-muted/50">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground font-medium">
                      {item.type === 'tool' ? '输出' : '详情'}
                    </span>
                  </div>
                </div>
                <pre className="p-3 text-sm font-mono text-foreground/80 max-h-64 overflow-y-auto custom-scrollbar whitespace-pre-wrap break-words">
                  {item.content}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

export default LogEntry;
