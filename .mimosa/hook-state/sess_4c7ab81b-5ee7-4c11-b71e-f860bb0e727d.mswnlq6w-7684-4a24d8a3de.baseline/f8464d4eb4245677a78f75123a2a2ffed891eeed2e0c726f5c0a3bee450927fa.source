/**
 * Task Detail Page
 */

import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ArrowLeft,
  Activity,
  AlertTriangle,
  CheckCircle,
  Clock,
  FileText,
  Calendar,
  GitBranch,
  Shield,
  Bug,
  TrendingUp,
  Download,
  Code,
  Lightbulb,
  Info,
  Zap,
  XCircle,
  ChevronDown,
  ChevronRight
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionPanel } from "@/components/ui/section-panel";
import { MetricCard } from "@/components/ui/metric-card";
import { StatusBadge } from "@/components/ui/status-badge";
import { getTaskStatusText, getSeverityText } from "@/shared/utils/uiText";
import { api } from "@/shared/config/database";
import type { AuditTask, AuditIssue } from "@/shared/types";
import { toast } from "sonner";
import ExportReportDialog from "@/components/reports/ExportReportDialog";
import { calculateTaskProgress } from "@/shared/utils/utils";
import { isRepositoryProject, getSourceTypeLabel, getRepositoryPlatformLabel } from "@/shared/utils/projectUtils";

// AI explanation parser
function parseAIExplanation(aiExplanation: string) {
  try {
    const parsed = JSON.parse(aiExplanation);
    if (parsed.xai) {
      return parsed.xai;
    }
    if (parsed.what || parsed.why || parsed.how) {
      return parsed;
    }
    return null;
  } catch (error) {
    return null;
  }
}

// Issues List Component
function IssuesList({ issues }: { issues: AuditIssue[] }) {

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'security': return <Shield className="w-4 h-4" />;
      case 'bug': return <AlertTriangle className="w-4 h-4" />;
      case 'performance': return <Zap className="w-4 h-4" />;
      case 'style': return <Code className="w-4 h-4" />;
      case 'maintainability': return <FileText className="w-4 h-4" />;
      default: return <Info className="w-4 h-4" />;
    }
  };

  const criticalIssues = issues.filter(issue => issue.severity === 'critical');
  const highIssues = issues.filter(issue => issue.severity === 'high');
  const mediumIssues = issues.filter(issue => issue.severity === 'medium');
  const lowIssues = issues.filter(issue => issue.severity === 'low');

  const renderIssue = (issue: AuditIssue, index: number) => (
    <div key={issue.id || index} className="rounded-xl border border-border bg-card p-4 shadow-card hover:border-border transition-all group">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-start space-x-3">
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${issue.severity === 'critical' ? 'bg-red-50 text-red-600' :
              issue.severity === 'high' ? 'bg-orange-50 text-orange-600' :
                issue.severity === 'medium' ? 'bg-amber-50 text-amber-600' :
                  'bg-blue-50 text-blue-600'
            }`}>
            {getTypeIcon(issue.issue_type)}
          </div>
          <div className="flex-1">
            <h4 className="font-semibold text-base text-foreground mb-1 group-hover:text-primary transition-colors">{issue.title}</h4>
            <div className="flex items-center space-x-1 text-xs text-muted-foreground">
              <FileText className="w-3 h-3" />
              <span className="technical-text px-2 py-0.5 rounded border border-border">{issue.file_path}</span>
            </div>
            {issue.line_number && (
              <div className="flex items-center space-x-1 text-xs text-muted-foreground mt-1">
                <span className="text-primary">&gt;</span>
                <span>LINE: {issue.line_number}</span>
                {issue.column_number && <span>, COL: {issue.column_number}</span>}
              </div>
            )}
          </div>
        </div>
        <StatusBadge value={issue.severity ?? "info"} label={getSeverityText(issue.severity ?? "info")} type="severity" />
      </div>

      {issue.description && (
        <div className="bg-muted border border-border p-3 mb-3 rounded-lg">
          <div className="flex items-center mb-1 border-b border-border pb-1">
            <Info className="w-3 h-3 text-muted-foreground mr-1" />
            <span className="font-medium text-muted-foreground text-xs">问题详情</span>
          </div>
          <p className="text-foreground text-xs leading-relaxed mt-1">
            {issue.description}
          </p>
        </div>
      )}

      {issue.code_snippet && (
        <div className="p-3 mb-3 border border-border rounded-lg">
          <div className="flex items-center justify-between mb-2 border-b border-border pb-1">
            <div className="flex items-center space-x-1">
              <Code className="w-4 h-4 text-primary" />
              <span className="text-xs font-medium text-muted-foreground">CODE_SNIPPET</span>
            </div>
            {issue.line_number && (
              <span className="text-muted-foreground text-xs">LINE: {issue.line_number}</span>
            )}
          </div>
          <div className="technical-text rounded-sm bg-slate-950 p-4 text-xs leading-6 text-slate-100">
            <pre className="overflow-x-auto">
              <code>{issue.code_snippet}</code>
            </pre>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {issue.suggestion && (
          <div className="bg-blue-50 border border-blue-200 p-3 rounded-lg">
            <div className="flex items-center mb-2 border-b border-blue-200 pb-1">
              <Lightbulb className="w-4 h-4 text-blue-600 mr-2" />
              <span className="font-medium text-blue-700 text-sm">修复建议</span>
            </div>
            <p className="text-blue-800 text-xs leading-relaxed">{issue.suggestion}</p>
          </div>
        )}

        {issue.ai_explanation && (() => {
          const parsedExplanation = parseAIExplanation(issue.ai_explanation);

          if (parsedExplanation) {
            return (
              <div className="bg-violet-50 border border-violet-200 p-3 rounded-lg">
                <div className="flex items-center mb-2 border-b border-violet-200 pb-1">
                  <Zap className="w-4 h-4 text-violet-600 mr-2" />
                  <span className="font-medium text-violet-700 text-sm">AI 解释</span>
                </div>

                <div className="space-y-2 text-xs">
                  {parsedExplanation.what && (
                    <div className="border-l-2 border-red-400 pl-2">
                      <span className="font-medium text-red-600">问题：</span>
                      <span className="text-foreground ml-1">{parsedExplanation.what}</span>
                    </div>
                  )}

                  {parsedExplanation.why && (
                    <div className="border-l-2 border-amber-400 pl-2">
                      <span className="font-medium text-amber-600">原因：</span>
                      <span className="text-foreground ml-1">{parsedExplanation.why}</span>
                    </div>
                  )}

                  {parsedExplanation.how && (
                    <div className="border-l-2 border-emerald-400 pl-2">
                      <span className="font-medium text-emerald-600">方案：</span>
                      <span className="text-foreground ml-1">{parsedExplanation.how}</span>
                    </div>
                  )}

                  {parsedExplanation.learn_more && (
                    <div className="border-l-2 border-blue-400 pl-2">
                      <span className="font-medium text-blue-600">链接：</span>
                      <a
                        href={parsedExplanation.learn_more}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:text-blue-500 hover:underline ml-1 font-medium"
                      >
                        {parsedExplanation.learn_more}
                      </a>
                    </div>
                  )}
                </div>
              </div>
            );
          } else {
            return (
              <div className="bg-violet-50 border border-violet-200 p-3 rounded-lg">
                <div className="flex items-center mb-2 border-b border-violet-200 pb-1">
                  <Zap className="w-4 h-4 text-violet-600 mr-2" />
                  <span className="font-medium text-violet-700 text-sm">AI 解释</span>
                </div>
                <p className="text-foreground text-xs leading-relaxed">{issue.ai_explanation}</p>
              </div>
            );
          }
        })()}
      </div>
    </div>
  );

  if (issues.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card p-16 text-center shadow-card">
        <CheckCircle className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
        <h3 className="text-xl font-semibold text-emerald-700 mb-2">代码质量优秀！</h3>
        <p className="text-emerald-600 mb-4">恭喜！没有发现任何问题</p>
        <div className="bg-emerald-50 border border-emerald-200 p-4 max-w-md mx-auto rounded-lg">
          <p className="text-emerald-700 text-sm">
            您的代码通过了所有质量检查，包括安全性、性能、可维护性等各个方面的评估。
          </p>
        </div>
      </div>
    );
  }

  return (
    <Tabs defaultValue="all" className="w-full">
      <TabsList className="grid w-full grid-cols-5 bg-muted border border-border p-1 h-auto gap-1 rounded-lg">
        <TabsTrigger value="all" className="data-[state=active]:bg-background data-[state=active]:text-foreground font-medium py-2 text-muted-foreground transition-all rounded-md text-xs">
          全部 ({issues.length})
        </TabsTrigger>
        <TabsTrigger value="critical" className="data-[state=active]:bg-red-50 data-[state=active]:text-red-700 font-medium py-2 text-muted-foreground transition-all rounded-md text-xs">
          严重 ({criticalIssues.length})
        </TabsTrigger>
        <TabsTrigger value="high" className="data-[state=active]:bg-orange-50 data-[state=active]:text-orange-700 font-medium py-2 text-muted-foreground transition-all rounded-md text-xs">
          高 ({highIssues.length})
        </TabsTrigger>
        <TabsTrigger value="medium" className="data-[state=active]:bg-amber-50 data-[state=active]:text-amber-700 font-medium py-2 text-muted-foreground transition-all rounded-md text-xs">
          中等 ({mediumIssues.length})
        </TabsTrigger>
        <TabsTrigger value="low" className="data-[state=active]:bg-blue-50 data-[state=active]:text-blue-700 font-medium py-2 text-muted-foreground transition-all rounded-md text-xs">
          低 ({lowIssues.length})
        </TabsTrigger>
      </TabsList>

      <TabsContent value="all" className="space-y-4 mt-6">
        {issues.map((issue, index) => renderIssue(issue, index))}
      </TabsContent>

      <TabsContent value="critical" className="space-y-4 mt-6">
        {criticalIssues.length > 0 ? (
          criticalIssues.map((issue, index) => renderIssue(issue, index))
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card text-center p-12 shadow-card">
            <CheckCircle className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-2">没有发现严重问题</h3>
            <p className="text-muted-foreground">代码在严重级别的检查中表现良好</p>
          </div>
        )}
      </TabsContent>

      <TabsContent value="high" className="space-y-4 mt-6">
        {highIssues.length > 0 ? (
          highIssues.map((issue, index) => renderIssue(issue, index))
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card text-center p-12 shadow-card">
            <CheckCircle className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-2">没有发现高优先级问题</h3>
            <p className="text-muted-foreground">代码在高优先级检查中表现良好</p>
          </div>
        )}
      </TabsContent>

      <TabsContent value="medium" className="space-y-4 mt-6">
        {mediumIssues.length > 0 ? (
          mediumIssues.map((issue, index) => renderIssue(issue, index))
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card text-center p-12 shadow-card">
            <CheckCircle className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-2">没有发现中等优先级问题</h3>
            <p className="text-muted-foreground">代码在中等优先级检查中表现良好</p>
          </div>
        )}
      </TabsContent>

      <TabsContent value="low" className="space-y-4 mt-6">
        {lowIssues.length > 0 ? (
          lowIssues.map((issue, index) => renderIssue(issue, index))
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card text-center p-12 shadow-card">
            <CheckCircle className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-2">没有发现低优先级问题</h3>
            <p className="text-muted-foreground">代码在低优先级检查中表现良好</p>
          </div>
        )}
      </TabsContent>
    </Tabs>
  );
}

export default function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const [task, setTask] = useState<AuditTask | null>(null);
  const [issues, setIssues] = useState<AuditIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [scanConfigExpanded, setScanConfigExpanded] = useState(false);

  // Zombie task detection
  const [lastProgressTime, setLastProgressTime] = useState<number>(Date.now());
  const [lastProgress, setLastProgress] = useState<number>(0);
  const ZOMBIE_TIMEOUT = 180000;

  useEffect(() => {
    if (id) {
      loadTaskDetail();
    }
  }, [id]);

  // Silent progress update for running tasks
  useEffect(() => {
    if (!task || !id) {
      return;
    }

    if (task.status === 'running' || task.status === 'pending') {
      const intervalId = setInterval(async () => {
        try {
          const [taskData, issuesData] = await Promise.all([
            api.getAuditTaskById(id),
            api.getAuditIssues(id)
          ]);

          if (!taskData) {
            console.error('任务数据获取失败');
            return;
          }

          const currentProgress = taskData.scanned_files || 0;
          if (currentProgress !== lastProgress) {
            setLastProgress(currentProgress);
            setLastProgressTime(Date.now());
          } else if (taskData.status === 'running' && Date.now() - lastProgressTime > ZOMBIE_TIMEOUT) {
            toast.warning("任务可能已停止响应，建议取消后重试", {
              id: 'zombie-warning',
              duration: 10000,
            });
          }

          if (
            taskData.status !== task.status ||
            taskData.scanned_files !== task.scanned_files ||
            taskData.issues_count !== task.issues_count
          ) {
            setTask(taskData);
            setIssues(issuesData);

            if (['completed', 'failed', 'cancelled'].includes(taskData.status)) {
              clearInterval(intervalId);
            }
          }
        } catch (error) {
          console.error('静默更新任务失败:', error);
          toast.error("获取任务状态失败，请检查网络连接", {
            id: 'network-error',
            duration: 5000,
          });
        }
      }, 3000);

      return () => clearInterval(intervalId);
    }
  }, [task?.status, task?.scanned_files, id, lastProgress, lastProgressTime]);

  const handleCancelTask = async () => {
    if (!id || cancelling) return;

    try {
      setCancelling(true);
      await api.cancelAuditTask(id);
      toast.success("任务已取消");
      const taskData = await api.getAuditTaskById(id);
      if (taskData) {
        setTask(taskData);
      }
    } catch (error: any) {
      console.error('取消任务失败:', error);
      toast.error(error?.response?.data?.detail || "取消任务失败");
    } finally {
      setCancelling(false);
    }
  };

  const loadTaskDetail = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const [taskData, issuesData] = await Promise.all([
        api.getAuditTaskById(id),
        api.getAuditIssues(id)
      ]);

      setTask(taskData);
      setIssues(issuesData);
    } catch (error) {
      console.error('Failed to load task detail:', error);
      toast.error("加载任务详情失败");
    } finally {
      setLoading(false);
    }
  };

  const getStatusBadge = (status: string) => {
    return <StatusBadge value={status} label={getTaskStatusText(status)} type="status" />;
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle className="w-4 h-4 text-emerald-400" />;
      case 'running': return <Activity className="w-4 h-4 text-sky-400" />;
      case 'failed': return <AlertTriangle className="w-4 h-4 text-rose-400" />;
      case 'cancelled': return <XCircle className="w-4 h-4 text-muted-foreground" />;
      default: return <Clock className="w-4 h-4 text-muted-foreground" />;
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center space-y-4">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-muted-foreground text-sm">加载任务详情...</p>
        </div>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="space-y-6 p-6 bg-background min-h-screen">
        <div className="flex items-center space-x-4">
          <Link to="/audit-tasks">
            <Button variant="outline" size="sm" className="h-10 w-10 p-0">
              <ArrowLeft className="w-5 h-5" />
            </Button>
          </Link>
        </div>
        <div className="rounded-xl border border-border bg-card p-16 text-center shadow-card">
          <AlertTriangle className="w-16 h-16 text-red-400 mx-auto mb-4" />
          <h3 className="text-xl font-semibold text-foreground mb-2">任务不存在</h3>
          <p className="text-muted-foreground">请检查任务ID是否正确</p>
        </div>
      </div>
    );
  }

  const progressPercentage = calculateTaskProgress(task.scanned_files, task.total_files);

  return (
    <div className="space-y-6 p-6 bg-background min-h-screen">

      <div className="flex items-center justify-between">
        <PageHeader
          eyebrow="审计任务"
          title="任务详情"
        />
        <div className="flex items-center space-x-3">
          <Link to="/audit-tasks">
            <Button variant="outline" size="sm">
              <ArrowLeft className="w-4 h-4 mr-2" />
              返回
            </Button>
          </Link>
          {getStatusBadge(task.status)}

          {(task.status === 'running' || task.status === 'pending') && (
            <Button
              size="sm"
              variant="destructive"
              onClick={handleCancelTask}
              disabled={cancelling}
            >
              <XCircle className="w-4 h-4 mr-2" />
              {cancelling ? '取消中...' : '取消任务'}
            </Button>
          )}

          {task.status === 'completed' && (
            <Button
              size="sm"
              onClick={() => setExportDialogOpen(true)}
            >
              <Download className="w-4 h-4 mr-2" />
              导出报告
            </Button>
          )}
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard label="扫描进度" value={`${progressPercentage}%`} icon={<Activity className="w-5 h-5" />} tone="blue" />
        <MetricCard label="发现问题" value={task.issues_count} icon={<Bug className="w-5 h-5" />} tone="amber" />
        <MetricCard label="质量评分" value={task.quality_score.toFixed(1)} icon={<TrendingUp className="w-5 h-5" />} tone="green" />
        <MetricCard label="代码行数" value={task.total_lines.toLocaleString()} icon={<FileText className="w-5 h-5" />} tone="default" />
      </div>

      {/* Task Info */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <SectionPanel title="任务信息">
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">任务类型</p>
                  <p className="text-base font-semibold text-foreground">
                    {task.task_type === 'repository' ? '仓库审计任务' : '即时分析任务'}
                  </p>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">目标分支</p>
                  <p className="text-base font-semibold text-foreground flex items-center">
                    <GitBranch className="w-4 h-4 mr-1" />
                    {task.branch_name || '默认分支'}
                  </p>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">创建时间</p>
                  <p className="text-base font-semibold text-foreground flex items-center">
                    <Calendar className="w-4 h-4 mr-1" />
                    {formatDate(task.created_at)}
                  </p>
                </div>
                {task.completed_at && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">完成时间</p>
                    <p className="text-base font-semibold text-foreground flex items-center">
                      <CheckCircle className="w-4 h-4 mr-1" />
                      {formatDate(task.completed_at)}
                    </p>
                  </div>
                )}
              </div>

              {task.exclude_patterns && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-2">排除模式</p>
                  <div className="flex flex-wrap gap-2">
                    {JSON.parse(task.exclude_patterns).map((pattern: string) => (
                      <Badge key={pattern} variant="outline" className="border-slate-200 bg-slate-50 text-slate-600">
                        {pattern}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {task.scan_config && (
                <div>
                  <button
                    type="button"
                    onClick={() => setScanConfigExpanded(!scanConfigExpanded)}
                    className="flex items-center gap-2 text-xs font-medium text-muted-foreground mb-2 hover:text-foreground transition-colors"
                  >
                    {scanConfigExpanded ? (
                      <ChevronDown className="w-4 h-4" />
                    ) : (
                      <ChevronRight className="w-4 h-4" />
                    )}
                    扫描配置
                  </button>
                  {scanConfigExpanded && (
                    <div className="technical-text rounded-sm bg-slate-950 p-4 text-xs leading-6 text-slate-100 overflow-x-auto">
                      <pre>{JSON.stringify(JSON.parse(task.scan_config), null, 2)}</pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          </SectionPanel>
        </div>

        <div>
          <SectionPanel title="项目信息">
            <div className="space-y-4">
              {task.project ? (
                <>
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">项目名称</p>
                    <Link to={`/projects/${task.project.id}`} className="text-base font-semibold text-primary hover:underline">
                      {task.project.name}
                    </Link>
                  </div>
                  {task.project.description && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">项目描述</p>
                      <p className="text-sm text-foreground">{task.project.description}</p>
                    </div>
                  )}
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">项目类型</p>
                    <p className="text-base font-semibold text-foreground">{getSourceTypeLabel(task.project.source_type)}</p>
                  </div>
                  {isRepositoryProject(task.project) && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">仓库平台</p>
                      <p className="text-base font-semibold text-foreground">{getRepositoryPlatformLabel(task.project.repository_type)}</p>
                    </div>
                  )}
                  {task.project.programming_languages && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-2">编程语言</p>
                      <div className="flex flex-wrap gap-1">
                        {JSON.parse(task.project.programming_languages).map((lang: string) => (
                          <Badge key={lang} variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
                            {lang}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <p className="text-muted-foreground font-medium">项目信息不可用</p>
              )}
            </div>
          </SectionPanel>
        </div>
      </div>

      {/* Issues List */}
      {issues.length > 0 && (
        <SectionPanel title={`发现的问题 (${issues.length})`}>
          <IssuesList issues={issues} />
        </SectionPanel>
      )}

      {/* Export Report Dialog */}
      {task && (
        <ExportReportDialog
          open={exportDialogOpen}
          onOpenChange={setExportDialogOpen}
          task={task}
          issues={issues}
        />
      )}
    </div>
  );
}
