/**
 * Stats Panel Component
 * Enterprise dashboard-style statistics
 */

import { memo } from "react";
import { Activity, FileCode, Repeat, Zap, Bug, Shield, AlertTriangle, TrendingUp, Database, Layers } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/shared/utils/utils";
import { VerificationStatusBreakdown } from "@/shared/components/VerificationStatusBreakdown";
import type { StatsPanelProps } from "../types";

function MetricItem({ icon, label, value, suffix = "", compact = false }: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  suffix?: string;
  compact?: boolean;
}) {
  return (
    <div className={cn(
      "flex items-center gap-2 rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors",
      compact ? "p-2" : "p-3"
    )}>
      <div className={cn("rounded-md bg-muted", compact ? "p-1.5" : "p-2")}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className={cn("text-muted-foreground font-medium", compact ? "text-[10px]" : "text-xs")}>{label}</div>
        <div className={cn("font-semibold text-foreground", compact ? "text-sm" : "text-base")}>
          {value}<span className={cn("text-muted-foreground ml-0.5", compact ? "text-xs" : "text-sm")}>{suffix}</span>
        </div>
      </div>
    </div>
  );
}

export const StatsPanel = memo(function StatsPanel({ task, findings, compact = false }: StatsPanelProps) {
  if (!task) return null;

  const totalFindings = task.findings_count || 0;
  const progressPercent = task.progress_percentage || 0;

  return (
    <div className={cn(compact ? "space-y-2" : "space-y-3")}>
      {/* Progress Section */}
      <div className={cn("rounded-lg border border-border bg-card", compact ? "p-2.5" : "p-4")}>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-md bg-primary/10 border border-primary/20">
              <Activity className="w-4 h-4 text-primary" />
            </div>
            <span className="text-sm text-foreground font-semibold">审计进度</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-lg text-primary font-semibold">{progressPercent.toFixed(0)}</span>
            <span className="text-sm text-muted-foreground">%</span>
          </div>
        </div>

        <div className="relative h-2.5 bg-muted rounded-full overflow-hidden">
          <div
            className="absolute inset-y-0 left-0 bg-primary rounded-full transition-all duration-700"
            style={{ width: `${progressPercent}%` }}
          />
        </div>

        <div className={cn("flex items-center justify-between mt-3", compact ? "text-xs" : "text-sm")}>
          <div className="flex items-center gap-2 text-muted-foreground">
            <FileCode className={cn(compact ? "w-3.5 h-3.5" : "w-4 h-4")} />
            <span className="font-medium">已扫描文件</span>
          </div>
          <span className="text-foreground font-semibold">
            {task.analyzed_files}<span className="text-muted-foreground font-normal"> / {task.total_files}</span>
          </span>
        </div>

        {task.files_with_findings > 0 && (
          <div className={cn("flex items-center justify-between mt-2", compact ? "text-xs" : "text-sm")}>
            <div className="flex items-center gap-2 text-muted-foreground">
              <AlertTriangle className={cn("text-red-500", compact ? "w-3.5 h-3.5" : "w-4 h-4")} />
              <span className="font-medium">存在漏洞的文件</span>
            </div>
            <span className="text-red-500 font-semibold">{task.files_with_findings}</span>
          </div>
        )}

        {/* Q1: 验证状态分布 */}
        {task.verification_status_breakdown && task.findings_count > 0 && (
          <VerificationStatusBreakdown
            breakdown={task.verification_status_breakdown}
            className="mt-2"
            variant={compact ? "compact" : "full"}
          />
        )}
      </div>

      {/* Metrics Grid */}
      <div className={cn("grid grid-cols-2", compact ? "gap-1.5" : "gap-2")}>
        <MetricItem
          icon={<Repeat className="w-4 h-4 text-teal-600" />}
          label="迭代次数"
          value={task.total_iterations || 0}
          compact={compact}
        />
        <MetricItem
          icon={<Zap className="w-4 h-4 text-amber-600" />}
          label="工具调用"
          value={task.tool_calls_count || 0}
          compact={compact}
        />
        <MetricItem
          icon={<TrendingUp className="w-4 h-4 text-violet-600" />}
          label="Token用量"
          value={((task.tokens_used || 0) / 1000).toFixed(1)}
          suffix="k"
          compact={compact}
        />
        <MetricItem
          icon={<Bug className="w-4 h-4 text-red-600" />}
          label="漏洞总数"
          value={totalFindings}
          compact={compact}
        />
        {(task.indexed_files ?? 0) > 0 && (
          <MetricItem
            icon={<Database className="w-4 h-4 text-cyan-600" />}
            label="已索引文件"
            value={task.indexed_files}
            compact={compact}
          />
        )}
        {(task.total_chunks ?? 0) > 0 && (
          <MetricItem
            icon={<Layers className="w-4 h-4 text-indigo-600" />}
            label="代码块数"
            value={task.total_chunks}
            compact={compact}
          />
        )}
        {task.verification_coverage != null && (
          <MetricItem
            icon={<Shield className="w-4 h-4 text-green-600" />}
            label="验证覆盖率"
            value={(task.verification_coverage * 100).toFixed(0)}
            suffix="%"
            compact={compact}
          />
        )}
      </div>

      {/* Findings breakdown */}
      {totalFindings > 0 && (
        <div className={cn("rounded-lg border border-red-200 dark:border-red-800 bg-card", compact ? "p-2.5" : "p-4")}>
          <div className={cn("flex items-center gap-2.5", compact ? "mb-2" : "mb-3")}>
            <div className={cn("rounded-md bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800", compact ? "p-1" : "p-1.5")}>
              <AlertTriangle className={cn("text-red-500", compact ? "w-3.5 h-3.5" : "w-4 h-4")} />
            </div>
            <span className={cn("text-foreground font-semibold", compact ? "text-xs" : "text-sm")}>严重程度分布</span>
          </div>

          <div className={cn("flex flex-wrap", compact ? "gap-1" : "gap-1.5")}>
            {(task.critical_count || 0) > 0 && (
              <Badge className="bg-red-50 text-red-700 border-red-200 dark:bg-red-950/20 dark:text-red-300 dark:border-red-800 text-xs font-semibold">
                严重: {task.critical_count}
              </Badge>
            )}
            {(task.high_count || 0) > 0 && (
              <Badge className="bg-orange-50 text-orange-700 border-orange-200 dark:bg-orange-950/20 dark:text-orange-300 dark:border-orange-800 text-xs font-semibold">
                高危: {task.high_count}
              </Badge>
            )}
            {(task.medium_count || 0) > 0 && (
              <Badge className="bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/20 dark:text-amber-300 dark:border-amber-800 text-xs font-semibold">
                中危: {task.medium_count}
              </Badge>
            )}
            {(task.low_count || 0) > 0 && (
              <Badge className="bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/20 dark:text-blue-300 dark:border-blue-800 text-xs font-semibold">
                低危: {task.low_count}
              </Badge>
            )}
          </div>
        </div>
      )}

      {/* Security Score */}
      {task.security_score !== null && task.security_score !== undefined && (
        <div className={cn("rounded-lg border border-border bg-card", compact ? "p-2.5" : "p-4")}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className={cn(
                "rounded-md border",
                compact ? "p-1" : "p-1.5",
                task.security_score >= 80 ? 'bg-emerald-50 border-emerald-200 dark:bg-emerald-950/20 dark:border-emerald-800' :
                task.security_score >= 60 ? 'bg-amber-50 border-amber-200 dark:bg-amber-950/20 dark:border-amber-800' :
                'bg-red-50 border-red-200 dark:bg-red-950/20 dark:border-red-800'
              )}>
                <Shield className={cn(
                  compact ? "w-3.5 h-3.5" : "w-4 h-4",
                  task.security_score >= 80 ? 'text-emerald-600' :
                  task.security_score >= 60 ? 'text-amber-600' :
                  'text-red-600'
                )} />
              </div>
              <div>
                <span className={cn("text-foreground font-semibold block", compact ? "text-xs" : "text-sm")}>安全评分</span>
                <span className={cn("text-muted-foreground", compact ? "text-[10px]" : "text-xs")}>
                  {task.security_score >= 80 ? '优秀' :
                   task.security_score >= 60 ? '良好' :
                   '需要关注'}
                </span>
              </div>
            </div>
            <span className={cn(
              "font-bold",
              compact ? "text-xl" : "text-2xl",
              task.security_score >= 80 ? 'text-emerald-600' :
              task.security_score >= 60 ? 'text-amber-600' :
              'text-red-600'
            )}>
              {task.security_score.toFixed(0)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
});

export default StatsPanel;

