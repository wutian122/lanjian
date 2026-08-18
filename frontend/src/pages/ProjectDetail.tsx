/**
 * Project Detail Page
 */

import { useMemo, useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ArrowLeft,
  Edit,
  ExternalLink,
  Shield,
  Activity,
  AlertTriangle,
  CheckCircle,
  Clock,
  XCircle,
  FileText,
  Upload,
  GitBranch
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionPanel } from "@/components/ui/section-panel";
import { StatusBadge } from "@/components/ui/status-badge";
import { getTaskStatusText } from "@/shared/utils/uiText";
import { api } from "@/shared/config/database";
import type { Project, AuditTask, CreateProjectForm, AuditIssue } from "@/shared/types";
import type { AgentFinding, AgentTask } from "@/shared/api/agentTasks";
import { getAgentTasks } from "@/shared/api/agentTasks";
import { apiClient } from "@/shared/api/serverClient";
import { isRepositoryProject, getSourceTypeLabel, getRepositoryPlatformLabel } from "@/shared/utils/projectUtils";
import { toast } from "sonner";
import CreateTaskDialog from "@/components/audit/CreateTaskDialog";
import TerminalProgressDialog from "@/components/audit/TerminalProgressDialog";
import { SUPPORTED_LANGUAGES, REPOSITORY_PLATFORMS } from "@/shared/constants";
import type { AggregatedAgentFinding, AggregatedAuditIssue, IssuesSummary, LatestProblem, UnifiedTask } from "@/shared/types";
import {
  PROJECT_DETAIL_ISSUES_FETCH_CONCURRENCY as ISSUES_FETCH_CONCURRENCY,
  PROJECT_DETAIL_ISSUES_MAX_TASKS as ISSUES_MAX_TASKS,
  PROJECT_DETAIL_REQUEST_TIMEOUT_MS as REQUEST_TIMEOUT_MS
} from "@/shared/constants";
import { ProjectIssuesTab } from "@/pages/project-detail/components/ProjectIssuesTab";
import { ProjectTasksTab } from "@/pages/project-detail/components/ProjectTasksTab";
import { ProjectStatsCards, type ProjectCombinedStats } from "@/pages/project-detail/components/ProjectStatsCards";

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [auditTasks, setAuditTasks] = useState<AuditTask[]>([]);
  const [agentTasks, setAgentTasks] = useState<AgentTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateTaskDialog, setShowCreateTaskDialog] = useState(false);
  const [showTerminalDialog, setShowTerminalDialog] = useState(false);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<CreateProjectForm>({
    name: "",
    description: "",
    source_type: "repository",
    repository_url: "",
    repository_type: "github",
    default_branch: "main",
    programming_languages: []
  });
  const [activeTab, setActiveTab] = useState("overview");
  const [latestIssues, setLatestIssues] = useState<AggregatedAuditIssue[]>([]);
  const [latestFindings, setLatestFindings] = useState<AggregatedAgentFinding[]>([]);
  const [loadingIssues, setLoadingIssues] = useState(false);
  const [issuesSummary, setIssuesSummary] = useState<IssuesSummary>({
    completedAuditTasksCount: 0,
    completedAgentTasksCount: 0,
    fetchedAuditTasksCount: 0,
    fetchedAgentTasksCount: 0,
    isLimited: false,
    maxTasks: 20
  });

  // ============ Helpers ============

  async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> {
    let timeoutId: number | undefined;
    const timeoutPromise = new Promise<T>((_resolve, reject) => {
      timeoutId = window.setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
    });
    try {
      return await Promise.race([promise, timeoutPromise]);
    } finally {
      if (timeoutId != null) window.clearTimeout(timeoutId);
    }
  }

  async function mapWithConcurrency<T, R>(
    items: T[],
    concurrency: number,
    mapper: (item: T) => Promise<R>
  ): Promise<PromiseSettledResult<R>[]> {
    const results: PromiseSettledResult<R>[] = new Array(items.length);
    let nextIndex = 0;

    async function worker(): Promise<void> {
      while (true) {
        const currentIndex = nextIndex++;
        if (currentIndex >= items.length) return;
        try {
          const value = await mapper(items[currentIndex]);
          results[currentIndex] = { status: "fulfilled", value };
        } catch (reason) {
          results[currentIndex] = { status: "rejected", reason };
        }
      }
    }

    const workers = Array.from({ length: Math.max(1, concurrency) }, () => worker());
    await Promise.all(workers);
    return results;
  }

  async function fetchAuditIssues(taskId: string): Promise<AuditIssue[]> {
    // Use apiClient directly so we can control timeout behavior at the call site
    const res = await withTimeout(apiClient.get(`/tasks/${taskId}/issues`), REQUEST_TIMEOUT_MS, `GET /tasks/${taskId}/issues`);
    return res.data;
  }

  async function fetchAgentFindings(taskId: string): Promise<AgentFinding[]> {
    const res = await withTimeout(apiClient.get(`/agent-tasks/${taskId}/findings`), REQUEST_TIMEOUT_MS, `GET /agent-tasks/${taskId}/findings`);
    return res.data;
  }

  useEffect(() => {
    if (activeTab === 'issues' && (auditTasks.length > 0 || agentTasks.length > 0)) {
      loadLatestIssues();
    }
  }, [activeTab, auditTasks, agentTasks]);

  const loadLatestIssues = async () => {
    const completedAuditTasks = auditTasks
      .filter((t: AuditTask) => t.status === 'completed')
      .sort((a: AuditTask, b: AuditTask) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    const completedAgentTasks = agentTasks
      .filter((t: AgentTask) => t.status === 'completed')
      .sort((a: AgentTask, b: AgentTask) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

    const limitedAuditTasks = completedAuditTasks.slice(0, ISSUES_MAX_TASKS);
    const limitedAgentTasks = completedAgentTasks.slice(0, ISSUES_MAX_TASKS);

    setIssuesSummary({
      completedAuditTasksCount: completedAuditTasks.length,
      completedAgentTasksCount: completedAgentTasks.length,
      fetchedAuditTasksCount: limitedAuditTasks.length,
      fetchedAgentTasksCount: limitedAgentTasks.length,
      isLimited: completedAuditTasks.length > ISSUES_MAX_TASKS || completedAgentTasks.length > ISSUES_MAX_TASKS,
      maxTasks: ISSUES_MAX_TASKS
    });

    if (limitedAuditTasks.length === 0 && limitedAgentTasks.length === 0) {
      setLatestIssues([]);
      setLatestFindings([]);
      return;
    }

      setLoadingIssues(true);
      try {
      const [issuesResults, findingsResults] = await Promise.all([
        mapWithConcurrency(limitedAuditTasks, ISSUES_FETCH_CONCURRENCY, async (task: AuditTask) => {
          const issues = await fetchAuditIssues(task.id);
          const enriched: AggregatedAuditIssue[] = (issues || []).map((issue) => ({
            ...(issue as AuditIssue),
            task_created_at: task.created_at,
            task_completed_at: task.completed_at
          }));
          return enriched;
        }),
        mapWithConcurrency(limitedAgentTasks, ISSUES_FETCH_CONCURRENCY, async (task: AgentTask) => {
          const findings = await fetchAgentFindings(task.id);
          const enriched: AggregatedAgentFinding[] = (findings || []).map((finding) => ({
            ...(finding as AgentFinding),
            task_created_at: task.created_at,
            task_completed_at: task.completed_at
          }));
          return enriched;
        })
      ]);

      const flatIssues = issuesResults
        .filter((r: PromiseSettledResult<AggregatedAuditIssue[]>): r is PromiseFulfilledResult<AggregatedAuditIssue[]> => r.status === 'fulfilled')
        .flatMap((r: PromiseFulfilledResult<AggregatedAuditIssue[]>) => r.value);
      const flatFindings = findingsResults
        .filter((r: PromiseSettledResult<AggregatedAgentFinding[]>): r is PromiseFulfilledResult<AggregatedAgentFinding[]> => r.status === 'fulfilled')
        .flatMap((r: PromiseFulfilledResult<AggregatedAgentFinding[]>) => r.value);

      const severityRank: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };
      flatIssues.sort((a: AggregatedAuditIssue, b: AggregatedAuditIssue) => {
        const createdAtA = new Date(a.created_at).getTime();
        const createdAtB = new Date(b.created_at).getTime();
        if (createdAtA !== createdAtB) return createdAtB - createdAtA;

        const severityA = severityRank[a.severity] ?? 0;
        const severityB = severityRank[b.severity] ?? 0;
        if (severityA !== severityB) return severityB - severityA;

        const taskCreatedAtA = a.task_created_at ? new Date(a.task_created_at).getTime() : 0;
        const taskCreatedAtB = b.task_created_at ? new Date(b.task_created_at).getTime() : 0;
        return taskCreatedAtB - taskCreatedAtA;
      });

      setLatestIssues(flatIssues);
      flatFindings.sort((a: AggregatedAgentFinding, b: AggregatedAgentFinding) => {
        const createdAtA = new Date(a.created_at).getTime();
        const createdAtB = new Date(b.created_at).getTime();
        if (createdAtA !== createdAtB) return createdAtB - createdAtA;

        const severityA = severityRank[String(a.severity || '').toLowerCase()] ?? 0;
        const severityB = severityRank[String(b.severity || '').toLowerCase()] ?? 0;
        if (severityA !== severityB) return severityB - severityA;

        const taskCreatedAtA = a.task_created_at ? new Date(a.task_created_at).getTime() : 0;
        const taskCreatedAtB = b.task_created_at ? new Date(b.task_created_at).getTime() : 0;
        return taskCreatedAtB - taskCreatedAtA;
      });
      setLatestFindings(flatFindings);
      } catch (error) {
        console.error('Failed to load issues:', error);
        toast.error("加载问题列表失败");
      } finally {
        setLoadingIssues(false);
      }
  };

  const latestProblems: LatestProblem[] = useMemo(() => {
    const parsePathLineFromTitle = (title: string) => {
      // Pattern examples:
      // "path/to/File.java:66 - Something"
      // "path/to/File.java:137-138 - Something"
      // Security hardening:
      // - Cap title length
      // - Restrict acceptable path characters
      // - Reject absolute paths and path traversal segments
      const safeTitle = String(title || "").slice(0, 500);
      const match = safeTitle.match(/^([A-Za-z0-9_.\-\/]+):(\d+)(?:-(\d+))?\s*-\s*(.+)$/);
      if (!match) return null;
      const [, rawPath, lineStartStr, lineEndStr, rest] = match;

      if (rawPath.startsWith("/") || rawPath.includes("..") || rawPath.includes("\u0000")) return null;

      const lineStart = Number(lineStartStr);
      const lineEnd = lineEndStr ? Number(lineEndStr) : null;
      const normalizedLineStart = Number.isFinite(lineStart) ? lineStart : NaN;
      const normalizedLineEnd = lineEnd != null && Number.isFinite(lineEnd) ? lineEnd : null;
      if (!Number.isFinite(normalizedLineStart) || normalizedLineStart <= 0) return null;
      return {
        file_path: rawPath,
        line_start: normalizedLineStart,
        line_end: normalizedLineEnd != null && normalizedLineEnd > 0 ? normalizedLineEnd : null,
        rest_title: rest,
      };
    };

    const normalizeSeverity = (s: unknown): LatestProblem['severity'] => {
      const v = String(s || '').toLowerCase();
      if (v === 'critical') return 'critical';
      if (v === 'high') return 'high';
      if (v === 'medium') return 'medium';
      return 'low';
    };

    const audit: LatestProblem[] = latestIssues.map((i) => ({
      // AuditIssue 在后端 schema 里可能叫 message（frontend type 没显式定义），这里做兼容兜底
      // 同时优先展示更“可读”的说明字段，避免 UI 出现大量 '-'
      kind: 'audit',
      id: i.id,
      task_id: i.task_id,
      task_created_at: i.task_created_at,
      created_at: i.created_at,
      severity: normalizeSeverity(i.severity),
      title: i.title || '(未命名问题)',
      description:
        i.description ??
        (i as any).message ??
        (i as any).ai_explanation ??
        (i as any).suggestion ??
        (i as any).code_snippet ??
        null,
      file_path: i.file_path,
      line_number: i.line_number ?? null,
      category: (i as any).issue_type ?? null,
    }));

    const agent: LatestProblem[] = latestFindings.map((f) => {
      const rawTitle = f.title || '(未命名漏洞)';
      const parsed = (!f.file_path || f.file_path === '-') ? parsePathLineFromTitle(rawTitle) : null;

      return {
        kind: 'agent',
        id: f.id,
        task_id: f.task_id,
        task_created_at: f.task_created_at,
        created_at: f.created_at,
        severity: normalizeSeverity(f.severity),
        // 如果 title 里带了 "path:line - xxx"，则剥离掉路径前缀，仅保留 xxx，避免标题重复且过长
        title: parsed?.rest_title || rawTitle,
        description: f.description,
        // 如果后端没给 file_path，尽量从 title 解析出来填到“文件”列
        file_path: f.file_path ?? parsed?.file_path ?? null,
        line_number: ((f.line_start ?? parsed?.line_start ?? null) as any),
        line_end: ((f.line_end ?? parsed?.line_end ?? null) as any),
        category: (f as any).vulnerability_type ?? null,
      };
    });

    const merged = [...audit, ...agent];
    // 按时间倒序（最新在前），时间相同再按严重程度
    const severityRank: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };
    merged.sort((a, b) => {
      const createdAtA = new Date(a.created_at).getTime();
      const createdAtB = new Date(b.created_at).getTime();
      if (createdAtA !== createdAtB) return createdAtB - createdAtA;

      const severityA = severityRank[a.severity] ?? 0;
      const severityB = severityRank[b.severity] ?? 0;
      if (severityA !== severityB) return severityB - severityA;

      const taskCreatedAtA = a.task_created_at ? new Date(a.task_created_at).getTime() : 0;
      const taskCreatedAtB = b.task_created_at ? new Date(b.task_created_at).getTime() : 0;
      return taskCreatedAtB - taskCreatedAtA;
    });
    return merged;
  }, [latestIssues, latestFindings]);

  const handleOpenSettings = () => {
    if (!project) return;

    setEditForm({
      name: project.name,
      description: project.description || "",
      source_type: project.source_type || "repository",
      repository_url: project.repository_url || "",
      repository_type: project.repository_type || "github",
      default_branch: project.default_branch || "main",
      programming_languages: project.programming_languages ? JSON.parse(project.programming_languages) : []
    });

    setActiveTab("settings");
  };

  const formatLanguageName = (lang: string): string => {
    const nameMap: Record<string, string> = {
      'javascript': 'JavaScript',
      'typescript': 'TypeScript',
      'python': 'Python',
      'java': 'Java',
      'go': 'Go',
      'rust': 'Rust',
      'cpp': 'C++',
      'csharp': 'C#',
      'php': 'PHP',
      'ruby': 'Ruby',
      'swift': 'Swift',
      'kotlin': 'Kotlin'
    };
    return nameMap[lang] || lang.charAt(0).toUpperCase() + lang.slice(1);
  };

  const supportedLanguages = SUPPORTED_LANGUAGES.map(formatLanguageName);

  useEffect(() => {
    if (id) {
      loadProjectData();
    }
  }, [id]);

  const loadProjectData = async () => {
    if (!id) return;

    try {
      setLoading(true);
      const [projectRes, auditTasksRes, agentTasksRes] = await Promise.allSettled([
        api.getProjectById(id),
        api.getAuditTasks(id),
        getAgentTasks({ project_id: id })
      ]);

      if (projectRes.status === 'fulfilled') {
        setProject(projectRes.value);
      } else {
        console.error('Failed to load project:', projectRes.reason);
        setProject(null);
      }

      if (auditTasksRes.status === 'fulfilled') {
        setAuditTasks(Array.isArray(auditTasksRes.value) ? auditTasksRes.value : []);
      } else {
        console.error('Failed to load audit tasks:', auditTasksRes.reason);
        setAuditTasks([]);
      }

      if (agentTasksRes.status === 'fulfilled') {
        setAgentTasks(Array.isArray(agentTasksRes.value) ? agentTasksRes.value : []);
      } else {
        // do not silently swallow: log for debugging and degrade gracefully
        console.warn('Failed to load agent tasks:', agentTasksRes.reason);
        setAgentTasks([]);
      }

    } catch (error) {
      console.error('Failed to load project data:', error);
      toast.error("加载项目数据失败");
    } finally {
      setLoading(false);
    }
  };

  const unifiedTasks: UnifiedTask[] = useMemo(() => {
    const merged: UnifiedTask[] = [
      ...auditTasks.map((t) => ({ kind: 'audit' as const, task: t })),
      ...agentTasks.map((t) => ({ kind: 'agent' as const, task: t })),
    ];
    merged.sort((a, b) => new Date((b.task as any).created_at).getTime() - new Date((a.task as any).created_at).getTime());
    return merged;
  }, [auditTasks, agentTasks]);

  const combinedStats: ProjectCombinedStats = useMemo(() => {
    const totalTasks = auditTasks.length + agentTasks.length;
    const completedTasks =
      auditTasks.filter((t) => t.status === 'completed').length +
      agentTasks.filter((t) => t.status === 'completed').length;
    const totalIssues =
      auditTasks.reduce((sum, t) => sum + (t.issues_count || 0), 0) +
      agentTasks.reduce((sum, t) => sum + (t.findings_count || 0), 0);
    const avgQualityScore = totalTasks > 0
      ? (
        (auditTasks.reduce((sum, t) => sum + (t.quality_score || 0), 0) +
          agentTasks.reduce((sum, t) => sum + (t.quality_score || 0), 0)) / totalTasks
      )
      : 0;
    return { totalTasks, completedTasks, totalIssues, avgQualityScore };
  }, [auditTasks, agentTasks]);

  const handleRunAudit = () => {
    setShowCreateTaskDialog(true);
  };

  const handleSaveSettings = async () => {
    if (!id) return;

    if (!editForm.name.trim()) {
      toast.error("项目名称不能为空");
      return;
    }

    try {
      await api.updateProject(id, editForm);
      toast.success("项目信息已保存");
      loadProjectData();
    } catch (error) {
      console.error('Failed to update project:', error);
      toast.error("保存失败");
    }
  };

  const handleToggleLanguage = (lang: string) => {
    const currentLanguages = editForm.programming_languages || [];
    const newLanguages = currentLanguages.includes(lang)
      ? currentLanguages.filter(l => l !== lang)
      : [...currentLanguages, lang];

    setEditForm({ ...editForm, programming_languages: newLanguages });
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

  const handleCreateTask = () => {
    setShowCreateTaskDialog(true);
  };

  const handleTaskCreated = () => {
    toast.success("审计任务已创建", {
      description: '因为网络和代码文件大小等因素，审计时长通常至少需要1分钟，请耐心等待...',
      duration: 5000
    });
    loadProjectData();
  };

  const handleFastScanStarted = (taskId: string) => {
    setCurrentTaskId(taskId);
    setShowTerminalDialog(true);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center space-y-4">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-muted-foreground text-sm">加载项目数据...</p>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-xl border border-border bg-card p-8 text-center shadow-card">
          <AlertTriangle className="w-16 h-16 text-red-400 mx-auto mb-4" />
          <h2 className="text-2xl font-semibold text-foreground mb-2">项目未找到</h2>
          <p className="text-muted-foreground mb-4">请检查项目ID是否正确</p>
          <Link to="/projects">
            <Button>
              <ArrowLeft className="w-4 h-4 mr-2" />
              返回项目列表
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6 bg-background min-h-screen">
      <PageHeader
        eyebrow="项目详情"
        title={project.name}
        actions={
          <>
            <Button onClick={handleRunAudit}>
              <Shield className="w-4 h-4 mr-2" />
              启动审计
            </Button>
            <Button variant="outline" onClick={handleOpenSettings}>
              <Edit className="w-4 h-4 mr-2" />
              编辑
            </Button>
          </>
        }
      />

      {/* 统计卡片 */}
      <ProjectStatsCards stats={combinedStats} />

      {/* 主要内容 */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid w-full grid-cols-4 bg-muted border border-border p-1 h-auto gap-1 rounded-lg">
          <TabsTrigger value="overview" className="data-[state=active]:bg-background data-[state=active]:text-foreground font-medium py-2 text-muted-foreground transition-all rounded-md">项目概览</TabsTrigger>
          <TabsTrigger value="tasks" className="data-[state=active]:bg-background data-[state=active]:text-foreground font-medium py-2 text-muted-foreground transition-all rounded-md">审计任务</TabsTrigger>
          <TabsTrigger value="issues" className="data-[state=active]:bg-background data-[state=active]:text-foreground font-medium py-2 text-muted-foreground transition-all rounded-md">问题管理</TabsTrigger>
          <TabsTrigger value="settings" className="data-[state=active]:bg-background data-[state=active]:text-foreground font-medium py-2 text-muted-foreground transition-all rounded-md">项目设置</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex flex-col gap-6 mt-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* 项目信息 */}
            <SectionPanel title="项目信息">
              <div className="space-y-4">
                <div className="space-y-3">
                  {project.repository_url && (
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">仓库地址</span>
                      <a
                        href={project.repository_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-primary hover:underline flex items-center font-medium"
                      >
                        查看仓库
                        <ExternalLink className="w-3 h-3 ml-1" />
                      </a>
                    </div>
                  )}

                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">项目类型</span>
                    <Badge variant="outline" className={isRepositoryProject(project) ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-amber-200 bg-amber-50 text-amber-700'}>
                      {getSourceTypeLabel(project.source_type)}
                    </Badge>
                  </div>

                  {isRepositoryProject(project) && (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-muted-foreground">仓库平台</span>
                        <Badge variant="outline" className="border-slate-200 bg-slate-50 text-slate-600">
                          {getRepositoryPlatformLabel(project.repository_type)}
                        </Badge>
                      </div>

                      <div className="flex items-center justify-between">
                        <span className="text-sm text-muted-foreground">默认分支</span>
                        <span className="text-sm font-medium text-foreground bg-muted px-2 py-0.5 rounded border border-border">{project.default_branch}</span>
                      </div>
                    </>
                  )}

                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">创建时间</span>
                    <span className="text-sm text-foreground">{formatDate(project.created_at)}</span>
                  </div>

                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">所有者</span>
                    <span className="text-sm text-foreground">{project.owner?.full_name || project.owner?.phone || '未知'}</span>
                  </div>
                </div>

                {project.programming_languages && (
                  <div className="pt-4 border-t border-border">
                    <h4 className="text-sm font-medium mb-2 text-muted-foreground">支持的编程语言</h4>
                    <div className="flex flex-wrap gap-2">
                      {JSON.parse(project.programming_languages).map((lang: string) => (
                        <Badge key={lang} variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
                          {lang}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </SectionPanel>

            {/* 最近活动 */}
            <SectionPanel title="最近活动">
              <div>
                {unifiedTasks.length > 0 ? (
                  <div className="space-y-2">
                    {unifiedTasks.slice(0, 5).map((t) => (
                      <Link
                        key={`${t.kind}:${t.task.id}`}
                        to={t.kind === 'audit' ? `/tasks/${t.task.id}` : `/agent-audit/${t.task.id}`}
                        className="flex items-center justify-between p-3 bg-muted/50 rounded-lg hover:bg-muted transition-all group"
                      >
                        <div className="flex items-center space-x-3">
                          <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${t.task.status === 'completed' ? 'bg-emerald-50 text-emerald-600' :
                            t.task.status === 'running' ? 'bg-blue-50 text-blue-600' :
                              t.task.status === 'failed' ? 'bg-red-50 text-red-600' :
                                'bg-muted text-muted-foreground'
                            }`}>
                            {getStatusIcon(t.task.status)}
                          </div>
                          <div>
                            <p className="text-sm font-medium text-foreground group-hover:text-primary transition-colors">
                              {t.kind === 'audit'
                                ? ((t.task as AuditTask).task_type === 'repository' ? '审计任务' : '即时分析')
                                : 'Agent 审计'}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {formatDate(t.task.created_at)}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className={t.kind === 'agent' ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-slate-200 bg-slate-50 text-slate-600'}>
                            {t.kind === 'agent' ? 'AGENT' : 'AUDIT'}
                          </Badge>
                          {getStatusBadge(t.task.status)}
                        </div>
                      </Link>
                    ))}
                  </div>
                ) : (
                  <div className="py-12 text-center">
                    <Activity className="w-12 h-12 text-muted-foreground mx-auto mb-3" />
                    <p className="text-muted-foreground">暂无活动记录</p>
                  </div>
                )}
              </div>
            </SectionPanel>
          </div>
        </TabsContent>

        <TabsContent value="tasks" className="flex flex-col gap-6 mt-6">
          <ProjectTasksTab
            unifiedTasks={unifiedTasks}
            onCreateTask={handleCreateTask}
            formatDate={formatDate}
            renderStatusBadge={getStatusBadge}
            renderStatusIcon={getStatusIcon}
          />
        </TabsContent>

        <TabsContent value="issues" className="flex flex-col gap-6 mt-6">
          <ProjectIssuesTab
            hasAnyTasks={auditTasks.length > 0 || agentTasks.length > 0}
            issuesSummary={issuesSummary}
            loading={loadingIssues}
            latestProblems={latestProblems}
            formatDate={formatDate}
          />
        </TabsContent>

        <TabsContent value="settings" className="flex flex-col gap-6 mt-6">
          <SectionPanel title="编辑项目配置">
            <div className="flex flex-col gap-6">
              {/* 基本信息 */}
              <div className="space-y-4">
                <div>
                  <Label htmlFor="edit-name" className="text-xs font-medium text-muted-foreground">项目名称 *</Label>
                  <Input
                    id="edit-name"
                    value={editForm.name}
                    onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                    placeholder="输入项目名称"
                    className="mt-1"
                  />
                </div>

                <div>
                  <Label htmlFor="edit-description" className="text-xs font-medium text-muted-foreground">项目描述</Label>
                  <Textarea
                    id="edit-description"
                    value={editForm.description}
                    onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                    placeholder="输入项目描述"
                    rows={3}
                    className="mt-1 min-h-[80px]"
                  />
                </div>
              </div>

              {/* 仓库信息 - 仅远程仓库类型显示 */}
              {editForm.source_type === 'repository' && (
                <div className="space-y-4 border-t border-border pt-4">
                  <h3 className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                    <GitBranch className="w-4 h-4" />
                    仓库信息
                  </h3>

                  <div>
                    <Label htmlFor="edit-repo-url" className="text-xs font-medium text-muted-foreground">仓库地址</Label>
                    <Input
                      id="edit-repo-url"
                      value={editForm.repository_url}
                      onChange={(e) => setEditForm({ ...editForm, repository_url: e.target.value })}
                      placeholder="https://github.com/username/repo"
                      className="mt-1"
                    />
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <Label htmlFor="edit-repo-type" className="text-xs font-medium text-muted-foreground">仓库平台</Label>
                      <Select
                        value={editForm.repository_type}
                        onValueChange={(value: any) => setEditForm({ ...editForm, repository_type: value })}
                      >
                        <SelectTrigger id="edit-repo-type" className="mt-1">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {REPOSITORY_PLATFORMS.map((platform) => (
                            <SelectItem key={platform.value} value={platform.value}>
                              {platform.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div>
                      <Label htmlFor="edit-branch" className="text-xs font-medium text-muted-foreground">默认分支</Label>
                      <Input
                        id="edit-branch"
                        value={editForm.default_branch}
                        onChange={(e) => setEditForm({ ...editForm, default_branch: e.target.value })}
                        placeholder="main"
                        className="mt-1"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* ZIP项目提示 */}
              {editForm.source_type === 'zip' && (
                <div className="border-t border-border pt-4">
                  <div className="bg-amber-50 border border-amber-200 p-4 rounded-lg">
                    <div className="flex items-start space-x-3">
                      <Upload className="w-5 h-5 text-amber-600 mt-0.5" />
                      <div>
                        <p className="font-medium text-amber-800 mb-1">ZIP上传项目</p>
                        <p className="text-amber-700 text-sm">
                          此项目通过ZIP文件上传创建。每次进行代码审计时，需要在创建任务时重新上传ZIP文件。
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* 编程语言 */}
              <div className="space-y-4 border-t border-border pt-4">
                <h3 className="text-sm font-medium text-muted-foreground">编程语言</h3>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  {supportedLanguages.map((lang) => (
                    <div
                      key={lang}
                      className={`flex items-center space-x-2 p-3 border cursor-pointer transition-all rounded-lg ${editForm.programming_languages?.includes(lang)
                        ? 'border-primary bg-primary/5 text-primary'
                        : 'border-border hover:border-border text-muted-foreground'
                        }`}
                      onClick={() => handleToggleLanguage(lang)}
                    >
                      <div
                        className={`w-4 h-4 border-2 rounded flex items-center justify-center ${editForm.programming_languages?.includes(lang)
                          ? 'bg-primary border-primary'
                          : 'border-border'
                          }`}
                      >
                        {editForm.programming_languages?.includes(lang) && (
                          <CheckCircle className="w-3 h-3 text-primary-foreground" />
                        )}
                      </div>
                      <span className="text-sm font-medium">{lang}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex justify-end space-x-3 pt-6 border-t border-border">
                <Button onClick={handleSaveSettings}>
                  <Edit className="w-4 h-4 mr-2" />
                  保存修改
                </Button>
              </div>
            </div>
          </SectionPanel>
        </TabsContent>
      </Tabs>

      {/* 创建任务对话框 */}
      <CreateTaskDialog
        open={showCreateTaskDialog}
        onOpenChange={setShowCreateTaskDialog}
        onTaskCreated={handleTaskCreated}
        onFastScanStarted={handleFastScanStarted}
        preselectedProjectId={id}
      />

      {/* 终端进度对话框 */}
      <TerminalProgressDialog
        open={showTerminalDialog}
        onOpenChange={setShowTerminalDialog}
        taskId={currentTaskId}
        taskType="repository"
      />
    </div>
  );
}