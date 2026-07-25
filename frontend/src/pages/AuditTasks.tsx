/**
 * Audit Tasks Page
 * 支持普通审计任务和Agent审计任务
 */

import { useState, useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  Activity,
  AlertTriangle,
  CheckCircle,
  Clock,
  Search,
  FileText,
  Calendar,
  Plus,
  XCircle,
  ArrowUpRight,
  Shield,
  Terminal,
  Bot,
  Zap,
  Download,
  Pause,
  Play
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { MetricCard } from "@/components/ui/metric-card";
import { StatusBadge } from "@/components/ui/status-badge";
import { getTaskStatusText } from "@/shared/utils/uiText";
import { api } from "@/shared/config/database";
import { apiClient } from "@/shared/api/serverClient";
import type { AuditTask } from "@/shared/types";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import CreateTaskDialog from "@/components/audit/CreateTaskDialog";
import TerminalProgressDialog from "@/components/audit/TerminalProgressDialog";
import ExportReportDialog from "@/components/reports/ExportReportDialog";
import { calculateTaskProgress } from "@/shared/utils/utils";
import { getAgentTasks, pauseAgentTask, resumeAgentTask, deleteAgentTask, getAgentFindings, type AgentTask, type AgentFinding } from "@/shared/api/agentTasks";
import ReportExportDialog from "@/components/reports/AgentReportExportDialog";

// Zombie task detection config
const ZOMBIE_TIMEOUT = 180000; // 3 minutes without progress is potentially stuck

// 任务类型标签
type TaskTab = "regular" | "agent";

// 说明：paused 属于「已停止但可恢复」的中间态，从活跃集合中移除，允许直接删除。
// 真正处于活跃执行中的状态才禁用删除按钮。
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

export default function AuditTasks() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TaskTab>("agent"); // 默认显示Agent任务

  // 普通任务状态
  const [tasks, setTasks] = useState<AuditTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null);
  const [showTerminal, setShowTerminal] = useState(false);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);

  // Agent任务状态
  const [agentTasks, setAgentTasks] = useState<AgentTask[]>([]);
  const [agentLoading, setAgentLoading] = useState(true);
  const [pausingAgentTaskId, setPausingAgentTaskId] = useState<string | null>(null);
  const [resumingAgentTaskId, setResumingAgentTaskId] = useState<string | null>(null);
  const [exportingTaskId, setExportingTaskId] = useState<string | null>(null);
  const [showExportDialog, setShowExportDialog] = useState(false);
  const [exportTask, setExportTask] = useState<AuditTask | null>(null);
  const [exportIssues, setExportIssues] = useState<any[]>([]);
  // Agent 任务导出对话框状态
  const [showAgentExportDialog, setShowAgentExportDialog] = useState(false);
  const [exportAgentTask, setExportAgentTask] = useState<AgentTask | null>(null);
  const [exportAgentFindings, setExportAgentFindings] = useState<AgentFinding[]>([]);
  const [showDeleteAgentDialog, setShowDeleteAgentDialog] = useState(false);
  const [agentTaskToDelete, setAgentTaskToDelete] = useState<AgentTask | null>(null);
  const [deletingAgentTaskId, setDeletingAgentTaskId] = useState<string | null>(null);
  const [showDeleteTaskDialog, setShowDeleteTaskDialog] = useState(false);
  const [taskToDelete, setTaskToDelete] = useState<AuditTask | null>(null);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);

  // Zombie task detection: track progress and time for each task
  const taskProgressRef = useRef<Map<string, { progress: number; time: number }>>(new Map());

  useEffect(() => {
    loadTasks();
    loadAgentTasks();
  }, []);

  // 加载Agent任务（支持静默更新，不触发 loading 状态）
  const loadAgentTasks = async (silent = false) => {
    try {
      if (!silent) {
        setAgentLoading(true);
      }
      const data = await getAgentTasks();
      setAgentTasks(data);
    } catch (error) {
      console.error('Failed to load agent tasks:', error);
      if (!silent) {
        toast.error("加载Agent任务失败");
      }
    } finally {
      if (!silent) {
        setAgentLoading(false);
      }
    }
  };

  // Silently update active tasks progress (no loading state trigger)
  useEffect(() => {
    const activeTasks = tasks.filter(
      task => task.status === 'running' || task.status === 'pending'
    );

    if (activeTasks.length === 0) {
      taskProgressRef.current.clear();
      return;
    }

    const intervalId = setInterval(async () => {
      try {
        const updatedData = await api.getAuditTasks();

        setTasks(prevTasks => {
          return prevTasks.map(prevTask => {
            const updated = updatedData.find(t => t.id === prevTask.id);
            if (!updated) return prevTask;

            // Zombie task detection
            if (updated.status === 'running') {
              const currentProgress = updated.scanned_files || 0;
              const lastRecord = taskProgressRef.current.get(updated.id);

              if (lastRecord) {
                if (currentProgress !== lastRecord.progress) {
                  taskProgressRef.current.set(updated.id, { progress: currentProgress, time: Date.now() });
                } else if (Date.now() - lastRecord.time > ZOMBIE_TIMEOUT) {
                  toast.warning(`任务 "${updated.project?.name || '未知'}" 可能已停止响应`, {
                    id: `zombie-${updated.id}`,
                    duration: 10000,
                    action: {
                      label: '取消任务',
                      onClick: () => handleCancelTask(updated.id),
                    },
                  });
                  taskProgressRef.current.set(updated.id, { progress: currentProgress, time: Date.now() });
                }
              } else {
                taskProgressRef.current.set(updated.id, { progress: currentProgress, time: Date.now() });
              }
            } else {
              taskProgressRef.current.delete(updated.id);
            }

            if (
              updated.status !== prevTask.status ||
              updated.scanned_files !== prevTask.scanned_files ||
              updated.issues_count !== prevTask.issues_count
            ) {
              return updated;
            }
            return prevTask;
          });
        });
      } catch (error) {
        console.error('静默更新任务列表失败:', error);
        toast.error("获取任务状态失败，请检查网络连接", {
          id: 'network-error',
          duration: 5000,
        });
      }
    }, 3000);

    return () => clearInterval(intervalId);
  }, [tasks.map(t => t.id + t.status).join(',')]);

  // 自动刷新Agent任务（静默更新，不显示 loading）
  useEffect(() => {
    const activeAgentTasks = agentTasks.filter(
      task => task.status === 'running' || task.status === 'pending'
    );

    if (activeAgentTasks.length === 0) return;

    const intervalId = setInterval(() => loadAgentTasks(true), 5000);
    return () => clearInterval(intervalId);
  }, [agentTasks.map(t => t.id + t.status).join(',')]);

  const handleCancelTask = async (taskId: string) => {
    if (cancellingTaskId) return;

    try {
      setCancellingTaskId(taskId);
      await api.cancelAuditTask(taskId);
      toast.success("任务已取消");
      await loadTasks();
    } catch (error: any) {
      console.error('取消任务失败:', error);
      toast.error(error?.response?.data?.detail || "取消任务失败");
    } finally {
      setCancellingTaskId(null);
    }
  };

  const handlePauseAgentTask = async (taskId: string) => {
    if (pausingAgentTaskId || resumingAgentTaskId) return;

    try {
      setPausingAgentTaskId(taskId);
      await pauseAgentTask(taskId);
      toast.success("Agent任务已暂停");
      await loadAgentTasks(false);
    } catch (error: any) {
      console.error('暂停Agent任务失败:', error);
      toast.error(error?.response?.data?.detail || "暂停Agent任务失败");
    } finally {
      setPausingAgentTaskId(null);
    }
  };

  const handleResumeAgentTask = async (taskId: string) => {
    if (resumingAgentTaskId || pausingAgentTaskId) return;

    try {
      setResumingAgentTaskId(taskId);
      await resumeAgentTask(taskId);
      toast.success("Agent任务已继续");
      await loadAgentTasks(false);
    } catch (error: any) {
      console.error('继续Agent任务失败:', error);
      toast.error(error?.response?.data?.detail || "继续Agent任务失败");
    } finally {
      setResumingAgentTaskId(null);
    }
  };

  const handleReferenceAgentTaskToAI = (task: AgentTask) => {
    navigate(`/ai?taskId=${encodeURIComponent(task.id)}&kind=agent`);
  };

  const handleReferenceTaskToAI = (task: AuditTask) => {
    navigate(`/ai?taskId=${encodeURIComponent(task.id)}&kind=regular`);
  };

  const handleDeleteAgentTask = async () => {
    if (!agentTaskToDelete || deletingAgentTaskId) return;

    try {
      setDeletingAgentTaskId(agentTaskToDelete.id);
      await deleteAgentTask(agentTaskToDelete.id);
      toast.success("Agent任务已删除");
      setShowDeleteAgentDialog(false);
      setAgentTaskToDelete(null);
      await loadAgentTasks(false);
    } catch (error: any) {
      console.error('删除Agent任务失败:', error);
      toast.error(error?.response?.data?.detail || "删除Agent任务失败");
    } finally {
      setDeletingAgentTaskId(null);
    }
  };

  const handleDeleteRegularTask = async () => {
    if (!taskToDelete || deletingTaskId) return;

    try {
      setDeletingTaskId(taskToDelete.id);
      await api.deleteAuditTask(taskToDelete.id);
      toast.success("审计任务已删除");
      setShowDeleteTaskDialog(false);
      setTaskToDelete(null);
      await loadTasks();
    } catch (error: any) {
      console.error('删除审计任务失败:', error);
      toast.error(error?.response?.data?.detail || "删除审计任务失败");
    } finally {
      setDeletingTaskId(null);
    }
  };

  const isActiveAgentTask = (task: AgentTask) => ACTIVE_AGENT_TASK_STATUSES.has(task.status);
  const isActiveRegularTask = (task: AuditTask) => task.status === 'running' || task.status === 'pending';

  // 打开快速扫描任务导出对话框
  const handleOpenExportDialog = async (task: AuditTask) => {
    try {
      setExportingTaskId(task.id);
      // 获取任务的问题列表
      const issuesResponse = await apiClient.get(`/tasks/${task.id}/issues`);
      setExportTask(task);
      setExportIssues(issuesResponse.data || []);
      setShowExportDialog(true);
    } catch (error: any) {
      console.error('获取问题列表失败:', error);
      toast.error("获取问题列表失败");
    } finally {
      setExportingTaskId(null);
    }
  };

  // 打开 Agent 任务导出对话框
  const handleOpenAgentExportDialog = async (task: AgentTask) => {
    try {
      setExportingTaskId(task.id);
      // 获取任务的 findings 列表
      const findings = await getAgentFindings(task.id);
      setExportAgentTask(task);
      setExportAgentFindings(findings);
      setShowAgentExportDialog(true);
    } catch (error: any) {
      console.error('获取 findings 列表失败:', error);
      toast.error("获取审计结果失败");
    } finally {
      setExportingTaskId(null);
    }
  };

  const loadTasks = async () => {
    try {
      setLoading(true);
      const data = await api.getAuditTasks();
      setTasks(data);
    } catch (error) {
      console.error('Failed to load tasks:', error);
      toast.error("加载任务失败");
    } finally {
      setLoading(false);
    }
  };

  const handleFastScanStarted = (taskId: string) => {
    setCurrentTaskId(taskId);
    setShowTerminal(true);
  };

  const getStatusBadge = (status: string) => {
    return <StatusBadge value={status} label={getTaskStatusText(status)} type="status" />;
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle className="w-4 h-4 text-emerald-400" />;
      case 'running': return <Activity className="w-4 h-4 text-sky-400" />;
      case 'paused': return <Pause className="w-4 h-4 text-amber-500" />;
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

  const filteredTasks = tasks.filter(task => {
    const matchesSearch = task.project?.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      task.task_type.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === "all" || task.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  const filteredAgentTasks = agentTasks.filter(task => {
    const matchesSearch = (task.name || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
      task.task_type.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === "all" || task.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  // 统计数据
  const regularStats = {
    total: tasks.length,
    completed: tasks.filter(t => t.status === 'completed').length,
    running: tasks.filter(t => t.status === 'running').length,
    failed: tasks.filter(t => t.status === 'failed').length,
  };

  const agentStats = {
    total: agentTasks.length,
    completed: agentTasks.filter(t => t.status === 'completed').length,
    running: agentTasks.filter(t => t.status === 'running').length,
    failed: agentTasks.filter(t => t.status === 'failed').length,
  };

  const currentStats = activeTab === "agent" ? agentStats : regularStats;

  if ((activeTab === "regular" && loading) || (activeTab === "agent" && agentLoading)) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center space-y-4">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-muted-foreground text-sm">加载任务数据...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6 bg-background min-h-screen">

      <PageHeader
        eyebrow="审计任务"
        title="审计任务"
        actions={
          activeTab === "agent" ? (
            <Button onClick={() => navigate("/")}>
              <Bot className="w-4 h-4 mr-2" />
              新建Agent审计
            </Button>
          ) : (
            <Button onClick={() => setShowCreateDialog(true)}>
              <Plus className="w-4 h-4 mr-2" />
              新建任务
            </Button>
          )
        }
      />

      {/* Tab 切换 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <button
          onClick={() => setActiveTab("agent")}
          className={`text-left p-5 rounded-xl border-2 transition-all ${activeTab === "agent"
              ? "border-primary bg-primary/5"
              : "border-border bg-card hover:border-primary/50"
            }`}
        >
          <div className="flex items-start gap-4">
            <div className={`flex-shrink-0 w-12 h-12 rounded-xl flex items-center justify-center ${activeTab === "agent" ? "bg-primary/10" : "bg-muted"}`}>
              <Bot className={`w-6 h-6 ${activeTab === "agent" ? "text-primary" : "text-muted-foreground"}`} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <h3 className={`text-lg font-semibold ${activeTab === "agent" ? "text-foreground" : "text-foreground"}`}>
                  Agent 智能审计
                </h3>
                {agentStats.running > 0 && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-blue-100 text-blue-700 border border-blue-200">
                    {agentStats.running} 运行中
                  </span>
                )}
                {activeTab === "agent" && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-primary text-primary-foreground">
                    当前
                  </span>
                )}
              </div>
              <p className="text-sm text-muted-foreground">
                LLM 驱动的多 Agent 协同深度审计，支持智能漏洞挖掘与验证
              </p>
              <div className="flex items-center gap-4 mt-3 text-xs">
                <span className="text-muted-foreground">
                  共 <span className="font-semibold text-foreground">{agentStats.total}</span> 个任务
                </span>
                <span className="text-emerald-600">
                  <CheckCircle className="w-3 h-3 inline mr-1" />
                  {agentStats.completed}
                </span>
                {agentStats.failed > 0 && (
                  <span className="text-red-500">
                    <AlertTriangle className="w-3 h-3 inline mr-1" />
                    {agentStats.failed}
                  </span>
                )}
              </div>
            </div>
          </div>
        </button>

        <button
          onClick={() => setActiveTab("regular")}
          className={`text-left p-5 rounded-xl border-2 transition-all ${activeTab === "regular"
              ? "border-primary bg-primary/5"
              : "border-border bg-card hover:border-primary/50"
            }`}
        >
          <div className="flex items-start gap-4">
            <div className={`flex-shrink-0 w-12 h-12 rounded-xl flex items-center justify-center ${activeTab === "regular" ? "bg-primary/10" : "bg-muted"}`}>
              <Zap className={`w-6 h-6 ${activeTab === "regular" ? "text-primary" : "text-muted-foreground"}`} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <h3 className="text-lg font-semibold text-foreground">
                  快速扫描任务
                </h3>
                {regularStats.running > 0 && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-blue-100 text-blue-700 border border-blue-200">
                    {regularStats.running} 运行中
                  </span>
                )}
                {activeTab === "regular" && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-primary text-primary-foreground">
                    当前
                  </span>
                )}
              </div>
              <p className="text-sm text-muted-foreground">
                传统规则引擎驱动的快速代码扫描，适合大规模批量检测
              </p>
              <div className="flex items-center gap-4 mt-3 text-xs">
                <span className="text-muted-foreground">
                  共 <span className="font-semibold text-foreground">{regularStats.total}</span> 个任务
                </span>
                <span className="text-emerald-600">
                  <CheckCircle className="w-3 h-3 inline mr-1" />
                  {regularStats.completed}
                </span>
                {regularStats.failed > 0 && (
                  <span className="text-red-500">
                    <AlertTriangle className="w-3 h-3 inline mr-1" />
                    {regularStats.failed}
                  </span>
                )}
              </div>
            </div>
          </div>
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard label="总任务数" value={currentStats.total} icon={<Activity className="w-5 h-5" />} tone="blue" />
        <MetricCard label="已完成" value={currentStats.completed} icon={<CheckCircle className="w-5 h-5" />} tone="green" />
        <MetricCard label="运行中" value={currentStats.running} icon={<Clock className="w-5 h-5" />} tone="blue" />
        <MetricCard label="失败" value={currentStats.failed} icon={<AlertTriangle className="w-5 h-5" />} tone="red" />
      </div>

      {/* Search and Filter */}
      <div className="rounded-xl border border-border bg-card p-4 shadow-card">
        <div className="flex flex-col md:flex-row items-center gap-4">
          <div className="flex-1 relative w-full">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-muted-foreground w-4 h-4 z-10" />
            <Input
              placeholder={activeTab === "agent" ? "搜索Agent任务名称..." : "搜索项目名称或任务类型..."}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="!pl-10"
            />
          </div>
          <div className="flex gap-2 w-full md:w-auto overflow-x-auto pb-2 md:pb-0">
            <Button
              size="sm"
              variant={statusFilter === "all" ? "default" : "outline"}
              onClick={() => setStatusFilter("all")}
            >
              全部
            </Button>
            <Button
              size="sm"
              variant={statusFilter === "running" ? "default" : "outline"}
              onClick={() => setStatusFilter("running")}
            >
              运行中
            </Button>
            <Button
              size="sm"
              variant={statusFilter === "completed" ? "default" : "outline"}
              onClick={() => setStatusFilter("completed")}
            >
              已完成
            </Button>
            <Button
              size="sm"
              variant={statusFilter === "paused" ? "default" : "outline"}
              onClick={() => setStatusFilter("paused")}
            >
              已暂停
            </Button>
            <Button
              size="sm"
              variant={statusFilter === "failed" ? "default" : "outline"}
              onClick={() => setStatusFilter("failed")}
            >
              失败
            </Button>
          </div>
        </div>
      </div>

      {/* Agent Task List */}
      {activeTab === "agent" && (
        <>
          {filteredAgentTasks.length > 0 ? (
            <div className="space-y-4">
              {filteredAgentTasks.map((task) => (
                <div key={task.id} className="rounded-xl border border-border bg-card p-6 shadow-card">
                  {/* Task Header */}
                  <div className="flex items-center justify-between mb-4 pb-4 border-b border-border">
                    <div className="flex items-center space-x-4">
                      <div className={`w-12 h-12 rounded-lg flex items-center justify-center ${task.status === 'completed' ? 'bg-emerald-50 text-emerald-600' :
                        task.status === 'running' ? 'bg-blue-50 text-blue-600' :
                        task.status === 'paused' ? 'bg-amber-50 text-amber-600' :
                          task.status === 'failed' ? 'bg-red-50 text-red-600' :
                            'bg-muted text-muted-foreground'
                        }`}>
                        <Bot className="w-6 h-6" />
                      </div>
                      <div>
                        <h3 className="font-semibold text-xl text-foreground max-w-[200px] truncate" title={task.name || 'Agent审计任务'}>
                          {task.name || 'Agent审计任务'}
                        </h3>
                        <p className="text-sm text-muted-foreground">
                          {task.current_phase || task.task_type}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      {getStatusBadge(task.status)}
                      {task.status === 'running' && (
                        <div className="flex items-center gap-1.5">
                          <span className="relative flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                          </span>
                        </div>
                      )}
                      {task.status === 'paused' && (
                        <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                          已暂停
                        </Badge>
                      )}
                    </div>
                  </div>

                  {/* Stats Grid */}
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-foreground">{task.total_files}</p>
                      <p className="text-xs text-muted-foreground">文件数</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-foreground">{task.analyzed_files}</p>
                      <p className="text-xs text-muted-foreground">已分析</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-amber-600">{task.findings_count}</p>
                      <p className="text-xs text-muted-foreground">发现问题</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-blue-600">{task.tool_calls_count || 0}</p>
                      <p className="text-xs text-muted-foreground">工具调用</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-primary">{task.security_score?.toFixed(1) || '-'}</p>
                      <p className="text-xs text-muted-foreground">安全评分</p>
                    </div>
                  </div>

                  {/* Severity Distribution */}
                  {task.findings_count > 0 && (
                    <div className="flex gap-4 mb-4 text-xs">
                      {task.critical_count > 0 && (
                        <span className="text-red-500 font-medium">Critical: {task.critical_count}</span>
                      )}
                      {task.high_count > 0 && (
                        <span className="text-orange-500 font-medium">High: {task.high_count}</span>
                      )}
                      {task.medium_count > 0 && (
                        <span className="text-amber-500 font-medium">Medium: {task.medium_count}</span>
                      )}
                      {task.low_count > 0 && (
                        <span className="text-blue-500 font-medium">Low: {task.low_count}</span>
                      )}
                    </div>
                  )}

                  {/* Progress Bar */}
                  <div className="mb-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-muted-foreground">审计进度</span>
                      <span className="text-sm text-muted-foreground">
                        {task.analyzed_files || 0} / {task.total_files || 0} 文件
                      </span>
                    </div>
                    <Progress
                      value={task.progress_percentage || 0}
                      className="h-2 bg-muted [&>div]:bg-primary"
                    />
                    <div className="text-right mt-1">
                      <span className="text-xs text-muted-foreground">
                        {(task.progress_percentage || 0).toFixed(0)}% 完成
                      </span>
                    </div>
                  </div>

                  {/* Task Footer */}
                  <div className="flex items-center justify-between pt-4 border-t border-border">
                    <div className="flex items-center space-x-6 text-sm text-muted-foreground">
                      <div className="flex items-center">
                        <Calendar className="w-4 h-4 mr-2" />
                        {formatDate(task.created_at)}
                      </div>
                      {task.completed_at && (
                        <div className="flex items-center">
                          <CheckCircle className="w-4 h-4 mr-2" />
                          {formatDate(task.completed_at)}
                        </div>
                      )}
                      {task.tokens_used > 0 && (
                        <div className="flex items-center text-muted-foreground">
                          <span>{task.tokens_used.toLocaleString()} tokens</span>
                        </div>
                      )}
                    </div>

                    <div className="flex gap-3">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-9"
                        onClick={() => handleReferenceAgentTaskToAI(task)}
                      >
                        <Bot className="w-4 h-4 mr-2" />
                        引用到AI
                      </Button>
                      {(task.status === 'running' || task.status === 'pending') && (
                        <Link to={`/agent-audit/${task.id}`}>
                          <Button size="sm" variant="default" className="h-9">
                            <Terminal className="w-4 h-4 mr-2" />
                            查看实时流
                          </Button>
                        </Link>
                      )}
                      {task.status === 'paused' && (
                        <Button
                          size="sm"
                          className="h-9"
                          onClick={() => handleResumeAgentTask(task.id)}
                          disabled={resumingAgentTaskId === task.id}
                        >
                          <Play className="w-4 h-4 mr-2" />
                          {resumingAgentTaskId === task.id ? '继续中...' : '继续'}
                        </Button>
                      )}
                      {task.status === 'running' && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-9 border-amber-200 text-amber-700 hover:bg-amber-50"
                          onClick={() => handlePauseAgentTask(task.id)}
                          disabled={pausingAgentTaskId === task.id}
                        >
                          <Pause className="w-4 h-4 mr-2" />
                          {pausingAgentTaskId === task.id ? '暂停中...' : '暂停'}
                        </Button>
                      )}
                      {(task.status === 'completed' || (task.findings_count != null && task.findings_count > 0)) && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-9"
                          onClick={() => handleOpenAgentExportDialog(task)}
                          disabled={exportingTaskId === task.id}
                        >
                          <Download className="w-4 h-4 mr-2" />
                          {exportingTaskId === task.id ? '加载中...' : '导出报告'}
                        </Button>
                      )}
                      <Link to={`/agent-audit/${task.id}`}>
                        <Button size="sm" variant="outline" className="h-9">
                          <FileText className="w-4 h-4 mr-2" />
                          查看详情
                        </Button>
                      </Link>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-9 border-red-200 text-red-600 hover:bg-red-50"
                        onClick={() => {
                          setAgentTaskToDelete(task);
                          setShowDeleteAgentDialog(true);
                        }}
                        disabled={isActiveAgentTask(task)}
                      >
                        <XCircle className="w-4 h-4 mr-2" />
                        删除
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-border bg-card p-16 text-center shadow-card">
              <Bot className="w-16 h-16 text-muted-foreground mx-auto mb-4" />
              <h3 className="text-xl font-semibold text-foreground mb-2">
                {searchTerm || statusFilter !== "all" ? '未找到匹配的Agent任务' : '暂无Agent审计任务'}
              </h3>
              <p className="text-muted-foreground mb-6">
                {searchTerm || statusFilter !== "all" ? '尝试调整搜索条件或筛选器' : '创建第一个Agent审计任务开始智能安全审计'}
              </p>
              {!searchTerm && statusFilter === "all" && (
                <Button onClick={() => navigate("/")}>
                  <Bot className="w-4 h-4 mr-2" />
                  创建Agent审计
                </Button>
              )}
            </div>
          )}
        </>
      )}

      {/* Regular Task List */}
      {activeTab === "regular" && (
        <>
          {filteredTasks.length > 0 ? (
            <div className="space-y-4">
              {filteredTasks.map((task) => (
                <div key={task.id} className="rounded-xl border border-border bg-card p-6 shadow-card">
                  {/* Task Header */}
                  <div className="flex items-center justify-between mb-4 pb-4 border-b border-border">
                    <div className="flex items-center space-x-4">
                      <div className={`w-12 h-12 rounded-lg flex items-center justify-center ${task.status === 'completed' ? 'bg-emerald-50 text-emerald-600' :
                        task.status === 'running' ? 'bg-blue-50 text-blue-600' :
                          task.status === 'failed' ? 'bg-red-50 text-red-600' :
                            'bg-muted text-muted-foreground'
                        }`}>
                        {getStatusIcon(task.status)}
                      </div>
                      <div>
                        <h3 className="font-semibold text-xl text-foreground max-w-[200px] truncate" title={task.project?.name || '未知项目'}>
                          {task.project?.name || '未知项目'}
                        </h3>
                        <p className="text-sm text-muted-foreground">
                          {task.task_type === 'repository' ? '仓库审计任务' : '即时分析任务'}
                        </p>
                      </div>
                    </div>
                    {getStatusBadge(task.status)}
                  </div>

                  {/* Stats Grid */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-foreground">{task.total_files}</p>
                      <p className="text-xs text-muted-foreground">文件数</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-foreground">{task.total_lines.toLocaleString()}</p>
                      <p className="text-xs text-muted-foreground">代码行数</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-amber-600">{task.issues_count}</p>
                      <p className="text-xs text-muted-foreground">发现问题</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded-lg border border-border">
                      <p className="text-2xl font-semibold text-primary">{task.quality_score.toFixed(1)}</p>
                      <p className="text-xs text-muted-foreground">质量评分</p>
                    </div>
                  </div>

                  {/* Progress Bar */}
                  <div className="mb-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-muted-foreground">扫描进度</span>
                      <span className="text-sm text-muted-foreground">
                        {task.scanned_files || 0} / {task.total_files || 0} 文件
                      </span>
                    </div>
                    <Progress
                      value={calculateTaskProgress(task.scanned_files, task.total_files)}
                      className="h-2 bg-muted [&>div]:bg-primary"
                    />
                    <div className="text-right mt-1">
                      <span className="text-xs text-muted-foreground">
                        {calculateTaskProgress(task.scanned_files, task.total_files)}% 完成
                      </span>
                    </div>
                  </div>

                  {/* Task Footer */}
                  <div className="flex items-center justify-between pt-4 border-t border-border">
                    <div className="flex items-center space-x-6 text-sm text-muted-foreground">
                      <div className="flex items-center">
                        <Calendar className="w-4 h-4 mr-2" />
                        {formatDate(task.created_at)}
                      </div>
                      {task.completed_at && (
                        <div className="flex items-center">
                          <CheckCircle className="w-4 h-4 mr-2" />
                          {formatDate(task.completed_at)}
                        </div>
                      )}
                    </div>

                    <div className="flex gap-3">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-9"
                        onClick={() => handleReferenceTaskToAI(task)}
                      >
                        <Bot className="w-4 h-4 mr-2" />
                        引用到AI
                      </Button>
                      {(task.status === 'running' || task.status === 'pending') && (
                        <Button
                          size="sm"
                          variant="destructive"
                          className="h-9"
                          onClick={() => handleCancelTask(task.id)}
                          disabled={cancellingTaskId === task.id}
                        >
                          <XCircle className="w-4 h-4 mr-2" />
                          {cancellingTaskId === task.id ? '取消中...' : '取消'}
                        </Button>
                      )}
                      {(task.issues_count > 0 || task.status === 'completed') && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-9"
                          onClick={() => handleOpenExportDialog(task)}
                          disabled={exportingTaskId === task.id}
                        >
                          <Download className="w-4 h-4 mr-2" />
                          {exportingTaskId === task.id ? '加载中...' : '导出报告'}
                        </Button>
                      )}
                      <Link to={`/tasks/${task.id}`}>
                        <Button size="sm" variant="outline" className="h-9">
                          <FileText className="w-4 h-4 mr-2" />
                          查看详情
                        </Button>
                      </Link>
                      {task.project && (
                        <Link to={`/projects/${task.project.id}`}>
                          <Button size="sm" className="h-9">
                            查看项目
                            <ArrowUpRight className="w-3 h-3 ml-2" />
                          </Button>
                        </Link>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-9 border-red-200 text-red-600 hover:bg-red-50"
                        onClick={() => {
                          setTaskToDelete(task);
                          setShowDeleteTaskDialog(true);
                        }}
                        disabled={isActiveRegularTask(task)}
                      >
                        <XCircle className="w-4 h-4 mr-2" />
                        删除
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-border bg-card p-16 text-center shadow-card">
              <Activity className="w-16 h-16 text-muted-foreground mx-auto mb-4" />
              <h3 className="text-xl font-semibold text-foreground mb-2">
                {searchTerm || statusFilter !== "all" ? '未找到匹配的任务' : '暂无审计任务'}
              </h3>
              <p className="text-muted-foreground mb-6">
                {searchTerm || statusFilter !== "all" ? '尝试调整搜索条件或筛选器' : '创建第一个审计任务开始代码质量分析'}
              </p>
              {!searchTerm && statusFilter === "all" && (
                <Button onClick={() => setShowCreateDialog(true)}>
                  <Plus className="w-4 h-4 mr-2" />
                  创建任务
                </Button>
              )}
            </div>
          )}
        </>
      )}

      {/* Create Task Dialog */}
      <CreateTaskDialog
        open={showCreateDialog}
        onOpenChange={setShowCreateDialog}
        onTaskCreated={loadTasks}
        onFastScanStarted={handleFastScanStarted}
      />

      {/* Terminal Progress Dialog for Fast Scan */}
      <TerminalProgressDialog
        open={showTerminal}
        onOpenChange={setShowTerminal}
        taskId={currentTaskId}
        taskType="repository"
      />

      {/* 快速扫描任务导出对话框 */}
      {exportTask && (
        <ExportReportDialog
          open={showExportDialog}
          onOpenChange={setShowExportDialog}
          task={exportTask}
          issues={exportIssues}
        />
      )}

      {/* Agent 任务导出对话框 */}
      {exportAgentTask && (
        <ReportExportDialog
          open={showAgentExportDialog}
          onOpenChange={setShowAgentExportDialog}
          task={exportAgentTask}
          findings={exportAgentFindings}
        />
      )}

      <AlertDialog open={showDeleteAgentDialog} onOpenChange={setShowDeleteAgentDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除 Agent 审计任务</AlertDialogTitle>
            <AlertDialogDescription>
              {agentTaskToDelete
                ? `你将永久删除任务「${agentTaskToDelete.name || '未命名任务'}」以及其关联事件、漏洞、检查点和树节点。运行中的任务不能直接删除。`
                : '确认删除该 Agent 审计任务？'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void handleDeleteAgentTask()} disabled={!!deletingAgentTaskId}>
              {deletingAgentTaskId ? '删除中...' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={showDeleteTaskDialog} onOpenChange={setShowDeleteTaskDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除审计任务</AlertDialogTitle>
            <AlertDialogDescription>
              {taskToDelete
                ? `你将永久删除该审计任务及其关联问题记录。运行中的任务不能直接删除，请先取消任务。`
                : '确认删除该审计任务？'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void handleDeleteRegularTask()} disabled={!!deletingTaskId}>
              {deletingTaskId ? '删除中...' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
