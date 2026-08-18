/**
 * Database Manager Component
 * Enterprise Blue-White UI
 */

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Download,
  Upload,
  Trash2,
  AlertCircle,
  CheckCircle2,
  RefreshCw,
  AlertTriangle,
  Info,
} from 'lucide-react';
import { dbMode } from '@/shared/config/database';
import { api } from '@/shared/api/database';
import { toast } from 'sonner';
import { SectionPanel } from '@/components/ui/section-panel';

type Message = { type: 'success' | 'error'; text: string };
type Health = {
  status: 'healthy' | 'warning' | 'error';
  database_connected: boolean;
  total_records: number;
  last_backup_date: string | null;
  issues: string[];
  warnings: string[];
};
type Stats = {
  total_projects: number;
  active_projects: number;
  total_tasks: number;
  completed_tasks: number;
  pending_tasks: number;
  running_tasks: number;
  failed_tasks: number;
  total_issues: number;
  open_issues: number;
  resolved_issues: number;
  critical_issues: number;
  high_issues: number;
  medium_issues: number;
  low_issues: number;
  total_analyses: number;
  total_members: number;
  has_config: boolean;
};

function buildSummary(counts: Record<string, number>, labels: Record<string, string>) {
  const parts = Object.entries(labels)
    .map(([key, label]) => {
      const value = counts[key] ?? 0;
      return value > 0 ? `${value} ${label}` : null;
    })
    .filter(Boolean);

  return parts.length > 0 ? parts.join('、') : '无数据变化';
}

export function DatabaseManager() {
  const [loading, setLoading] = useState(false);
  const [healthLoading, setHealthLoading] = useState(false);
  const [statsLoading, setStatsLoading] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    loadHealth();
    loadStats();
  }, []);

  const loadHealth = async () => {
    try {
      setHealthLoading(true);
      const healthData = await api.checkDatabaseHealth();
      setHealth(healthData);
    } catch (error) {
      console.error('数据库健康检查失败:', error);
    } finally {
      setHealthLoading(false);
    }
  };

  const loadStats = async () => {
    try {
      setStatsLoading(true);
      const statsData = await api.getDatabaseStats();
      setStats(statsData);
    } catch (error) {
      console.error('数据库统计加载失败:', error);
    } finally {
      setStatsLoading(false);
    }
  };

  const handleExport = async () => {
    try {
      setLoading(true);
      setMessage(null);
      const exportData = await api.exportDatabase();
      const fullData = {
        version: '1.0.0',
        export_date: exportData.export_date,
        data: exportData.data,
      };
      const blob = new Blob([JSON.stringify(fullData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `lanjian-backup-${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success('数据导出成功');
      setMessage({ type: 'success', text: '数据导出成功' });
      loadStats();
    } catch (error: any) {
      const errorMsg = error?.response?.data?.detail || error?.message || '数据导出失败，请稍后重试';
      toast.error(errorMsg);
      setMessage({ type: 'error', text: errorMsg });
    } finally {
      setLoading(false);
    }
  };

  const handleImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.json')) {
      toast.error('请选择 JSON 格式的备份文件');
      event.target.value = '';
      return;
    }

    if (file.size > 50 * 1024 * 1024) {
      toast.error('备份文件不能超过 50MB');
      event.target.value = '';
      return;
    }

    try {
      setLoading(true);
      setMessage(null);
      const result = await api.importDatabase(file);
      const summary = buildSummary(result.imported, {
        projects: '个项目',
        tasks: '个任务',
        issues: '个问题',
        analyses: '条分析记录',
        config: '项配置',
      });
      const successText = `数据导入成功：${summary}`;
      toast.success(successText);
      setMessage({ type: 'success', text: successText });
      event.target.value = '';
      loadStats();
      loadHealth();
      setTimeout(() => window.location.reload(), 2000);
    } catch (error: any) {
      const errorMsg = error?.response?.data?.detail || error?.message || '数据导入失败，请检查文件后重试';
      toast.error(errorMsg);
      setMessage({ type: 'error', text: errorMsg });
      event.target.value = '';
    } finally {
      setLoading(false);
    }
  };

  const handleClear = async () => {
    const firstConfirm = window.confirm(
      '此操作将清空项目、任务、问题、分析记录和配置数据。建议先导出备份。是否继续？'
    );
    if (!firstConfirm) return;

    const secondConfirm = window.confirm('请再次确认：清空数据库后无法从页面恢复。确定继续？');
    if (!secondConfirm) return;

    try {
      setLoading(true);
      setMessage(null);
      const result = await api.clearDatabase();
      const summary = buildSummary(result.deleted, {
        projects: '个项目',
        tasks: '个任务',
        issues: '个问题',
        analyses: '条分析记录',
        config: '项配置',
      });
      const successText = `数据库清空完成：${summary}`;
      toast.success(successText);
      setMessage({ type: 'success', text: successText });
      loadStats();
      loadHealth();
      setTimeout(() => window.location.reload(), 2000);
    } catch (error: any) {
      const errorMsg = error?.response?.data?.detail || error?.message || '清空数据库失败，请稍后重试';
      toast.error(errorMsg);
      setMessage({ type: 'error', text: errorMsg });
    } finally {
      setLoading(false);
    }
  };

  const getHealthStatusBadge = (status: string) => {
    switch (status) {
      case 'healthy':
        return <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">健康</Badge>;
      case 'warning':
        return <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">警告</Badge>;
      case 'error':
        return <Badge variant="outline" className="border-red-200 bg-red-50 text-red-700">异常</Badge>;
      default:
        return <Badge variant="outline">未知</Badge>;
    }
  };

  return (
    <div className="space-y-6">
      <SectionPanel
        title="数据库健康检查"
        actions={
          <Button variant="outline" size="sm" onClick={loadHealth} disabled={healthLoading} className="h-8">
            <RefreshCw className={`h-3 w-3 mr-2 ${healthLoading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        }
      >
        {healthLoading ? (
          <div className="flex items-center justify-center py-8">
            <div className="loading-spinner" />
          </div>
        ) : health ? (
          <div className="space-y-4">
            <div className="flex items-center gap-4 flex-wrap">
              {health.status === 'healthy' ? (
                <CheckCircle2 className="h-5 w-5 text-emerald-600" />
              ) : health.status === 'warning' ? (
                <AlertTriangle className="h-5 w-5 text-amber-600" />
              ) : (
                <AlertCircle className="h-5 w-5 text-red-600" />
              )}
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-foreground">运行状态</span>
                {getHealthStatusBadge(health.status)}
              </div>
              <span className="text-sm text-muted-foreground">
                数据库连接：
                <span className={health.database_connected ? 'text-emerald-600' : 'text-red-600'}>
                  {health.database_connected ? '正常' : '失败'}
                </span>
                <span className="mx-2">|</span>
                总记录数：<span className="text-foreground">{health.total_records.toLocaleString()}</span>
              </span>
            </div>

            {health.issues.length > 0 && (
              <div className="rounded-lg border border-red-200 bg-red-50 p-4">
                <p className="font-semibold text-red-700 text-sm mb-2 flex items-center gap-2">
                  <AlertCircle className="w-4 h-4" />
                  发现问题
                </p>
                <ul className="list-disc list-inside space-y-1 text-sm text-red-600/80">
                  {health.issues.map((issue, index) => (
                    <li key={index}>{issue}</li>
                  ))}
                </ul>
              </div>
            )}

            {health.warnings.length > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                <p className="font-semibold text-amber-700 text-sm mb-2 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4" />
                  注意事项
                </p>
                <ul className="list-disc list-inside space-y-1 text-sm text-amber-600/80">
                  {health.warnings.map((warning, index) => (
                    <li key={index}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 flex items-start gap-3">
            <Info className="h-5 w-5 text-blue-600 mt-0.5" />
            <p className="text-sm text-blue-700/80">暂未获取到数据库健康状态。</p>
          </div>
        )}
      </SectionPanel>

      <SectionPanel
        title="数据库统计"
        actions={
          <Button variant="outline" size="sm" onClick={loadStats} disabled={statsLoading} className="h-8">
            <RefreshCw className={`h-3 w-3 mr-2 ${statsLoading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        }
      >
        {statsLoading ? (
          <div className="flex items-center justify-center py-8">
            <div className="loading-spinner" />
          </div>
        ) : stats ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="rounded-xl border border-border bg-muted/30 p-4">
              <p className="text-xs text-muted-foreground">项目总数</p>
              <p className="text-2xl font-semibold text-foreground">{stats.total_projects}</p>
              <p className="text-xs text-emerald-600 mt-1">活跃 {stats.active_projects}</p>
            </div>
            <div className="rounded-xl border border-border bg-muted/30 p-4">
              <p className="text-xs text-muted-foreground">审计任务</p>
              <p className="text-2xl font-semibold text-emerald-600">{stats.total_tasks}</p>
              <p className="text-xs text-muted-foreground mt-1">完成 {stats.completed_tasks} | 运行 {stats.running_tasks}</p>
            </div>
            <div className="rounded-xl border border-border bg-muted/30 p-4">
              <p className="text-xs text-muted-foreground">安全问题</p>
              <p className="text-2xl font-semibold text-amber-600">{stats.total_issues}</p>
              <p className="text-xs text-muted-foreground mt-1">开放 {stats.open_issues} | 已解决 {stats.resolved_issues}</p>
            </div>
            <div className="rounded-xl border border-border bg-muted/30 p-4">
              <p className="text-xs text-muted-foreground">即时分析</p>
              <p className="text-2xl font-semibold text-violet-600">{stats.total_analyses}</p>
              <p className="text-xs text-muted-foreground mt-1">成员 {stats.total_members}</p>
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 flex items-start gap-3">
            <Info className="h-5 w-5 text-blue-600 mt-0.5" />
            <p className="text-sm text-blue-700/80">暂未获取到数据库统计。</p>
          </div>
        )}
      </SectionPanel>

      <SectionPanel title="数据操作">
        <div className="space-y-6">
          {message && (
            <div className={`p-4 flex items-start gap-3 rounded-lg ${
              message.type === 'success'
                ? 'border border-emerald-200 bg-emerald-50'
                : 'border border-red-200 bg-red-50'
            }`}>
              {message.type === 'success' ? (
                <CheckCircle2 className="h-5 w-5 text-emerald-600 mt-0.5" />
              ) : (
                <AlertCircle className="h-5 w-5 text-red-600 mt-0.5" />
              )}
              <p className={`text-sm ${message.type === 'success' ? 'text-emerald-700' : 'text-red-700'}`}>
                {message.text}
              </p>
            </div>
          )}

          <div className="grid gap-6 md:grid-cols-3">
            <div className="space-y-3">
              <h4 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Download className="h-4 w-4 text-sky-600" />
                导出备份
              </h4>
              <p className="text-xs text-muted-foreground">导出当前数据库数据为 JSON 文件，文件名使用 lanjian 前缀。</p>
              <Button onClick={handleExport} disabled={loading} variant="outline" className="w-full h-10">
                <Download className="mr-2 h-4 w-4" />
                导出数据
              </Button>
            </div>

            <div className="space-y-3">
              <h4 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Upload className="h-4 w-4 text-emerald-600" />
                导入备份
              </h4>
              <p className="text-xs text-muted-foreground">导入 JSON 备份文件，最大支持 50MB。</p>
              <Button onClick={() => document.getElementById('import-file')?.click()} disabled={loading} variant="outline" className="w-full h-10">
                <Upload className="mr-2 h-4 w-4" />
                导入数据
              </Button>
              <input id="import-file" type="file" accept=".json" onChange={handleImport} className="hidden" />
            </div>

            <div className="space-y-3">
              <h4 className="text-sm font-semibold text-red-600 flex items-center gap-2">
                <Trash2 className="h-4 w-4" />
                清空数据
              </h4>
              <p className="text-xs text-muted-foreground">危险操作：清空业务数据前请先导出备份。</p>
              <Button onClick={handleClear} disabled={loading} className="w-full bg-red-600 hover:bg-red-700 text-white h-10">
                <Trash2 className="mr-2 h-4 w-4" />
                清空数据库
              </Button>
            </div>
          </div>

          <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-red-900">
            <h3 className="text-base font-semibold">操作提醒</h3>
            <p className="text-sm text-red-700 mt-1">导入和清空会影响业务数据，请先确认备份文件可用。</p>
          </div>

          <div className="pt-6 border-t border-border">
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 flex items-start gap-3">
              <Info className="h-5 w-5 text-blue-600 mt-0.5 flex-shrink-0" />
              <p className="text-sm text-blue-700/80">
                <strong className="text-blue-600">当前模式：</strong>
                {dbMode === 'api'
                  ? '后端 API + PostgreSQL 数据库模式。'
                  : '本地模拟数据库模式，仅用于开发调试。'}
              </p>
            </div>
          </div>
        </div>
      </SectionPanel>
    </div>
  );
}