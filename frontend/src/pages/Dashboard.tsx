/**
 * Dashboard Page
 * Enterprise Blue-White Design
 */

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/layout/PageHeader";
import { MetricCard } from "@/components/ui/metric-card";
import { SectionPanel } from "@/components/ui/section-panel";
import { StatusBadge } from "@/components/ui/status-badge";
import { getTaskStatusText } from "@/shared/utils/uiText";
import {
  LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from "recharts";
import {
  Activity, AlertTriangle, Clock, Code,
  GitBranch, Shield, TrendingUp, Zap,
  BarChart3, ArrowUpRight, Calendar,
  MessageSquare, Bot
} from "lucide-react";
import { api, dbMode, isDemoMode } from "@/shared/config/database";
import type { Project, AuditTask, ProjectStats } from "@/shared/types";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { getRuleSets } from "@/shared/api/rules";
import { getPromptTemplates } from "@/shared/api/prompts";

export default function Dashboard() {
  const [stats, setStats] = useState<ProjectStats | null>(null);
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [recentTasks, setRecentTasks] = useState<AuditTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [issueTypeData, setIssueTypeData] = useState<Array<{ name: string; value: number; color: string }>>([]);
  const [qualityTrendData, setQualityTrendData] = useState<Array<{ date: string; score: number }>>([]);
  const [ruleStats, setRuleStats] = useState({ total: 0, enabled: 0 });
  const [templateStats, setTemplateStats] = useState({ total: 0, active: 0 });

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    try {
      setLoading(true);

      const results = await Promise.allSettled([
        api.getProjectStats(),
        api.getProjects(),
        api.getAuditTasks()
      ]);

      if (results[0].status === 'fulfilled') {
        setStats(results[0].value);
      } else {
        setStats({
          total_projects: 0,
          active_projects: 0,
          total_tasks: 0,
          completed_tasks: 0,
          total_issues: 0,
          resolved_issues: 0,
          avg_quality_score: 0
        });
      }

      if (results[1].status === 'fulfilled') {
        setRecentProjects(Array.isArray(results[1].value) ? results[1].value.slice(0, 6) : []);
      } else {
        setRecentProjects([]);
      }

      let tasks: AuditTask[] = [];
      if (results[2].status === 'fulfilled') {
        tasks = Array.isArray(results[2].value) ? results[2].value : [];
        setRecentTasks(tasks.slice(0, 10));
      } else {
        setRecentTasks([]);
      }

      if (tasks.length > 0) {
        const tasksByDate = tasks
          .filter(t => t.completed_at && t.quality_score > 0)
          .sort((a, b) => new Date(a.completed_at!).getTime() - new Date(b.completed_at!).getTime())
          .slice(-6);

        const trendData = tasksByDate.map((task) => ({
          date: new Date(task.completed_at!).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }),
          score: task.quality_score
        }));

        setQualityTrendData(trendData.length > 0 ? trendData : []);

        const validScores = tasks
          .filter(t => t.status === 'completed' && t.quality_score > 0)
          .map(t => t.quality_score);
        if (validScores.length > 0) {
          const avg = validScores.reduce((a, b) => a + b, 0) / validScores.length;
          setStats(prev => ({ ...prev!, avg_quality_score: Math.round(avg * 100) / 100 }));
        }
      } else {
        setQualityTrendData([]);
      }

      try {
        const allIssues = await Promise.all(
          tasks.map(task => api.getAuditIssues(task.id).catch(() => []))
        );
        const flatIssues = allIssues.flat();

        if (flatIssues.length > 0) {
          const typeCount: Record<string, number> = {};
          flatIssues.forEach(issue => {
            typeCount[issue.issue_type] = (typeCount[issue.issue_type] || 0) + 1;
          });

          const typeMap: Record<string, { name: string; color: string }> = {
            security: { name: '安全问题', color: '#f43f5e' },
            bug: { name: '潜在Bug', color: '#f97316' },
            performance: { name: '性能问题', color: '#eab308' },
            style: { name: '代码风格', color: '#3b82f6' },
            maintainability: { name: '可维护性', color: '#8b5cf6' }
          };

          const issueData = Object.entries(typeCount).map(([type, count]) => ({
            name: typeMap[type]?.name || type,
            value: count,
            color: typeMap[type]?.color || '#6b7280'
          }));

          setIssueTypeData(issueData);
        } else {
          setIssueTypeData([]);
        }
      } catch (error) {
        setIssueTypeData([]);
      }

      try {
        const [rulesRes, promptsRes] = await Promise.all([
          getRuleSets(),
          getPromptTemplates(),
        ]);
        const totalRules = rulesRes.items.reduce((acc, rs) => acc + rs.rules_count, 0);
        const enabledRules = rulesRes.items.reduce((acc, rs) => acc + rs.enabled_rules_count, 0);
        setRuleStats({ total: totalRules, enabled: enabledRules });
        setTemplateStats({
          total: promptsRes.items.length,
          active: promptsRes.items.filter(t => t.is_active).length
        });
      } catch (error) {
        console.error('获取规则和模板统计失败:', error);
      }
    } catch (error) {
      console.error('仪表盘数据加载失败:', error);
      toast.error("数据加载失败");
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="rounded-xl border border-border bg-card px-6 py-5 text-center shadow-card">
          <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-sm text-muted-foreground">正在加载仪表盘数据...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="安全态势"
        title="仪表盘"
        description="集中查看项目、审计任务、漏洞问题和质量趋势。"
        actions={
          <Button asChild>
            <Link to="/projects">创建项目</Link>
          </Button>
        }
      />

      {isDemoMode && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
            <div className="text-sm text-amber-800">
              当前使用<span className="font-semibold">演示模式</span>，显示的是模拟数据。
              <Link to="/admin" className="ml-2 font-medium underline underline-offset-2">
                前往系统管理 →
              </Link>
            </div>
          </div>
        </div>
      )}

      <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="总项目数"
          value={stats?.total_projects || 0}
          description={`活跃: ${stats?.active_projects || 0}`}
          icon={<Code className="h-5 w-5" />}
          tone="blue"
        />
        <MetricCard
          label="审计任务"
          value={stats?.total_tasks || 0}
          description={`已完成: ${stats?.completed_tasks || 0}`}
          icon={<Activity className="h-5 w-5" />}
          tone="green"
        />
        <MetricCard
          label="发现问题"
          value={stats?.total_issues || 0}
          description={`已解决: ${stats?.resolved_issues || 0}`}
          icon={<AlertTriangle className="h-5 w-5" />}
          tone="amber"
        />
        <MetricCard
          label="平均质量分"
          value={stats?.avg_quality_score ? stats.avg_quality_score.toFixed(1) : '0.0'}
          description={stats?.avg_quality_score ? '持续改进' : '暂无数据'}
          icon={<TrendingUp className="h-5 w-5" />}
          tone="blue"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <div className="xl:col-span-3 space-y-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <SectionPanel title="质量趋势">
              {qualityTrendData.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={qualityTrendData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                    <XAxis dataKey="date" stroke="hsl(var(--muted-foreground))" fontSize={11} />
                    <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} domain={[0, 100]} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'hsl(var(--card))',
                        border: '1px solid hsl(var(--border))',
                        borderRadius: '4px',
                        fontSize: '12px',
                        color: 'hsl(var(--foreground))'
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="score"
                      stroke="hsl(var(--primary))"
                      strokeWidth={2}
                      dot={{ fill: 'hsl(var(--primary))', stroke: 'hsl(var(--card))', strokeWidth: 2, r: 4 }}
                      activeDot={{ r: 6, fill: 'hsl(var(--primary))' }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex flex-col items-center justify-center h-[220px] text-muted-foreground">
                  <TrendingUp className="w-8 h-8 mb-3 opacity-40" />
                  <p className="text-sm">暂无质量趋势数据</p>
                </div>
              )}
            </SectionPanel>

            <SectionPanel title="问题类型分布">
              {issueTypeData.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie
                      data={issueTypeData}
                      cx="50%"
                      cy="50%"
                      labelLine={false}
                      label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                      outerRadius={70}
                      dataKey="value"
                      stroke="hsl(var(--card))"
                      strokeWidth={2}
                    >
                      {issueTypeData.map((entry) => (
                        <Cell key={`cell-${entry.name}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'hsl(var(--card))',
                        border: '1px solid hsl(var(--border))',
                        borderRadius: '4px',
                        fontSize: '12px',
                        color: 'hsl(var(--foreground))'
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex flex-col items-center justify-center h-[220px] text-muted-foreground">
                  <BarChart3 className="w-8 h-8 mb-3 opacity-40" />
                  <p className="text-sm">暂无问题分布数据</p>
                </div>
              )}
            </SectionPanel>
          </div>

          <SectionPanel title="最近项目">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {recentProjects.length > 0 ? (
                recentProjects.map((project) => (
                  <Link
                    key={project.id}
                    to={`/projects/${project.id}`}
                    className="block rounded-lg border border-border bg-muted/30 p-4 transition-all hover:bg-accent hover:border-primary/30 group"
                  >
                    <div className="flex items-start justify-between mb-2">
                      <h4 className="font-semibold text-foreground group-hover:text-primary transition-colors truncate">
                        {project.name}
                      </h4>
                      <Badge
                        variant="outline"
                        className={`ml-2 shrink-0 ${project.is_active ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-slate-50 text-slate-600'}`}
                      >
                        {project.is_active ? '活跃' : '暂停'}
                      </Badge>
                    </div>
                    <p className="text-sm text-muted-foreground line-clamp-2 mb-3">
                      {project.description || '暂无描述'}
                    </p>
                    <div className="flex items-center text-sm text-muted-foreground">
                      <Calendar className="w-4 h-4 mr-1" />
                      {new Date(project.created_at).toLocaleDateString('zh-CN')}
                    </div>
                  </Link>
                ))
              ) : (
                <div className="col-span-full flex flex-col items-center justify-center py-12 text-muted-foreground">
                  <Code className="w-10 h-10 mb-3 opacity-40" />
                  <p className="text-base font-medium">暂无项目</p>
                  <p className="text-sm">创建您的第一个项目开始审计</p>
                </div>
              )}
            </div>
          </SectionPanel>

          <SectionPanel
            title="最近审计任务"
            actions={
              <Button variant="ghost" size="sm" asChild>
                <Link to="/audit-tasks">
                  查看全部 <ArrowUpRight className="ml-1 h-3 w-3" />
                </Link>
              </Button>
            }
          >
            <div className="space-y-2">
              {recentTasks.length > 0 ? (
                recentTasks.slice(0, 6).map((task) => (
                  <Link
                    key={task.id}
                    to={`/tasks/${task.id}`}
                    className="flex items-center justify-between rounded-lg bg-muted/30 p-3 transition-all hover:bg-accent group"
                  >
                    <div className="flex items-center gap-3">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${
                        task.status === 'completed' ? 'bg-emerald-50 text-emerald-600' :
                        task.status === 'running' ? 'bg-blue-50 text-blue-600' :
                        task.status === 'failed' ? 'bg-red-50 text-red-600' :
                        'bg-slate-100 text-slate-500'
                      }`}>
                        {task.status === 'completed' ? <Activity className="w-4 h-4" /> :
                         task.status === 'running' ? <Clock className="w-4 h-4" /> :
                         <AlertTriangle className="w-4 h-4" />}
                      </div>
                      <div>
                        <p className="text-base font-medium text-foreground group-hover:text-primary transition-colors">
                          {task.project?.name || '未知项目'}
                        </p>
                        <p className="text-sm text-muted-foreground">
                          质量分: <span className="text-foreground">{task.quality_score?.toFixed(1) || '0.0'}</span>
                        </p>
                      </div>
                    </div>
                    <StatusBadge value={task.status} label={getTaskStatusText(task.status)} />
                  </Link>
                ))
              ) : (
                <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                  <Activity className="w-10 h-10 mb-3 opacity-40" />
                  <p className="text-base font-medium">暂无任务</p>
                </div>
              )}
            </div>
          </SectionPanel>
        </div>

        <div className="xl:col-span-1 space-y-4">
          <SectionPanel title="快速操作">
            <div className="space-y-2">
              <Link to="/projects" className="block">
                <Button className="w-full justify-start h-10">
                  <Bot className="w-4 h-4 mr-2" />
                  Agent 智能审计
                </Button>
              </Link>
              <Link to="/instant-analysis" className="block">
                <Button variant="outline" className="w-full justify-start h-10">
                  <Zap className="w-4 h-4 mr-2" />
                  即时代码分析
                </Button>
              </Link>
              <Link to="/projects" className="block">
                <Button variant="outline" className="w-full justify-start h-10">
                  <GitBranch className="w-4 h-4 mr-2" />
                  创建新项目
                </Button>
              </Link>
              <Link to="/audit-tasks" className="block">
                <Button variant="outline" className="w-full justify-start h-10">
                  <Shield className="w-4 h-4 mr-2" />
                  启动审计任务
                </Button>
              </Link>
            </div>
          </SectionPanel>

          <SectionPanel title="规则与模板" className="border-t border-border pt-4 mt-4">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">数据库模式</span>
                <Badge variant="outline" className="rounded-full px-2.5 py-1 text-xs font-medium">
                  {dbMode === 'api' ? '后端 API' : dbMode === 'local' ? '本地' : dbMode === 'supabase' ? 'Supabase' : '演示'}
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">活跃项目</span>
                <span className="text-sm font-semibold text-foreground">{stats?.active_projects || 0}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">运行中任务</span>
                <span className="text-sm font-semibold text-blue-600">
                  {/* #6 修复：改取 stats 聚合字段（后端已聚合 agent_tasks），
                      原 recentTasks 只含传统 /tasks/ 维度，agent 任务运行中时显示 0 */}
                  {stats?.running_tasks || 0}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">待解决问题</span>
                <span className="text-sm font-semibold text-amber-600">
                  {stats ? stats.total_issues - stats.resolved_issues : 0}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground flex items-center gap-1">
                  <Shield className="w-4 h-4" />
                  审计规则
                </span>
                <span className="text-sm font-semibold text-violet-600">
                  {ruleStats.enabled}/{ruleStats.total}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground flex items-center gap-1">
                  <MessageSquare className="w-4 h-4" />
                  提示词模板
                </span>
                <span className="text-sm font-semibold text-emerald-600">
                  {templateStats.active}/{templateStats.total}
                </span>
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="最新活动" className="border-t border-border pt-4 mt-4">
            <div className="space-y-2">
              {recentTasks.length > 0 ? (
                recentTasks.slice(0, 3).map((task) => {
                  const timeAgo = (() => {
                    const now = new Date();
                    const taskDate = new Date(task.created_at);
                    const diffMs = now.getTime() - taskDate.getTime();
                    const diffMins = Math.floor(diffMs / 60000);
                    const diffHours = Math.floor(diffMs / 3600000);
                    const diffDays = Math.floor(diffMs / 86400000);

                    if (diffMins < 60) return `${diffMins}分钟前`;
                    if (diffHours < 24) return `${diffHours}小时前`;
                    return `${diffDays}天前`;
                  })();

                  const statusText =
                    task.status === 'completed' ? '任务完成' :
                    task.status === 'running' ? '任务运行中' :
                    task.status === 'failed' ? '任务失败' : '任务待处理';

                  return (
                    <Link
                      key={task.id}
                      to={`/tasks/${task.id}`}
                      className={`block rounded-lg border p-3 transition-all ${
                        task.status === 'completed' ? 'border-emerald-100 bg-emerald-50/50 hover:border-emerald-200' :
                        task.status === 'running' ? 'border-blue-100 bg-blue-50/50 hover:border-blue-200' :
                        task.status === 'failed' ? 'border-red-100 bg-red-50/50 hover:border-red-200' :
                        'border-border bg-muted/30 hover:bg-accent'
                      }`}
                    >
                      <p className="text-sm font-medium text-foreground">{statusText}</p>
                      <p className="text-sm text-muted-foreground mt-1 line-clamp-1">
                        项目 "{task.project?.name || '未知项目'}"
                        {task.status === 'completed' && task.issues_count > 0 &&
                          ` - 发现 ${task.issues_count} 个问题`
                        }
                      </p>
                      <p className="text-xs text-muted-foreground/70 mt-1">{timeAgo}</p>
                    </Link>
                  );
                })
              ) : (
                <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
                  <Clock className="w-10 h-10 mb-2 opacity-40" />
                  <p className="text-sm">暂无活动记录</p>
                </div>
              )}
            </div>
          </SectionPanel>
        </div>
      </div>
    </div>
  );
}
