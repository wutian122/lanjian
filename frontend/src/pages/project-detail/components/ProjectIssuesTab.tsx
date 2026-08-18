import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle, FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { getSeverityText } from "@/shared/utils/uiText";
import type { IssuesSummary, LatestProblem } from "@/shared/types";

export function ProjectIssuesTab(props: {
  hasAnyTasks: boolean;
  issuesSummary: IssuesSummary;
  loading: boolean;
  latestProblems: LatestProblem[];
  formatDate: (dateString: string) => string;
}) {
  const { hasAnyTasks, issuesSummary, loading, latestProblems, formatDate } = props;

  return (
    <>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 mb-0 pb-0 border-0">
          <AlertTriangle className="w-5 h-5 text-amber-500" />
          <h3 className="text-lg font-semibold text-foreground">最新发现的问题</h3>
        </div>
        {hasAnyTasks && (
          <p className="text-sm text-muted-foreground font-mono">
            已完成审计任务：{issuesSummary.completedAuditTasksCount} 次 / Agent审计：{issuesSummary.completedAgentTasksCount} 次
            {issuesSummary.isLimited ? `（各仅展示最近 ${issuesSummary.maxTasks} 次）` : ""}
            ，共 {latestProblems.length} 条问题/漏洞
          </p>
        )}
      </div>

      {loading ? (
        <div className="text-center py-12">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-muted-foreground">正在加载问题列表...</p>
        </div>
      ) : latestProblems.length > 0 ? (
        <div className="space-y-4">
          {latestProblems.map((issue, index) => (
            <div key={index} className="rounded-xl border border-border bg-card p-4 shadow-card hover:border-border transition-all">
              <div className="flex items-start justify-between">
                <div className="flex items-start space-x-3">
                  <div
                    className={`w-8 h-8 rounded-lg flex items-center justify-center ${issue.severity === "critical"
                      ? "bg-red-50 text-red-600"
                      : issue.severity === "high"
                        ? "bg-orange-50 text-orange-600"
                        : issue.severity === "medium"
                          ? "bg-amber-50 text-amber-600"
                          : "bg-blue-50 text-blue-600"
                      }`}
                  >
                    <AlertTriangle className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="font-semibold text-base text-foreground mb-1">{issue.title}</h4>
                    <div className="flex items-center space-x-2 text-xs text-muted-foreground">
                      <span className="technical-text px-2 py-0.5 rounded border border-border">
                        {issue.file_path || "未知文件"}
                        {issue.line_number != null
                          ? issue.line_end != null && issue.line_end !== issue.line_number
                            ? `:${issue.line_number}-${issue.line_end}`
                            : `:${issue.line_number}`
                          : ""}
                      </span>
                      <span>{issue.category || "-"}</span>
                      {issue.task_created_at && (
                        <span className="bg-muted px-2 py-0.5 rounded border border-border">
                          {issue.kind === "agent" ? "Agent" : "Audit"} {issue.task_id?.slice(0, 8)} ·{" "}
                          {formatDate(issue.task_created_at)}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Link to={issue.kind === "agent" ? `/agent-audit/${issue.task_id}` : `/tasks/${issue.task_id}`}>
                    <Button variant="outline" size="sm">
                      <FileText className="w-4 h-4 mr-2" />
                      查看任务
                    </Button>
                  </Link>
                  <StatusBadge value={issue.severity} label={getSeverityText(issue.severity)} type="severity" />
                </div>
              </div>
              <p className="mt-3 text-sm text-muted-foreground border-t border-border pt-3">{issue.description || "-"}</p>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card p-12 text-center">
          <CheckCircle className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-foreground mb-2">未发现问题</h3>
          <p className="text-sm text-muted-foreground">最近一次审计/Agent审计未发现明显问题，或尚未进行审计。</p>
        </div>
      )}
    </>
  );
}


