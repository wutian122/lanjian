import { useMemo, useState } from "react";
import type { AgentTask } from "@/shared/api/agentTasks";
import type { AuditTask } from "@/shared/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Search } from "lucide-react";
import { VerificationStatusBreakdown } from "@/shared/components/VerificationStatusBreakdown";

export type FilterState = {
  search: string;
  status: "all" | "completed" | "failed" | "running" | "pending";
  type: "all" | "agent" | "regular";
};

interface TaskReferencePanelProps {
  agentTasks: AgentTask[];
  regularTasks: AuditTask[];
  activeTaskId: string | null;
  activeTaskKind: "agent" | "regular" | null;
  onReferenceAgent: (task: AgentTask) => void;
  onReferenceRegular: (task: AuditTask) => void;
  onOpenAgentWorkbench: (task: AgentTask) => void;
  onOpenRegularTask: (task: AuditTask) => void;
  filterState: FilterState;
  onFilterChange: (filter: FilterState) => void;
}

const STATUS_OPTIONS: { value: FilterState["status"]; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "running", label: "进行中" },
  { value: "pending", label: "待处理" },
];

export function TaskReferencePanel({
  agentTasks,
  regularTasks,
  activeTaskId,
  activeTaskKind,
  onReferenceAgent,
  onReferenceRegular,
  onOpenAgentWorkbench,
  onOpenRegularTask,
  filterState,
  onFilterChange,
}: TaskReferencePanelProps) {
  const [tab, setTab] = useState<"all" | "agent" | "regular">(filterState.type || "all");

  const handleTabChange = (newTab: "all" | "agent" | "regular") => {
    setTab(newTab);
    onFilterChange({ ...filterState, type: newTab });
  };

  const filteredAgentTasks = useMemo(() => {
    return agentTasks.filter(task => {
      if (filterState.status !== "all" && task.status !== filterState.status) return false;
      if (filterState.search && !(task.name || "").toLowerCase().includes(filterState.search.toLowerCase())) return false;
      return true;
    });
  }, [agentTasks, filterState]);

  const filteredRegularTasks = useMemo(() => {
    return regularTasks.filter(task => {
      if (filterState.status !== "all" && task.status !== filterState.status) return false;
      if (filterState.search && !(task.project?.name || "").toLowerCase().includes(filterState.search.toLowerCase())) return false;
      return true;
    });
  }, [regularTasks, filterState]);

  const showAgent = tab === "all" || tab === "agent";
  const showRegular = tab === "all" || tab === "regular";

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="border-b border-border px-4 py-3">
        <div className="text-sm font-semibold text-foreground">审计任务引用</div>
        <div className="text-xs text-muted-foreground">选择任务作为当前 AI 上下文</div>
      </div>

      <div className="border-b border-border px-3 py-2 space-y-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <Input
            placeholder="搜索项目名称..."
            value={filterState.search}
            onChange={(e) => onFilterChange({ ...filterState, search: e.target.value })}
            className="pl-8 h-8 text-xs"
          />
        </div>
        <div className="flex items-center gap-1">
          {STATUS_OPTIONS.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => onFilterChange({ ...filterState, status: opt.value })}
              className={`px-2 py-0.5 text-[11px] rounded transition-colors ${
                filterState.status === opt.value
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:bg-muted/80"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          {([{ value: "all", label: "全部" }, { value: "agent", label: "Agent" }, { value: "regular", label: "普通" }] as const).map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => handleTabChange(opt.value)}
              className={`px-2.5 py-0.5 text-[11px] rounded transition-colors ${
                tab === opt.value
                  ? "bg-primary/10 text-primary border border-primary/30"
                  : "text-muted-foreground border border-transparent hover:bg-muted"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 custom-scrollbar">
        <div className="space-y-4">
          {showAgent && (
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Agent 审计任务</div>
              <div className="space-y-2">
                {filteredAgentTasks.length > 0 ? filteredAgentTasks.map(task => {
                  const isActive = task.id === activeTaskId && activeTaskKind === "agent";
                  return (
                    <div
                      key={task.id}
                      className={[
                        "rounded-lg border p-3 transition-colors group",
                        isActive ? "border-blue-200 bg-blue-50" : "border-border bg-background",
                      ].join(" ")}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="text-sm font-medium text-foreground">{task.name || "未命名 Agent 任务"}</div>
                          <div className="mt-1 text-xs text-muted-foreground">{task.current_phase || task.task_type}</div>
                        </div>
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {task.status}
                        </Badge>
                      </div>

                      <div className="mt-2 text-xs text-muted-foreground">
                        发现 {task.findings_count} 个问题，已验证 {task.verified_count} 个，进度 {(task.progress_percentage || 0).toFixed(0)}%
                      </div>
                      <VerificationStatusBreakdown
                        breakdown={task.verification_status_breakdown}
                        className="mt-1.5"
                      />

                      <div className="mt-2 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => onReferenceAgent(task)}>
                          引用到会话
                        </Button>
                        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => onOpenAgentWorkbench(task)}>
                          打开工作台
                        </Button>
                      </div>
                    </div>
                  );
                }) : (
                  <div className="text-xs text-muted-foreground py-4 text-center">无匹配的 Agent 任务</div>
                )}
              </div>
            </div>
          )}

          {showRegular && (
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">普通审计任务</div>
              <div className="space-y-2">
                {filteredRegularTasks.length > 0 ? filteredRegularTasks.map(task => {
                  const isActive = task.id === activeTaskId && activeTaskKind === "regular";
                  return (
                    <div
                      key={task.id}
                      className={[
                        "rounded-lg border p-3 transition-colors group",
                        isActive ? "border-blue-200 bg-blue-50" : "border-border bg-background",
                      ].join(" ")}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="text-sm font-medium text-foreground">{task.project?.name || "普通审计任务"}</div>
                          <div className="mt-1 text-xs text-muted-foreground">{task.task_type}</div>
                        </div>
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {task.status}
                        </Badge>
                      </div>

                      <div className="mt-2 text-xs text-muted-foreground">
                        问题 {task.issues_count} 个，扫描 {(task.scanned_files || 0)} / {(task.total_files || 0)} 文件
                      </div>

                      <div className="mt-2 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => onReferenceRegular(task)}>
                          引用到会话
                        </Button>
                        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => onOpenRegularTask(task)}>
                          查看详情
                        </Button>
                      </div>
                    </div>
                  );
                }) : (
                  <div className="text-xs text-muted-foreground py-4 text-center">无匹配的普通任务</div>
                )}
              </div>
            </div>
          )}

          {showAgent && showRegular && filteredAgentTasks.length === 0 && filteredRegularTasks.length === 0 && (
            <div className="text-xs text-muted-foreground py-8 text-center">没有匹配的任务</div>
          )}
        </div>
      </div>
    </div>
  );
}
