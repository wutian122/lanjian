import Dashboard from "@/pages/Dashboard";
import Projects from "@/pages/Projects";
import ProjectDetail from "@/pages/ProjectDetail";
import RecycleBin from "@/pages/RecycleBin";
import InstantAnalysis from "@/pages/InstantAnalysis";
import AuditTasks from "@/pages/AuditTasks";
import TaskDetail from "@/pages/TaskDetail";
import AgentAudit from "@/pages/AgentAudit";
import AIPage from "@/pages/AI";
import AdminDashboard from "@/pages/AdminDashboard";
import Account from "@/pages/Account";
import AuditRules from "@/pages/AuditRules";
import PromptManager from "@/pages/PromptManager";
import type { ReactNode } from 'react';

export interface RouteConfig {
  name: string;
  path: string;
  element: ReactNode;
  visible?: boolean;
  description?: string;
}

const routes: RouteConfig[] = [
  {
    name: "仪表盘",
    path: "/dashboard",
    element: <Dashboard />,
    visible: true,
    description: "查看项目安全态势、审计趋势和最近任务",
  },
  {
    name: "AI 审计助手",
    path: "/ai",
    element: <AIPage />,
    visible: true,
    description: "全局 AI 审计控制中心",
  },
  {
    name: "项目管理",
    path: "/projects",
    element: <Projects />,
    visible: true,
    description: "导入、管理和审计需要进行代码安全分析的项目",
  },
  {
    name: "项目详情",
    path: "/projects/:id",
    element: <ProjectDetail />,
    visible: false,
  },
  {
    name: "即时分析",
    path: "/instant-analysis",
    element: <InstantAnalysis />,
    visible: true,
    description: "提交代码片段或文件，快速获取安全风险分析",
  },
  {
    name: "审计任务",
    path: "/audit-tasks",
    element: <AuditTasks />,
    visible: true,
    description: "查看和管理所有代码安全审计任务",
  },
  {
    name: "任务详情",
    path: "/tasks/:id",
    element: <TaskDetail />,
    visible: false,
  },
  {
    name: "Agent审计任务",
    path: "/agent-audit/:taskId",
    element: <AgentAudit />,
    visible: false, // 隐藏路由，通过项目创建审计任务后跳转
  },
  {
    name: "审计规则",
    path: "/audit-rules",
    element: <AuditRules />,
    visible: true,
    description: "管理安全规则集和启用状态",
  },
  {
    name: "提示词管理",
    path: "/prompts",
    element: <PromptManager />,
    visible: true,
    description: "维护 Agent 提示词模板和版本",
  },
  {
    name: "系统管理",
    path: "/admin",
    element: <AdminDashboard />,
    visible: true,
    description: "配置模型、数据库和平台运行参数",
  },
  {
    name: "回收站",
    path: "/recycle-bin",
    element: <RecycleBin />,
    visible: true,
    description: "恢复或永久删除已移除资源",
  },
  {
    name: "账号管理",
    path: "/account",
    element: <Account />,
    visible: false, // 不在主导航显示，在侧边栏底部单独显示
  },
];

export default routes;
