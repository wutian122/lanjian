import { Activity, AlertTriangle, CheckCircle, Code } from "lucide-react";
import { MetricCard } from "@/components/ui/metric-card";

export type ProjectCombinedStats = {
  totalTasks: number;
  completedTasks: number;
  totalIssues: number;
  avgQualityScore: number;
};

export function ProjectStatsCards(props: { stats: ProjectCombinedStats }) {
  const { stats } = props;
  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-4 relative z-10">
      <MetricCard label="审计任务" value={stats.totalTasks} icon={<Activity className="w-5 h-5" />} tone="blue" />
      <MetricCard label="已完成" value={stats.completedTasks} icon={<CheckCircle className="w-5 h-5" />} tone="green" />
      <MetricCard label="发现问题" value={stats.totalIssues} icon={<AlertTriangle className="w-5 h-5" />} tone="amber" />
      <MetricCard label="平均质量分" value={stats.avgQualityScore.toFixed(1)} icon={<Code className="w-5 h-5" />} tone="default" />
    </div>
  );
}


