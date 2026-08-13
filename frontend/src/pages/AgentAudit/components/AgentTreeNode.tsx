/**
 * Agent Tree Node Component
 * Clean tree visualization with enterprise styling
 */

import { useState, memo } from "react";
import { ChevronDown, ChevronRight, Bot, Cpu, Scan, FileSearch, ShieldCheck, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/shared/utils/utils";
import type { AgentTreeNodeItemProps } from "../types";

// Agent type icons
const AGENT_TYPE_ICONS: Record<string, React.ReactNode> = {
  orchestrator: <Cpu className="w-3.5 h-3.5 text-violet-600" />,
  recon: <Scan className="w-3.5 h-3.5 text-teal-600" />,
  analysis: <FileSearch className="w-3.5 h-3.5 text-amber-600" />,
  verification: <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />,
};

const AGENT_TYPE_BG: Record<string, string> = {
  orchestrator: 'bg-violet-50 border-violet-200 dark:bg-violet-950/20 dark:border-violet-800',
  recon: 'bg-teal-50 border-teal-200 dark:bg-teal-950/20 dark:border-teal-800',
  analysis: 'bg-amber-50 border-amber-200 dark:bg-amber-950/20 dark:border-amber-800',
  verification: 'bg-emerald-50 border-emerald-200 dark:bg-emerald-950/20 dark:border-emerald-800',
};

export const AgentTreeNodeItem = memo(function AgentTreeNodeItem({
  node,
  depth = 0,
  selectedId,
  onSelect,
  isLast = false
}: AgentTreeNodeItemProps & { isLast?: boolean }) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = node.children && node.children.length > 0;
  const isSelected = selectedId === node.agent_id;
  const isRunning = node.status === 'running';
  const isCompleted = node.status === 'completed';
  const isFailed = node.status === 'failed';

  const typeIcon = AGENT_TYPE_ICONS[node.agent_type] || <Bot className="w-3 h-3 text-muted-foreground" />;
  const typeBg = AGENT_TYPE_BG[node.agent_type] || 'bg-muted border-border';

  const indent = depth * 20;

  return (
    <div className="relative">
      {depth > 0 && (
        <>
          <div
            className="absolute border-l-2 border-slate-200 dark:border-slate-700"
            style={{
              left: `${indent - 10}px`,
              top: 0,
              height: isLast ? '18px' : '100%',
            }}
          />
          <div
            className="absolute border-t-2 border-slate-200 dark:border-slate-700"
            style={{
              left: `${indent - 10}px`,
              top: '18px',
              width: '10px',
            }}
          />
        </>
      )}

      <div
        className={cn(
          "relative flex items-center gap-2 py-1.5 px-2 cursor-pointer rounded-md transition-colors",
          isSelected
            ? 'bg-primary/10 border border-primary/30'
            : isRunning
              ? 'bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800'
              : isCompleted
                ? 'bg-card border border-emerald-200 dark:border-emerald-800'
                : isFailed
                  ? 'bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800'
                  : node.status === 'waiting'
                    ? 'bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800'
                    : 'bg-card border border-border hover:border-muted-foreground/30'
        )}
        style={{ marginLeft: `${indent}px` }}
        onClick={() => onSelect(node.agent_id)}
      >
        {hasChildren ? (
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
            className="flex-shrink-0 w-4 h-4 flex items-center justify-center rounded hover:bg-muted"
          >
            {expanded ? (
              <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
            )}
          </button>
        ) : (
          <span className="w-4" />
        )}

        {/* Status indicator */}
        <div className="relative flex-shrink-0">
          <div className={cn(
            "w-2 h-2 rounded-full",
            isRunning && 'bg-emerald-500',
            isCompleted && 'bg-emerald-500',
            isFailed && 'bg-red-500',
            node.status === 'waiting' && 'bg-amber-500',
            node.status === 'created' && 'bg-slate-400'
          )} />
          {isRunning && (
            <div className="absolute inset-0 w-2 h-2 rounded-full bg-emerald-500 animate-ping opacity-40" />
          )}
        </div>

        {/* Agent type icon */}
        <div className={cn("flex-shrink-0 p-1 rounded border", typeBg)}>
          {typeIcon}
        </div>

        {/* Agent name */}
        <span className={cn(
          "text-sm truncate flex-1",
          isSelected ? 'text-foreground font-semibold' : 'text-foreground'
        )}>
          {node.agent_name}
        </span>

        {/* Metrics */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {(node.iterations ?? 0) > 0 && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
              <Zap className="w-3 h-3" />
              <span>{node.iterations}</span>
            </div>
          )}

          {!node.parent_agent_id && node.findings_count > 0 && (
            <Badge className="h-5 px-1.5 text-xs bg-red-50 text-red-600 border-red-200 dark:bg-red-950/20 dark:text-red-300 dark:border-red-800 font-semibold">
              {node.findings_count}
            </Badge>
          )}
        </div>
      </div>

      {expanded && hasChildren && (
        <div className="relative">
          {node.children.map((child, index) => (
            <AgentTreeNodeItem
              key={child.agent_id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              isLast={index === node.children.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  );
});

export default AgentTreeNodeItem;
